#!/usr/bin/env python3
"""
Federal Writers' Project (FWP) indexer.

Walks the Library of Congress collection API, downloads OCR'd full text
for each item's pages, and bulk-indexes the result into Elasticsearch
under `profoundd_fwp_docs`.

Phase 1 target: American Life Histories (~2,000 items).
Phase 2/3 (Slave Narratives, State Guides) reuse this script with
different `--collection-slug` / `--format-filter` args.

Resumable via SQLite state DB at <state-dir>/fwp_state.db.
Polite rate: 1 req/sec to LOC by default.

Usage:
    python scripts/fwp_indexer.py \\
        --es-url http://127.0.0.1:9201 \\
        --state-dir /home/mark/fwp-state \\
        --collection-slug federal-writers-project \\
        --format-filter "manuscript/mixed material" \\
        --rate 1.0
"""
import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("fwp_indexer")

ES_INDEX = "profoundd_fwp_docs"
USER_AGENT = "ProfounddBot/1.0 (+https://profoundd.com/bot)"

LOC_BASE = "https://www.loc.gov"


def init_state(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "fwp_state.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS items (
        item_id TEXT PRIMARY KEY,
        status TEXT,
        indexed_at TEXT,
        page_count INTEGER,
        text_chars INTEGER,
        error TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON items(status)")
    conn.commit()
    return conn


def loc_get(url, timeout=30, retries=3):
    """GET a LOC URL with retries. Returns parsed JSON or None."""
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=timeout,
            )
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning("LOC 429 rate-limited; sleeping %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            logger.error("LOC fetch failed (%s): %s", url, e)
            return None
        except json.JSONDecodeError as e:
            logger.error("LOC bad JSON (%s): %s", url, e)
            return None
    return None


def iter_collection(slug, format_filter=None, rate=1.0):
    """Yield item URLs from a LOC collection, paginated."""
    page = 1
    while True:
        params = ["fo=json", "c=25", f"sp={page}"]
        if format_filter:
            from urllib.parse import quote
            params.append(f"fa=original-format:{quote(format_filter)}")
        url = f"{LOC_BASE}/collections/{slug}/?" + "&".join(params)
        logger.info("Fetching collection page %d", page)
        data = loc_get(url)
        if not data:
            return
        results = data.get("results", []) or []
        if not results:
            return
        for r in results:
            yield r
        pag = data.get("pagination", {}) or {}
        if not pag.get("next"):
            return
        page += 1
        time.sleep(rate)


def extract_loc_id(item_url):
    """`http://www.loc.gov/item/wpalh000041/` -> `wpalh000041`."""
    u = item_url.rstrip("/")
    return u.rsplit("/", 1)[-1]


def fetch_item_full(item_url, rate=1.0):
    """Fetch the item JSON + concatenate per-page OCR full_text.
    Returns (item_dict, text_chars) or (None, 0) on failure."""
    if not item_url.endswith("/"):
        item_url += "/"
    json_url = item_url.replace("http://", "https://") + "?fo=json"
    data = loc_get(json_url)
    if not data:
        return None, 0

    item = data.get("item", {}) or {}
    resources = data.get("resources", []) or []

    # Walk every page in every resource, fetch fulltext_service if present
    full_pages = []
    page_count = 0
    for r in resources:
        files_per_page = r.get("files") or []
        for page_files in files_per_page:
            page_count += 1
            ft_url = None
            for f in page_files:
                if f.get("mimetype") == "text/plain" and f.get("fulltext_service"):
                    ft_url = f["fulltext_service"]
                    break
            if not ft_url:
                continue
            time.sleep(rate)
            try:
                resp = requests.get(
                    ft_url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=20,
                )
                if resp.status_code == 200:
                    j = resp.json()
                    # Endpoint returns {"<segment>": {"full_text": "..."}}
                    for v in j.values():
                        if isinstance(v, dict) and v.get("full_text"):
                            full_pages.append(v["full_text"])
                            break
            except Exception as e:
                logger.debug("page text err: %s", e)

    full_text = "\n\n".join(full_pages).strip()

    # Resolve top-level pdf URL (first resource that has one)
    pdf_url = ""
    for r in resources:
        if r.get("pdf"):
            pdf_url = r["pdf"]
            break

    return {
        "item": item,
        "resources": resources,
        "full_text": full_text,
        "page_count": page_count,
        "pdf_url": pdf_url,
    }, len(full_text)


def _flatten_listdict(items):
    """LOC returns lists of {label: url}; we just want the labels."""
    out = []
    for x in items or []:
        if isinstance(x, dict):
            out.extend(list(x.keys()))
        elif isinstance(x, str):
            out.append(x)
    return out


def build_es_doc(payload, loc_id):
    item = payload["item"]
    titles = item.get("title")
    if isinstance(titles, list):
        title = titles[0] if titles else ""
    else:
        title = titles or ""

    contributors = _flatten_listdict(item.get("contributors"))
    subjects = _flatten_listdict(item.get("subjects"))
    locations = _flatten_listdict(item.get("location") or item.get("locations"))
    dates = _flatten_listdict(item.get("dates"))
    doc_date = ""
    for d in dates:
        # Pull first ISO-shaped date
        d = d.strip()
        if len(d) >= 4 and d[:4].isdigit():
            doc_date = d[:10] if len(d) >= 10 else d[:4]
            break

    full_text = payload["full_text"]
    summary = full_text[:500].replace("\n", " ").strip()
    if len(full_text) > 500:
        summary = summary.rsplit(" ", 1)[0] + "…"

    return {
        "item_id": loc_id,
        "title": title.strip()[:500] or f"FWP item {loc_id}",
        "summary": summary,
        "content": full_text,
        "contributors": contributors[:20],
        "subjects": subjects[:30],
        "location": locations[:10],
        "doc_date": doc_date,
        "page_count": payload["page_count"],
        "pdf_url": payload["pdf_url"],
        "source_url": f"https://www.loc.gov/item/{loc_id}/",
        "collection": "federal-writers-project",
        "subcollection": "american-life-histories",
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "fwp",
        "result_type": "document",
    }


def bulk_index(es_url, docs):
    if not docs:
        return 0
    payload = []
    for doc in docs:
        doc_id = hashlib.md5(doc["item_id"].encode()).hexdigest()
        payload.append(json.dumps({"index": {"_index": ES_INDEX, "_id": doc_id}}))
        payload.append(json.dumps(doc))
    body = "\n".join(payload) + "\n"
    try:
        resp = requests.post(
            f"{es_url}/_bulk",
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.error("ES bulk error %s: %s", resp.status_code, resp.text[:300])
            return 0
        result = resp.json()
        return sum(
            1 for it in result.get("items", [])
            if it.get("index", {}).get("status", 500) in (200, 201)
        )
    except Exception as e:
        logger.error("ES bulk failed: %s", e)
        return 0


def ensure_es_index(es_url):
    """Create the FWP index if it doesn't exist (mapping mirrors Epstein)."""
    mapping = {
        "mappings": {
            "properties": {
                "item_id": {"type": "keyword"},
                "title": {
                    "type": "text", "analyzer": "english",
                    "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
                },
                "summary": {"type": "text", "analyzer": "english"},
                "content": {"type": "text", "analyzer": "english"},
                "contributors": {"type": "keyword"},
                "subjects": {"type": "keyword"},
                "location": {"type": "keyword"},
                "doc_date": {
                    "type": "date",
                    "format": "yyyy-MM-dd||yyyy-MM||yyyy||epoch_millis",
                    "ignore_malformed": True,
                },
                "page_count": {"type": "integer"},
                "pdf_url": {"type": "keyword"},
                "source_url": {"type": "keyword"},
                "collection": {"type": "keyword"},
                "subcollection": {"type": "keyword"},
                "indexed_at": {"type": "date"},
                "category": {"type": "keyword"},
                "result_type": {"type": "keyword"},
            }
        },
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    }
    try:
        r = requests.head(f"{es_url}/{ES_INDEX}", timeout=10)
        if r.status_code == 404:
            r = requests.put(
                f"{es_url}/{ES_INDEX}",
                json=mapping,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            r.raise_for_status()
            logger.info("Created index %s", ES_INDEX)
        else:
            logger.info("Index %s already exists", ES_INDEX)
    except Exception as e:
        logger.error("Could not ensure index: %s", e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-url", default="http://127.0.0.1:9201")
    parser.add_argument("--state-dir", default="/home/mark/fwp-state")
    parser.add_argument("--collection-slug", default="federal-writers-project")
    parser.add_argument("--format-filter", default="manuscript/mixed material")
    parser.add_argument("--rate", type=float, default=1.0,
                        help="Seconds between LOC requests (default 1.0)")
    parser.add_argument("--max-items", type=int, default=None,
                        help="Stop after indexing N items (for testing)")
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    conn = init_state(state_dir)

    ensure_es_index(args.es_url)

    indexed = 0
    skipped = 0
    errors = 0
    batch = []
    start = time.time()

    for it in iter_collection(args.collection_slug, args.format_filter, args.rate):
        item_url = it.get("id", "")
        if not item_url or "/item/" not in item_url:
            continue
        loc_id = extract_loc_id(item_url)

        # Skip already-indexed
        row = conn.execute(
            "SELECT status FROM items WHERE item_id=?", (loc_id,)
        ).fetchone()
        if row and row[0] == "indexed":
            skipped += 1
            continue

        time.sleep(args.rate)
        try:
            payload, n_chars = fetch_item_full(item_url, rate=args.rate)
        except Exception as e:
            payload, n_chars = None, 0
            logger.warning("fetch_item_full %s: %s", loc_id, e)

        if not payload:
            errors += 1
            conn.execute(
                "INSERT OR REPLACE INTO items (item_id,status,error,indexed_at) VALUES (?,?,?,?)",
                (loc_id, "error", "fetch_failed", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            continue

        doc = build_es_doc(payload, loc_id)
        batch.append(doc)
        conn.execute(
            "INSERT OR REPLACE INTO items "
            "(item_id,status,indexed_at,page_count,text_chars,error) "
            "VALUES (?,?,?,?,?,?)",
            (loc_id, "queued", datetime.now(timezone.utc).isoformat(),
             payload["page_count"], n_chars, None),
        )
        conn.commit()

        if len(batch) >= args.batch_size:
            n = bulk_index(args.es_url, batch)
            indexed += n
            for d in batch:
                conn.execute(
                    "UPDATE items SET status='indexed' WHERE item_id=?",
                    (d["item_id"],),
                )
            conn.commit()
            batch = []
            elapsed = time.time() - start
            logger.info(
                "Progress: indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                indexed, skipped, errors, elapsed,
            )

        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        n = bulk_index(args.es_url, batch)
        indexed += n
        for d in batch:
            conn.execute(
                "UPDATE items SET status='indexed' WHERE item_id=?",
                (d["item_id"],),
            )
        conn.commit()

    logger.info(
        "Done. indexed=%d skipped=%d errors=%d elapsed=%.0fs",
        indexed, skipped, errors, time.time() - start,
    )
    conn.close()


if __name__ == "__main__":
    main()
