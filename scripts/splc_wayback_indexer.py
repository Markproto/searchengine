#!/usr/bin/env python3
"""
SPLC (Southern Poverty Law Center) Wayback indexer.

Fetches archived snapshots of splcenter.org from the Internet Archive
and indexes the extracted article text into Elasticsearch under
`profoundd_archive_docs` with collection="splc".

Strategy:
  1. For each prefix path (intelligence-report, hate-map, hatewatch, etc.),
     query Wayback CDX for unique URLs (deduped by `urlkey`)
  2. For each unique URL, take the most recent successful 200 snapshot
  3. Fetch via web.archive.org/web/<timestamp>id_/<url> (the `id_` flag
     gives raw HTML without Wayback's framing)
  4. Extract main article text via BeautifulSoup
  5. Bulk-index

CDX API ref: https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server
We dedupe with `&collapse=urlkey` so each URL is fetched only once even
if it has 100 snapshots. Snapshot-date is preserved on the doc.

Usage:
    python scripts/splc_wayback_indexer.py \\
        --es-url http://127.0.0.1:9201 \\
        --state-dir /home/mark/splc-state \\
        --rate 1.0
"""
import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("splc_wayback")

ES_INDEX = "profoundd_archive_docs"
COLLECTION = "splc"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
MAX_TEXT_CHARS = 60_000

# Path prefixes worth recovering (priority order).
# Wildcards via &matchType=prefix
SPLC_PREFIXES = [
    "splcenter.org/fighting-hate",
    "splcenter.org/intelligence-report",
    "splcenter.org/hate-map",
    "splcenter.org/year-in-hate",
    "splcenter.org/hatewatch",
    "splcenter.org/our-issues",
    "splcenter.org/news",
]


