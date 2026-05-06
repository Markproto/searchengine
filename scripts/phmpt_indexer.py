#!/usr/bin/env python3
"""
PHMPT (Public Health and Medical Professionals for Transparency) indexer.

Court-ordered FDA release of Pfizer's COVID-19 vaccine BLA submission.
phmpt.org has 4 collection pages, each linking to thousands of static
PDFs at phmpt.org/wp-content/uploads/...

This indexer:
  1. Fetches each collection HTML page
  2. Extracts every <a href="...pdf"> in the page
  3. Downloads each PDF, runs PyPDF2 text extraction, indexes to
     `profoundd_archive_docs` with collection="phmpt"
  4. Resumable via SQLite state DB

Usage:
    python scripts/phmpt_indexer.py \\
        --es-url http://127.0.0.1:9201 \\
        --state-dir /home/mark/phmpt-state \\
        --rate 1.0
"""
import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

# Allow running as a script — add scripts/ to path so the common module imports.
sys.path.insert(0, str(Path(__file__).parent))
from _archive_indexer_common import (  # noqa: E402
    ensure_archive_index, bulk_index, extract_pdf_text,
    init_state, make_doc_id, USER_AGENT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("phmpt")

COLLECTION = "phmpt"
COLLECTION_PAGES = [
    "https://phmpt.org/pfizer-16-plus-documents/",
    "https://phmpt.org/pfizer-12-15-documents/",
    "https://phmpt.org/pfizer-12-15-and-moderna-court-documents/",
    "https://phmpt.org/pfizer-court-documents/",
]


def fetch_listing_pdfs():
    """Walk every PHMPT collection page and yield (sub_collection, pdf_url)."""
    seen = set()
    for page in COLLECTION_PAGES:
        sub = urlparse(page).path.strip("/").split("/")[-1]
        try:
            r = requests.get(page, headers={"User-Agent": USER_AGENT}, timeout=60)
            if r.status_code != 200:
                logger.warning("listing %s: %s", page, r.status_code)
                continue
        except Exception as e:
            logger.warning("listing %s: %s", page, e)
            continue
        # Extract all PDF anchors that point at phmpt.org/wp-content/...
        urls = re.findall(
            r'href=["\'](https?://phmpt\.org/wp-content/[^"\']+\.pdf)["\']',
            r.text, re.IGNORECASE,
        )
        # Some links may also be relative — catch those too
        rel = re.findall(
            r'href=["\']([^"\']+\.pdf)["\']', r.text, re.IGNORECASE,
        )
        for u in urls + [
            ("https://phmpt.org" + r) if r.startswith("/") else r
            for r in rel if "phmpt.org" in r or r.startswith("/wp-content/")
        ]:
            if u in seen:
                continue
            seen.add(u)
            yield sub, u


def parse_metadata(pdf_url):
    """Extract a human title from the URL path."""
    name = urlparse(pdf_url).path.rsplit("/", 1)[-1]
    name = name[:-4] if name.lower().endswith(".pdf") else name
    title = name.replace("_", " ").replace("-", " ").strip()
    return title[:300]


def build_doc(pdf_url, sub_collection, text, page_count):
    title = parse_metadata(pdf_url)
    if text:
        summary = text[:500].replace("\n", " ").strip()
        if len(text) > 500:
            summary = summary.rsplit(" ", 1)[0] + "…"
    else:
        summary = (
            f"Pfizer FDA submission document — {page_count} pages. "
            "PHMPT court-ordered release. Open the PDF to read."
        )
    return {
        "doc_id": make_doc_id(COLLECTION, pdf_url),
        "collection": COLLECTION,
        "title": title,
        "summary": summary,
        "content": text or "",
        "case": "pfizer-bla-submission",
        "sub_case": sub_collection,
        "page_count": page_count,
        "pdf_url": pdf_url,
        "source_url": pdf_url,
        "tags": ["phmpt", "pfizer", "covid", sub_collection],
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "archive",
        "result_type": "document",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", default="http://127.0.0.1:9201")
    p.add_argument("--state-dir", default="/home/mark/phmpt-state")
    p.add_argument("--rate", type=float, default=1.0)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()

    conn = init_state(args.state_dir, "phmpt_state.db", key_column="url")
    ensure_archive_index(args.es_url)

    indexed = skipped = errors = 0
    batch = []
    start = time.time()

    for sub, pdf_url in fetch_listing_pdfs():
        row = conn.execute("SELECT status FROM items WHERE url=?", (pdf_url,)).fetchone()
        if row and row[0] == "indexed":
            skipped += 1
            continue

        time.sleep(args.rate)
        try:
            r = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=120)
            if r.status_code != 200 or "pdf" not in r.headers.get("Content-Type", "").lower():
                errors += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items (url,status,error,indexed_at) VALUES (?,?,?,?)",
                    (pdf_url, "error", f"http {r.status_code}",
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue
            text, pages = extract_pdf_text(r.content)
        except Exception as e:
            errors += 1
            logger.warning("%s: %s", pdf_url, e)
            conn.execute(
                "INSERT OR REPLACE INTO items (url,status,error,indexed_at) VALUES (?,?,?,?)",
                (pdf_url, "error", str(e)[:200],
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            continue

        doc = build_doc(pdf_url, sub, text, pages)
        batch.append(doc)
        conn.execute(
            "INSERT OR REPLACE INTO items "
            "(url,status,indexed_at,page_count,text_chars,error) VALUES (?,?,?,?,?,?)",
            (pdf_url, "queued", datetime.now(timezone.utc).isoformat(),
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
            logger.info("Progress: indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                        indexed, skipped, errors, elapsed)
        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        n = bulk_index(args.es_url, batch)
        indexed += n
        for d in batch:
            conn.execute("UPDATE items SET status='indexed' WHERE url=?",
                         (d["pdf_url"],))
        conn.commit()

    logger.info("Done. indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                indexed, skipped, errors, time.time() - start)
    conn.close()


if __name__ == "__main__":
    main()
