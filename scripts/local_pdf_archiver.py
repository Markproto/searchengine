#!/usr/bin/env python3
"""
Local PDF archiver for archive_docs + fwp_docs.

Walks Elasticsearch for items whose `pdf_url` is a real http(s) URL and
downloads each PDF to /app/data/archive-pdfs/<collection>/<doc_id>.pdf
(or /app/data/fwp-pdfs/<item_id>.pdf for FWP).

Why: indexers store extracted text + the source URL. If the source goes
down, removes the file, or starts requiring login, we lose access. Local
mirror solves that — we serve from disk instead.

Usage examples:

    # Smaller collections first (default)
    python scripts/local_pdf_archiver.py \\
        --es-url http://127.0.0.1:9201 \\
        --output-root /app/data/archive-pdfs \\
        --collections steiner,hesperian,military-manuals,acip

    # Just FWP
    python scripts/local_pdf_archiver.py \\
        --output-root /app/data/fwp-pdfs --fwp

    # All archive collections
    python scripts/local_pdf_archiver.py --all-archive

State DB: <output-root>/<collection>.archive_state.db so resumable.
Skips already-downloaded files. Polite 0.5 req/sec default.
"""
import argparse
import hashlib
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("local_pdf_archiver")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0"
)
ARCHIVE_INDEX = "profoundd_archive_docs"
FWP_INDEX = "profoundd_fwp_docs"
EPSTEIN_INDEX = "profoundd_epstein_docs"

# Per-collection size cap: refuse to download anything bigger than this.
# Some PHMPT and archive.org PDFs run >100MB; cap so a single doc can't
# eat the disk.
MAX_PDF_BYTES = 200 * 1024 * 1024  # 200MB


def init_state(output_root, collection):
    out = Path(output_root)
    out.mkdir(parents=True, exist_ok=True)
    db = out / f"{collection}.archive_state.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS archived (
        doc_id TEXT PRIMARY KEY,
        pdf_url TEXT,
        local_path TEXT,
        bytes INTEGER,
        status TEXT,
        archived_at TEXT,
        error TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON archived(status)")
    conn.commit()
    return conn


def es_scan(es_url, query, fields, batch_size=100):
    """Iterate every doc matching the query via search-after pagination.
    Yields dicts with the requested `fields` populated."""
    body = {
        "size": batch_size,
        "query": query,
        "_source": fields,
        "sort": [{"_id": "asc"}],
    }
    last = None
    while True:
        if last is not None:
            body["search_after"] = [last]
        try:
            r = requests.post(
                f"{es_url}/_search",
                json=body,
                timeout=60,
            )
            r.raise_for_status()
        except Exception as e:
            logger.error("ES scan failed: %s", e)
            return
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return
        for h in hits:
            src = h.get("_source", {}) or {}
            src["_id"] = h.get("_id")
            yield src
        last = hits[-1].get("sort", [None])[0]
        if not last:
            return


