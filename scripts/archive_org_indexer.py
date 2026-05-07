#!/usr/bin/env python3
"""
Generic archive.org collection indexer.

archive.org's API exposes two clean endpoints we use:
  1. /advancedsearch.php — paginated JSON search by query string
  2. /metadata/<identifier> — file list per item (we prefer the
     pre-extracted *_djvu.txt or *_hocr_searchtext.txt.gz over PDF
     because text extraction is already done)

Usage examples:

    # Survival / water / SERE / military medical
    python scripts/archive_org_indexer.py \\
        --query 'title:"FM 21-76" OR title:"Survival Evasion" OR title:"water purification" OR title:"combat medic"' \\
        --collection military-manuals

    # Rudolf Steiner public-domain books
    python scripts/archive_org_indexer.py \\
        --query 'creator:"Rudolf Steiner" AND mediatype:texts' \\
        --collection steiner

State DB at <state-dir>/archive_org_<collection>.db so multiple
collections can run in parallel without colliding.
"""
import argparse
import gzip
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

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
logger = logging.getLogger("archive_org")

ARCHIVE_BASE = "https://archive.org"
SEARCH_URL = ARCHIVE_BASE + "/advancedsearch.php"
META_URL = ARCHIVE_BASE + "/metadata/{identifier}"
DOWNLOAD_URL = ARCHIVE_BASE + "/download/{identifier}/{filename}"
MAX_TEXT_CHARS = 200_000


def search_items(query, rows=100, page=1):
    """Hit archive.org's search API. Returns list of identifier dicts."""
    params = {
        "q": query,
        "fl[]": ["identifier", "title", "creator", "date", "subject"],
        "rows": rows,
        "page": page,
        "output": "json",
    }
    r = requests.get(
        SEARCH_URL, params=params,
        headers={"User-Agent": USER_AGENT}, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("response", {}).get("docs", []), data.get("response", {}).get("numFound", 0)


def fetch_metadata(identifier):
    r = requests.get(
        META_URL.format(identifier=identifier),
        headers={"User-Agent": USER_AGENT}, timeout=60,
    )
    if r.status_code != 200:
        return None
    return r.json()


def pick_text_or_pdf(metadata):
    """Return (kind, filename) where kind is 'text' or 'pdf'.
    Prefer pre-extracted text — much faster than downloading a PDF and
    running PyPDF2 over it."""
    files = metadata.get("files", []) or []
    text_file = None
    pdf_file = None
    for f in files:
        name = (f.get("name") or "").lower()
        fmt = (f.get("format") or "").lower()
        if name.endswith("_djvu.txt") and not text_file:
            text_file = f["name"]
        elif name.endswith("_hocr_searchtext.txt.gz") and not text_file:
            text_file = f["name"]
        elif fmt in ("text pdf", "pdf") and not pdf_file:
            pdf_file = f["name"]
    if text_file:
        return ("text", text_file)
    if pdf_file:
        return ("pdf", pdf_file)
    return (None, None)


def fetch_text(identifier, filename, kind):
    """Download + extract text. Returns (text, byte_size_downloaded)."""
    url = DOWNLOAD_URL.format(identifier=identifier, filename=quote_plus(filename))
    # quote_plus over-escapes slashes — undo
    url = url.replace("%2F", "/")
    try:
        if kind == "text":
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
            if r.status_code != 200:
                return "", 0
            content = r.content
            if filename.endswith(".gz"):
                content = gzip.decompress(content)
            text = content.decode("utf-8", errors="ignore")
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS] + "\n…[truncated]"
            return text, len(content)
        else:  # pdf
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180)
            if r.status_code != 200:
                return "", 0
            text, _pages = extract_pdf_text(r.content, max_chars=MAX_TEXT_CHARS)
            return text, len(r.content)
    except Exception as e:
        logger.debug("fetch_text %s/%s: %s", identifier, filename, e)
        return "", 0


