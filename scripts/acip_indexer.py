#!/usr/bin/env python3
"""
ACIP (Advisory Committee on Immunization Practices) indexer.

CDC's vaccine policy advisory committee. The meetings index page at
cdc.gov/acip/meetings/index.html lists agendas + linked materials as
static PDFs. We walk the index, follow internal pages, and pull every
PDF we find under /acip/.

Resumable via SQLite state DB. Polite 1 req/sec.
"""
import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

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
logger = logging.getLogger("acip")

COLLECTION = "acip"
SEED_PAGES = [
    "https://www.cdc.gov/acip/meetings/index.html",
    "https://www.cdc.gov/acip/recs/index.html",
    "https://www.cdc.gov/acip/about/index.html",
]
BASE = "https://www.cdc.gov"


def crawl_pdfs(rate=1.0):
    """Walk seed pages, follow same-host /acip/ links one level deep,
    yield (page_url, pdf_url) pairs."""
    seen_pages = set()
    seen_pdfs = set()
    queue = list(SEED_PAGES)
    while queue:
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        try:
            r = requests.get(page, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code != 200:
                continue
        except Exception as e:
            logger.warning("page %s: %s", page, e)
            continue
        time.sleep(rate)

        # Find PDFs on this page
        pdfs = re.findall(r'href=["\']([^"\']+\.pdf)["\']', r.text, re.IGNORECASE)
        for p in pdfs:
            full = urljoin(page, p)
            # Restrict to cdc.gov /acip downloads
            if "cdc.gov" not in full or "/acip/" not in full:
                continue
            if full in seen_pdfs:
                continue
            seen_pdfs.add(full)
            yield page, full

        # Follow internal /acip/ pages one level
        if len(seen_pages) < 30:
            sub_pages = re.findall(r'href=["\'](/acip/[^"\']+\.html?)["\']',
                                   r.text, re.IGNORECASE)
            for sp in sub_pages:
                full = urljoin(BASE, sp)
                if full not in seen_pages and full not in queue:
                    queue.append(full)


def parse_title(pdf_url):
    name = urlparse(pdf_url).path.rsplit("/", 1)[-1]
    name = name[:-4] if name.lower().endswith(".pdf") else name
    return name.replace("_", " ").replace("-", " ").strip()[:300]


def parse_date(pdf_url):
    """Best-effort YYYY-MM-DD or YYYY-MM extraction from path."""
    m = re.search(r"(20\d{2})[-_](\d{1,2})[-_](\d{1,2})", pdf_url)
    if m:
        try:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[-_](\d{1,2})", pdf_url)
    if m:
        try:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        except ValueError:
            pass
    return ""


def build_doc(page_url, pdf_url, text, page_count):
    title = parse_title(pdf_url)
    doc_date = parse_date(pdf_url)
    if text:
        summary = text[:500].replace("\n", " ").strip()
        if len(text) > 500:
            summary = summary.rsplit(" ", 1)[0] + "…"
    else:
        summary = (
            f"ACIP material — {page_count} pages. "
            "CDC vaccine advisory committee document. Open the PDF to read."
        )
    # sub_case: agenda / slides / minutes / recs based on path
    path = urlparse(pdf_url).path.lower()
    if "agenda" in path:
        sub = "agendas"
    elif "slide" in path:
        sub = "slides"
    elif "minute" in path:
        sub = "minutes"
    elif "recs" in path or "recommend" in path:
        sub = "recommendations"
    else:
        sub = "other"
    return {
        "doc_id": make_doc_id(COLLECTION, pdf_url),
        "collection": COLLECTION,
        "title": title,
        "summary": summary,
        "content": text or "",
        "case": "acip",
        "sub_case": sub,
        "page_count": page_count,
        "pdf_url": pdf_url,
        "source_url": page_url,
        "doc_date": doc_date,
        "tags": ["acip", "cdc", "vaccines", sub],
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "archive",
        "result_type": "document",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", default="http://127.0.0.1:9201")
    p.add_argument("--state-dir", default="/home/mark/acip-state")
    p.add_argument("--rate", type=float, default=1.0)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()

    conn = init_state(args.state_dir, "acip_state.db", key_column="url")
    ensure_archive_index(args.es_url)

    indexed = skipped = errors = 0
    batch = []
    start = time.time()

    for page_url, pdf_url in crawl_pdfs(rate=args.rate):
        row = conn.execute("SELECT status FROM items WHERE url=?", (pdf_url,)).fetchone()
        if row and row[0] == "indexed":
            skipped += 1
            continue

        time.sleep(args.rate)
        try:
            r = requests.get(pdf_url, headers={"User-Agent": USER_AGENT}, timeout=90)
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

        doc = build_doc(page_url, pdf_url, text, pages)
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
            logger.info("Progress: indexed=%d skipped=%d errors=%d", indexed, skipped, errors)
        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        indexed += bulk_index(args.es_url, batch)
        for d in batch:
            conn.execute("UPDATE items SET status='indexed' WHERE url=?",
                         (d["pdf_url"],))
        conn.commit()

    logger.info("Done. indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                indexed, skipped, errors, time.time() - start)
    conn.close()


if __name__ == "__main__":
    main()