def safe_filename(s):
    """Slug the doc_id / collection name for filesystem use."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:200]


def download_pdf(url, out_path, rate=0.5, timeout=300):
    """GET url, stream to out_path. Return (bytes_written, error_msg)."""
    try:
        time.sleep(rate)
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            stream=True,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return 0, f"http {r.status_code}"
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "pdf" not in ctype and not url.lower().endswith(".pdf"):
            r.close()
            return 0, f"not pdf (ct={ctype[:40]})"
        clen = r.headers.get("Content-Length")
        if clen and int(clen) > MAX_PDF_BYTES:
            r.close()
            return 0, f"too large ({int(clen)})"
        # Write atomically via .part
        tmp = str(out_path) + ".part"
        total = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=131072):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    r.close()
                    f.close()
                    os.remove(tmp)
                    return 0, f"exceeded cap mid-download"
                f.write(chunk)
        r.close()
        os.replace(tmp, out_path)
        return total, None
    except Exception as e:
        return 0, str(e)[:200]


def archive_collection(es_url, collection, output_root, rate=0.5, max_items=None):
    """Walk archive_docs for one collection, download PDFs."""
    conn = init_state(output_root, collection)
    out_dir = Path(output_root) / collection
    out_dir.mkdir(parents=True, exist_ok=True)

    query = {"term": {"collection": collection}}
    fields = ["doc_id", "title", "pdf_url", "source_url", "page_count"]

    archived = skipped = errors = 0
    nopdf = 0
    start = time.time()

    for doc in es_scan(es_url, query, fields):
        doc_id = doc.get("doc_id") or doc["_id"]
        url = doc.get("pdf_url") or ""
        if not url or not url.startswith("http"):
            nopdf += 1
            continue

        row = conn.execute(
            "SELECT status, bytes FROM archived WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if row and row[0] == "ok":
            skipped += 1
            continue

        fname = safe_filename(doc_id) + ".pdf"
        out_path = out_dir / fname

        size, err = download_pdf(url, out_path, rate=rate)
        if err or size == 0:
            errors += 1
            conn.execute(
                "INSERT OR REPLACE INTO archived "
                "(doc_id,pdf_url,local_path,bytes,status,archived_at,error) "
                "VALUES (?,?,?,?,?,?,?)",
                (doc_id, url, str(out_path), 0, "error",
                 datetime.now(timezone.utc).isoformat(), err),
            )
            conn.commit()
            continue

        archived += 1
        conn.execute(
            "INSERT OR REPLACE INTO archived "
            "(doc_id,pdf_url,local_path,bytes,status,archived_at,error) "
            "VALUES (?,?,?,?,?,?,?)",
            (doc_id, url, str(out_path), size, "ok",
             datetime.now(timezone.utc).isoformat(), None),
        )
        conn.commit()

        if archived % 25 == 0:
            elapsed = time.time() - start
            mb_total = sum(r[0] for r in conn.execute(
                "SELECT bytes FROM archived WHERE status='ok'"
            ).fetchall()) / 1024 / 1024
            logger.info(
                "[%s] archived=%d skipped=%d errors=%d nopdf=%d total=%.0fMB elapsed=%.0fs",
                collection, archived, skipped, errors, nopdf, mb_total, elapsed,
            )
        if max_items and archived >= max_items:
            break

    elapsed = time.time() - start
    logger.info(
        "[%s] DONE archived=%d skipped=%d errors=%d nopdf=%d elapsed=%.0fs",
        collection, archived, skipped, errors, nopdf, elapsed,
    )
    conn.close()
    return archived


def archive_fwp(es_url, output_root, rate=0.5, max_items=None):
    """Walk profoundd_fwp_docs, download each item's pdf_url to disk."""
    conn = init_state(output_root, "fwp")
    out_dir = Path(output_root) / "fwp"
    out_dir.mkdir(parents=True, exist_ok=True)

    query = {"match_all": {}}
    fields = ["item_id", "title", "pdf_url", "source_url"]

    archived = skipped = errors = nopdf = 0
    start = time.time()

    for doc in es_scan(es_url, query, fields):
        # FWP uses item_id as the natural key
        doc_id = doc.get("item_id") or doc["_id"]
        url = doc.get("pdf_url") or ""
        if not url or not url.startswith("http"):
            nopdf += 1
            continue

        row = conn.execute(
            "SELECT status FROM archived WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if row and row[0] == "ok":
            skipped += 1
            continue

        fname = safe_filename(doc_id) + ".pdf"
        out_path = out_dir / fname
        size, err = download_pdf(url, out_path, rate=rate)
        if err or size == 0:
            errors += 1
            conn.execute(
                "INSERT OR REPLACE INTO archived "
                "(doc_id,pdf_url,local_path,bytes,status,archived_at,error) "
                "VALUES (?,?,?,?,?,?,?)",
                (doc_id, url, str(out_path), 0, "error",
                 datetime.now(timezone.utc).isoformat(), err),
            )
            conn.commit()
            continue
        archived += 1
        conn.execute(
            "INSERT OR REPLACE INTO archived "
            "(doc_id,pdf_url,local_path,bytes,status,archived_at,error) "
            "VALUES (?,?,?,?,?,?,?)",
            (doc_id, url, str(out_path), size, "ok",
             datetime.now(timezone.utc).isoformat(), None),
        )
        conn.commit()

        if archived % 50 == 0:
            elapsed = time.time() - start
            logger.info(
                "[fwp] archived=%d skipped=%d errors=%d nopdf=%d elapsed=%.0fs",
                archived, skipped, errors, nopdf, elapsed,
            )
        if max_items and archived >= max_items:
            break
    logger.info("[fwp] DONE archived=%d skipped=%d errors=%d nopdf=%d",
                archived, skipped, errors, nopdf)
    conn.close()
    return archived


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", default="http://127.0.0.1:9201")
    p.add_argument("--output-root", default="/app/data/archive-pdfs")
    p.add_argument("--collections", default=None,
                   help="Comma-separated archive_docs collection slugs")
    p.add_argument("--fwp", action="store_true",
                   help="Also archive FWP items (separate output subdir)")
    p.add_argument("--all-archive", action="store_true",
                   help="Walk every distinct collection in archive_docs")
    p.add_argument("--rate", type=float, default=0.5)
    p.add_argument("--max-items", type=int, default=None,
                   help="Cap per-collection (for testing)")
    args = p.parse_args()

    collections = []
    if args.all_archive:
        # Discover collection slugs via aggregation
        try:
            r = requests.post(
                f"{args.es_url}/{ARCHIVE_INDEX}/_search",
                json={
                    "size": 0,
                    "aggs": {
                        "by": {"terms": {"field": "collection", "size": 30}},
                    },
                },
                timeout=30,
            )
            data = r.json()
            collections = [
                b["key"]
                for b in data["aggregations"]["by"]["buckets"]
            ]
        except Exception as e:
            logger.error("could not list collections: %s", e)
    elif args.collections:
        collections = [c.strip() for c in args.collections.split(",") if c.strip()]

    for c in collections:
        archive_collection(args.es_url, c, args.output_root,
                           rate=args.rate, max_items=args.max_items)

    if args.fwp:
        archive_fwp(args.es_url, args.output_root,
                    rate=args.rate, max_items=args.max_items)

    logger.info("All collections done.")


if __name__ == "__main__":
    main()