def build_doc(identifier, doc_meta, text, collection):
    md = doc_meta.get("metadata", {}) if isinstance(doc_meta, dict) else {}
    title = md.get("title") or doc_meta.get("title") or identifier
    if isinstance(title, list):
        title = title[0] if title else identifier
    creator = md.get("creator") or doc_meta.get("creator") or ""
    if isinstance(creator, list):
        creator = ", ".join(c for c in creator if c)
    date = md.get("date") or doc_meta.get("date") or ""
    subjects = md.get("subject") or doc_meta.get("subject") or []
    if isinstance(subjects, str):
        subjects = [subjects]

    summary = (text[:500].replace("\n", " ").strip() if text else "")
    if len(text) > 500:
        summary = summary.rsplit(" ", 1)[0] + "…"

    # Coerce date to YYYY or YYYY-MM-DD if possible
    doc_date = ""
    if date:
        m = re.match(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", str(date))
        if m:
            parts = [m.group(1)]
            if m.group(2):
                parts.append(m.group(2))
                if m.group(3):
                    parts.append(m.group(3))
            doc_date = "-".join(parts)

    return {
        "doc_id": make_doc_id(collection, identifier),
        "collection": collection,
        "title": (title or "")[:400],
        "summary": summary,
        "content": text or "",
        "case": collection,
        "sub_case": (creator or "")[:200],
        "page_count": 0,
        "pdf_url": f"{ARCHIVE_BASE}/details/{identifier}",
        "source_url": f"{ARCHIVE_BASE}/details/{identifier}",
        "doc_date": doc_date,
        "tags": [collection] + [str(s)[:60] for s in subjects[:10]],
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "archive",
        "result_type": "document",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", default="http://127.0.0.1:9201")
    p.add_argument("--state-dir", default="/home/mark/archive-org-state")
    p.add_argument("--query", required=True,
                   help="archive.org advanced-search query string")
    p.add_argument("--collection", required=True,
                   help="our collection slug, e.g. military-manuals")
    p.add_argument("--rate", type=float, default=0.7)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--page-size", type=int, default=50,
                   help="archive.org rows per search page")
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()

    state_path = f"archive_org_{args.collection}_state.db"
    conn = init_state(args.state_dir, state_path, key_column="identifier")
    ensure_archive_index(args.es_url)

    indexed = skipped = errors = empty = 0
    batch = []
    start = time.time()
    page = 1

    while True:
        time.sleep(args.rate)
        try:
            docs, total = search_items(args.query, rows=args.page_size, page=page)
        except Exception as e:
            logger.error("search failed page=%d: %s", page, e)
            break
        if not docs:
            break
        if page == 1:
            logger.info("query matched %d items total", total)

        for doc in docs:
            ident = doc.get("identifier")
            if not ident:
                continue
            row = conn.execute(
                "SELECT status FROM items WHERE identifier=?", (ident,)
            ).fetchone()
            if row and row[0] == "indexed":
                skipped += 1
                continue

            time.sleep(args.rate)
            meta = fetch_metadata(ident)
            if not meta:
                errors += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items (identifier,status,error,indexed_at) VALUES (?,?,?,?)",
                    (ident, "error", "metadata_fetch_failed",
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue

            kind, filename = pick_text_or_pdf(meta)
            if not filename:
                empty += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items (identifier,status,error,indexed_at) VALUES (?,?,?,?)",
                    (ident, "no_text_or_pdf", None,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue

            time.sleep(args.rate)
            text, _bytes = fetch_text(ident, filename, kind)
            if not text:
                empty += 1
                conn.execute(
                    "INSERT OR REPLACE INTO items (identifier,status,error,indexed_at) VALUES (?,?,?,?)",
                    (ident, "extract_failed", filename,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                continue

            d = build_doc(ident, meta, text, args.collection)
            batch.append(d)
            conn.execute(
                "INSERT OR REPLACE INTO items "
                "(identifier,status,indexed_at,page_count,text_chars,error) "
                "VALUES (?,?,?,?,?,?)",
                (ident, "queued", datetime.now(timezone.utc).isoformat(),
                 0, len(text), None),
            )
            conn.commit()

            if len(batch) >= args.batch_size:
                n = bulk_index(args.es_url, batch)
                indexed += n
                for q in batch:
                    qid = q["pdf_url"].rsplit("/", 1)[-1]
                    conn.execute(
                        "UPDATE items SET status='indexed' WHERE identifier=?",
                        (qid,),
                    )
                conn.commit()
                batch = []
                logger.info(
                    "Progress: indexed=%d skipped=%d empty=%d errors=%d elapsed=%.0fs",
                    indexed, skipped, empty, errors, time.time() - start,
                )
            if args.max_items and indexed >= args.max_items:
                break

        if args.max_items and indexed >= args.max_items:
            break

        # Last page?
        if len(docs) < args.page_size:
            break
        page += 1

    if batch:
        indexed += bulk_index(args.es_url, batch)
        for q in batch:
            qid = q["pdf_url"].rsplit("/", 1)[-1]
            conn.execute(
                "UPDATE items SET status='indexed' WHERE identifier=?", (qid,),
            )
        conn.commit()

    logger.info(
        "Done. indexed=%d skipped=%d empty=%d errors=%d elapsed=%.0fs",
        indexed, skipped, empty, errors, time.time() - start,
    )
    conn.close()


if __name__ == "__main__":
    main()