def init_state(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "splc_state.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        url TEXT PRIMARY KEY,
        snapshot_ts TEXT,
        status TEXT,
        indexed_at TEXT,
        text_chars INTEGER,
        error TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON items(status)")
    conn.commit()
    return conn


def cdx_search(prefix, timeout=120):
    """Yield (timestamp, original_url) tuples for unique snapshots of a prefix."""
    url = (
        "http://web.archive.org/cdx/search/cdx"
        f"?url={prefix}*"
        "&output=json"
        "&filter=mimetype:text/html"
        "&filter=statuscode:200"
        "&collapse=urlkey"
        "&fl=timestamp,original"
    )
    logger.info("CDX %s", prefix)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("CDX failed for %s: %s", prefix, e)
        return
    if not data:
        return
    # First row is the header
    for row in data[1:]:
        ts, orig = row[0], row[1]
        yield ts, orig


def wayback_url(timestamp, original):
    """Return the raw-HTML wayback URL (id_ flag suppresses framing)."""
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def fetch_text(timestamp, original_url, rate=1.0):
    """Fetch a single snapshot, extract readable text. Returns (title, text)."""
    url = wayback_url(timestamp, original_url)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=45)
        if r.status_code != 200:
            return None, None
    except Exception as e:
        logger.warning("fetch %s: %s", url, e)
        return None, None

    soup = BeautifulSoup(r.text, "html.parser")
    # Strip junk
    for tag in soup(["script", "style", "nav", "footer", "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    main = (soup.find("article") or soup.find("main")
            or soup.find("div", class_=lambda c: c and "content" in c.lower())
            or soup.body)
    text = ""
    if main:
        paras = [p.get_text(" ", strip=True) for p in main.find_all("p")
                 if p.get_text(strip=True)]
        text = "\n".join(paras)[:MAX_TEXT_CHARS]
        if len(text) < 200:
            # Fall back to whole body text
            text = main.get_text("\n", strip=True)[:MAX_TEXT_CHARS]
    return title[:300], text.strip()


def build_doc(original_url, title, text, snapshot_ts):
    raw_id = f"{COLLECTION}::{original_url}"
    doc_id = hashlib.md5(raw_id.encode()).hexdigest()
    summary = (text[:500].replace("\n", " ").strip() if text else "")
    if len(text) > 500:
        summary = summary.rsplit(" ", 1)[0] + "…"
    snap_iso = ""
    if snapshot_ts and len(snapshot_ts) >= 8:
        try:
            snap_iso = (
                f"{snapshot_ts[:4]}-{snapshot_ts[4:6]}-{snapshot_ts[6:8]}"
            )
        except Exception:
            snap_iso = ""
    return {
        "doc_id": doc_id,
        "collection": COLLECTION,
        "title": title or original_url,
        "summary": summary,
        "content": text or "",
        "case": "",
        "sub_case": "",
        "page_count": 0,
        "pdf_url": "",
        "source_url": original_url,
        "snapshot_date": snap_iso,
        "tags": ["splc", "wayback"],
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "archive",
        "result_type": "document",
    }


def bulk_index(es_url, docs):
    if not docs:
        return 0
    payload = []
    for doc in docs:
        payload.append(json.dumps(
            {"index": {"_index": ES_INDEX, "_id": doc["doc_id"]}}
        ))
        payload.append(json.dumps(doc))
    body = "\n".join(payload) + "\n"
    try:
        resp = requests.post(
            f"{es_url}/_bulk",
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=90,
        )
        if resp.status_code >= 400:
            logger.error("ES bulk %s: %s", resp.status_code, resp.text[:300])
            return 0
        return sum(
            1 for it in resp.json().get("items", [])
            if it.get("index", {}).get("status", 500) in (200, 201)
        )
    except Exception as e:
        logger.error("ES bulk failed: %s", e)
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-url", default="http://127.0.0.1:9201")
    parser.add_argument("--state-dir", default="/home/mark/splc-state")
    parser.add_argument("--rate", type=float, default=1.0,
                        help="Sleep between Wayback fetches (default 1s)")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--prefixes", default=None,
                        help="Comma-separated SPLC path prefixes; default = built-in list")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    conn = init_state(state_dir)

    prefixes = args.prefixes.split(",") if args.prefixes else SPLC_PREFIXES
    indexed = skipped = errors = empty = 0
    batch = []
    start = time.time()

    for prefix in prefixes:
        for ts, orig in cdx_search(prefix.strip()):
            if not orig.startswith("http"):
                orig = "https://" + orig
            row = conn.execute(
                "SELECT status FROM items WHERE url=?", (orig,)
            ).fetchone()
            if row and row[0] == "indexed":
                skipped += 1
                continue

            time.sleep(args.rate)
            title, text = fetch_text(ts, orig, rate=args.rate)
            if title is None and text is None:
                errors += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items "
                    "(url,snapshot_ts,status,error,indexed_at) VALUES (?,?,?,?,?)",
                    (orig, ts, "error", "fetch_failed",
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue
            if not text or len(text) < 100:
                empty += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items "
                    "(url,snapshot_ts,status,error,indexed_at) VALUES (?,?,?,?,?)",
                    (orig, ts, "empty", None,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue

            doc = build_doc(orig, title or "", text, ts)
            batch.append(doc)
            conn.execute(
                "INSERT OR REPLACE INTO items "
                "(url,snapshot_ts,status,indexed_at,text_chars,error) "
                "VALUES (?,?,?,?,?,?)",
                (orig, ts, "queued",
                 datetime.now(timezone.utc).isoformat(),
                 len(text), None),
            )
            conn.commit()

            if len(batch) >= args.batch_size:
                n = bulk_index(args.es_url, batch)
                indexed += n
                for d in batch:
                    conn.execute(
                        "UPDATE items SET status='indexed' WHERE url=?",
                        (d["source_url"],),
                    )
                conn.commit()
                batch = []
                elapsed = time.time() - start
                logger.info(
                    "Progress: indexed=%d skipped=%d empty=%d errors=%d elapsed=%.0fs",
                    indexed, skipped, empty, errors, elapsed,
                )
            if args.max_items and indexed >= args.max_items:
                break
        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        n = bulk_index(args.es_url, batch)
        indexed += n
        for d in batch:
            conn.execute(
                "UPDATE items SET status='indexed' WHERE url=?",
                (d["source_url"],),
            )
        conn.commit()

    logger.info(
        "Done. indexed=%d skipped=%d empty=%d errors=%d elapsed=%.0fs",
        indexed, skipped, empty, errors, time.time() - start,
    )
    conn.close()


if __name__ == "__main__":
    main()
