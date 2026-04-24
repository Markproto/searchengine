"""
Index downloaded WEF PDFs into the profoundd_wef_docs Elasticsearch index.

One ES doc per PDF page, deterministic _id so reruns are idempotent.
Title, doc_id, and year are derived from the filename (WEF_ convention).

Run inside the container:
    docker exec profoundd python /app/scripts/wef_indexer.py

Safe to re-run while wef_downloader.py is still fetching; only indexes
rows marked status='done' in the state DB. Already-indexed pages are
overwritten (same _id), so nothing duplicates.
"""
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from PyPDF2 import PdfReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wef_indexer")

ES_URL = "http://elasticsearch:9200"
INDEX = "profoundd_wef_docs"
BASE = Path("/app/data/wef-docs")
STATE_DB = BASE / "state.db"

MAPPING = {
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "filename": {"type": "keyword"},
            "document_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "year": {"type": "integer"},
            "page_number": {"type": "integer"},
            "title": {"type": "text"},
            "content": {"type": "text"},
            "source_url": {"type": "keyword"},
            "wayback_ts": {"type": "keyword"},
            "file_path": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "result_type": {"type": "keyword"},
            "indexed_at": {"type": "date"},
            "size_bytes": {"type": "long"},
        }
    }
}

# Rough tag hints from filename keywords — helps category search later
TAG_HINTS = {
    "global_risks": ["global-risks", "risk"],
    "great_reset": ["great-reset"],
    "fourth_industrial": ["4ir", "fourth-industrial-revolution"],
    "cyber": ["cybersecurity", "cyber"],
    "climate": ["climate"],
    "gender": ["gender", "equity"],
    "future_of_jobs": ["future-of-jobs", "jobs", "labor"],
    "energy_transition": ["energy-transition", "energy"],
    "digital": ["digital", "technology"],
    "health": ["health"],
    "stakeholder_capitalism": ["stakeholder-capitalism", "capitalism"],
    "agenda": ["agenda"],
    "davos": ["davos", "annual-meeting"],
    "china": ["china"],
    "blockchain": ["blockchain"],
    "ai_": ["ai", "artificial-intelligence"],
    "biodiversity": ["biodiversity"],
    "water": ["water"],
    "food": ["food-system"],
    "trade": ["trade"],
    "competitiveness": ["competitiveness"],
}


def ensure_index():
    r = requests.head(f"{ES_URL}/{INDEX}")
    if r.status_code == 404:
        r = requests.put(f"{ES_URL}/{INDEX}", json=MAPPING)
        r.raise_for_status()
        logger.info("Created index %s", INDEX)
    else:
        logger.info("Index %s already exists", INDEX)


def slugify(name):
    # 'WEF_Global_Risks_Report_2025.pdf' -> 'wef-global-risks-report-2025'
    s = name[:-4] if name.lower().endswith(".pdf") else name
    s = s.replace("_", "-").replace(" ", "-")
    s = re.sub(r"-+", "-", s).strip("-").lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s


def derive_title(filename):
    s = filename[:-4] if filename.lower().endswith(".pdf") else filename
    if s.startswith("WEF_"):
        s = s[4:]
    elif s.startswith("WEF "):
        s = s[4:]
    # Collapse underscores/dashes to spaces
    s = re.sub(r"[_]+", " ", s).strip()
    return s


def derive_year(filename):
    m = re.search(r"\b(19|20)\d{2}\b", filename)
    if m:
        try:
            y = int(m.group(0))
            if 1970 <= y <= datetime.now().year + 1:
                return y
        except ValueError:
            pass
    return None


def derive_tags(filename):
    low = filename.lower()
    tags = ["wef", "world-economic-forum"]
    for key, vals in TAG_HINTS.items():
        if key in low:
            tags.extend(vals)
    y = derive_year(filename)
    if y:
        tags.append(f"year-{y}")
    return sorted(set(tags))


def extract_pages(pdf_path):
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        logger.warning("Cannot open %s: %s", pdf_path.name, e)
        return
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.debug("Page %d extract failed for %s: %s", i, pdf_path.name, e)
            text = ""
        yield i, text.strip()


def bulk_index(docs):
    if not docs:
        return 0
    lines = []
    for d in docs:
        _id = d.pop("_id")
        lines.append(json.dumps({"index": {"_index": INDEX, "_id": _id}}))
        lines.append(json.dumps(d))
    body = "\n".join(lines) + "\n"
    r = requests.post(
        f"{ES_URL}/_bulk",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        timeout=60,
    )
    if r.status_code >= 400:
        logger.error("bulk error %s: %s", r.status_code, r.text[:300])
        return 0
    items = r.json().get("items", [])
    return sum(1 for it in items if it.get("index", {}).get("status", 500) in (200, 201))


def index_one(row):
    filename, original_url, wayback_ts, size_bytes = row
    pdf_path = BASE / filename
    if not pdf_path.is_file():
        logger.warning("Missing on disk: %s", filename)
        return 0

    doc_id = slugify(filename)
    document_name = derive_title(filename)
    year = derive_year(filename)
    tags = derive_tags(filename)
    now = datetime.now(timezone.utc).isoformat()

    batch = []
    total_ok = 0
    skipped = 0
    total_pages = 0

    for page_no, text in extract_pages(pdf_path):
        total_pages += 1
        if len(text) < 20:
            skipped += 1
            continue
        batch.append({
            "_id": f"{doc_id}-p{page_no:04d}",
            "doc_id": doc_id,
            "filename": filename,
            "document_name": document_name,
            "year": year,
            "page_number": page_no,
            "title": f"{document_name} — Page {page_no}",
            "content": text,
            "source_url": original_url,
            "wayback_ts": wayback_ts,
            "file_path": str(pdf_path),
            "tags": tags,
            "result_type": "wef-doc",
            "indexed_at": now,
            "size_bytes": size_bytes,
        })
        if len(batch) >= 75:
            total_ok += bulk_index(batch)
            batch = []
    if batch:
        total_ok += bulk_index(batch)

    logger.info("[%s] %d pages indexed / %d total (%d skipped)", doc_id, total_ok, total_pages, skipped)
    return total_ok


def main():
    ensure_index()
    if not STATE_DB.is_file():
        logger.error("State DB not found: %s (run wef_downloader first)", STATE_DB)
        sys.exit(1)
    conn = sqlite3.connect(STATE_DB)
    rows = conn.execute(
        "SELECT filename, original_url, wayback_ts, size_bytes FROM downloads WHERE status='done' ORDER BY filename"
    ).fetchall()
    logger.info("%d completed downloads available to index", len(rows))

    grand = 0
    for i, row in enumerate(rows, 1):
        logger.info("[%d/%d] %s", i, len(rows), row[0])
        grand += index_one(row)

    requests.post(f"{ES_URL}/{INDEX}/_refresh")
    r = requests.get(f"{ES_URL}/{INDEX}/_count")
    logger.info("DONE. Indexed %d new pages total. Index count: %s", grand, r.json().get("count"))


if __name__ == "__main__":
    main()
