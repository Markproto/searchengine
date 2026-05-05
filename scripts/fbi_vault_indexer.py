#!/usr/bin/env python3
"""
FBI Vault indexer.

The Vault (vault.fbi.gov) is a Plone-driven archive of declassified case
files. Each URL in its sitemap.xml.gz IS the PDF directly — no scraping
of intermediate pages required.

This script:
  1. Downloads + parses sitemap.xml.gz to get every case URL
  2. For each URL: fetches the PDF, extracts text via PyPDF2, builds
     metadata from the URL path (case slug + sub-case + part-number)
  3. Bulk-indexes into Elasticsearch under `profoundd_archive_docs`
     with collection="fbi-vault"

Resumable via SQLite state DB. Polite rate: 1 req/sec.

Usage:
    python scripts/fbi_vault_indexer.py \\
        --es-url http://127.0.0.1:9201 \\
        --state-dir /home/mark/fbi-vault-state \\
        --rate 1.0
"""
import argparse
import gzip
import hashlib
import io
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fbi_vault_indexer")

ES_INDEX = "profoundd_archive_docs"
COLLECTION = "fbi-vault"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
SITEMAP_URL = "https://vault.fbi.gov/sitemap.xml.gz"
MAX_TEXT_CHARS = 200_000


def init_state(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "fbi_vault_state.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        url TEXT PRIMARY KEY,
        status TEXT,
        indexed_at TEXT,
        page_count INTEGER,
        text_chars INTEGER,
        error TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON items(status)")
    conn.commit()
    return conn


def fetch_sitemap_urls():
    """Return the full list of vault URLs from sitemap.xml.gz."""
    logger.info("Fetching sitemap %s", SITEMAP_URL)
    r = requests.get(SITEMAP_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    xml = gzip.decompress(r.content).decode("utf-8", errors="ignore")
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    # Skip non-vault items (e.g. an embedded fbijobs link if present)
    return [u for u in locs if u.startswith("https://vault.fbi.gov/")]


def parse_url_metadata(url):
    """Extract case + part metadata from the URL path.

    Example URL:
      https://vault.fbi.gov/cointel-pro/new-left/COINTELPRO%20New%20Left%20Atlanta%20Part%2001%20(Final)
    Yields:
      case = "cointel-pro"
      sub_case = "new-left"
      title = "COINTELPRO New Left Atlanta Part 01 (Final)"
      part_number = 1
    """
    p = urlparse(url)
    parts = [unquote(s) for s in p.path.strip("/").split("/") if s]
    case = parts[0] if parts else ""
    sub_case = parts[1] if len(parts) > 1 else ""
    title = parts[-1] if parts else ""
    part_number = None
    m = re.search(r"\bPart\s+(\d+)", title, re.IGNORECASE)
    if m:
        try:
            part_number = int(m.group(1))
        except ValueError:
            pass
    return {
        "case": case,
        "sub_case": sub_case,
        "title": title,
        "part_number": part_number,
    }


def extract_pdf_text(pdf_bytes, max_chars=MAX_TEXT_CHARS):
    """Best-effort PDF → plain text. Tries PyPDF2 first, then pypdf."""
    try:
        from PyPDF2 import PdfReader
    except Exception:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as e:
            logger.warning("No PDF library available: %s", e)
            return "", 0

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        page_count = len(reader.pages)
        chunks = []
        chars = 0
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t:
                chunks.append(t)
                chars += len(t)
                if chars >= max_chars:
                    break
        text = "\n".join(chunks).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…[truncated]"
        return text, page_count
    except Exception as e:
        logger.debug("PDF parse failed: %s", e)
        return "", 0


def fetch_and_extract(url, rate=1.0):
    """Returns (text, page_count, content_type, byte_len) or (None, ...) on fail."""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=90)
        if r.status_code != 200:
            return None, 0, r.headers.get("Content-Type", ""), 0
        ctype = r.headers.get("Content-Type", "")
        if "pdf" not in ctype.lower():
            # Not all vault URLs are PDFs (some are folder index pages)
            return "", 0, ctype, len(r.content)
        text, pages = extract_pdf_text(r.content)
        return text, pages, ctype, len(r.content)
    except Exception as e:
        logger.warning("fetch %s: %s", url, e)
        return None, 0, "", 0


def build_es_doc(url, text, page_count):
    meta = parse_url_metadata(url)
    title = meta["title"] or "FBI Vault document"
    summary = (text[:500].replace("\n", " ") if text else "").strip()
    if len(text) > 500:
        summary = summary.rsplit(" ", 1)[0] + "…"

    # doc_id is collection-prefixed so multiple collections share one index
    raw_id = f"{COLLECTION}::{url}"
    doc_id = hashlib.md5(raw_id.encode()).hexdigest()

    return {
        "doc_id": doc_id,
        "collection": COLLECTION,
        "title": title[:400],
        "summary": summary,
        "content": text,
        "case": meta["case"],
        "sub_case": meta["sub_case"],
        "part_number": meta["part_number"],
        "page_count": page_count,
        "pdf_url": url,
        "source_url": url,
        "tags": [t for t in [meta["case"], meta["sub_case"]] if t],
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


# Mapping is created via ensure_archive_index() in this script — same shape
# is also created from engine.py at app startup.
ARCHIVE_MAPPING = {
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "collection": {"type": "keyword"},
            "title": {
                "type": "text", "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "summary": {"type": "text", "analyzer": "english"},
            "content": {"type": "text", "analyzer": "english"},
            "case": {"type": "keyword"},
            "sub_case": {"type": "keyword"},
            "part_number": {"type": "integer"},
            "page_count": {"type": "integer"},
            "pdf_url": {"type": "keyword"},
            "source_url": {"type": "keyword"},
            "snapshot_date": {
                "type": "date",
                "format": "yyyy-MM-dd||yyyyMMdd||yyyy-MM-dd'T'HH:mm:ss||epoch_millis",
                "ignore_malformed": True,
            },
            "doc_date": {
                "type": "date",
                "format": "yyyy-MM-dd||yyyy-MM||yyyy||epoch_millis",
                "ignore_malformed": True,
            },
            "tags": {"type": "keyword"},
            "indexed_at": {"type": "date"},
            "category": {"type": "keyword"},
            "result_type": {"type": "keyword"},
        }
    },
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
}


def ensure_archive_index(es_url):
    try:
        r = requests.head(f"{es_url}/{ES_INDEX}", timeout=10)
        if r.status_code == 404:
            r = requests.put(
                f"{es_url}/{ES_INDEX}",
                json=ARCHIVE_MAPPING,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            logger.info("Created index %s", ES_INDEX)
        else:
            logger.info("Index %s already exists", ES_INDEX)
    except Exception as e:
        logger.error("ensure index: %s", e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-url", default="http://127.0.0.1:9201")
    parser.add_argument("--state-dir", default="/home/mark/fbi-vault-state")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--filter-prefix", default=None,
                        help="Only ingest URLs starting with /<prefix>")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    conn = init_state(state_dir)
    ensure_archive_index(args.es_url)

    urls = fetch_sitemap_urls()
    logger.info("sitemap returned %d URLs", len(urls))
    if args.filter_prefix:
        urls = [u for u in urls
                if urlparse(u).path.lstrip("/").startswith(args.filter_prefix)]
        logger.info("after filter: %d URLs", len(urls))

    # Sort URLs by path depth descending so leaf PDF URLs are tried before
    # case folder index pages (which return text/html and waste a request).
    # Within the same depth, longer URLs first as a tiebreaker (more specific).
    def _depth_key(u):
        p = urlparse(u).path
        return (-p.count("/"), -len(p))
    urls.sort(key=_depth_key)

    indexed = skipped = errors = nopdf = 0
    batch = []
    start = time.time()

    for url in urls:
        row = conn.execute("SELECT status FROM items WHERE url=?", (url,)).fetchone()
        if row and row[0] == "indexed":
            skipped += 1
            continue

        time.sleep(args.rate)
        text, pages, ctype, blen = fetch_and_extract(url, rate=args.rate)
        if text is None:
            errors += 1
            conn.execute(
                "INSERT OR REPLACE INTO items (url,status,error,indexed_at) VALUES (?,?,?,?)",
                (url, "error", "fetch_failed", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            continue
        if "pdf" not in ctype.lower():
            nopdf += 1
            conn.execute(
                "INSERT OR REPLACE INTO items (url,status,error,indexed_at) VALUES (?,?,?,?)",
                (url, "not_pdf", ctype[:100], datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            continue

        doc = build_es_doc(url, text, pages)
        batch.append(doc)
        conn.execute(
            "INSERT OR REPLACE INTO items "
            "(url,status,indexed_at,page_count,text_chars,error) VALUES (?,?,?,?,?,?)",
            (url, "queued", datetime.now(timezone.utc).isoformat(),
             pages, len(text), None),
        )
        conn.commit()

        if len(batch) >= args.batch_size:
            n = bulk_index(args.es_url, batch)
            indexed += n
            for d in batch:
                conn.execute("UPDATE items SET status='indexed' WHERE url=?",
                             (d["pdf_url"],))
            conn.commit()
            batch = []
            elapsed = time.time() - start
            logger.info("Progress: indexed=%d skipped=%d nopdf=%d errors=%d elapsed=%.0fs",
                        indexed, skipped, nopdf, errors, elapsed)
        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        n = bulk_index(args.es_url, batch)
        indexed += n
        for d in batch:
            conn.execute("UPDATE items SET status='indexed' WHERE url=?",
                         (d["pdf_url"],))
        conn.commit()

    logger.info("Done. indexed=%d skipped=%d nopdf=%d errors=%d elapsed=%.0fs",
                indexed, skipped, nopdf, errors, time.time() - start)
    conn.close()


if __name__ == "__main__":
    main()
