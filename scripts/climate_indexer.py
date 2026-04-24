"""
Index Oregon city climate action / planning documents into Elasticsearch.

Extracts text page-by-page from each PDF in /app/data/climate-docs/,
creates one ES doc per page in the profoundd_climate_docs index.

Run inside the profoundd container:
    docker exec profoundd python /app/scripts/climate_indexer.py
"""
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PyPDF2 import PdfReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("climate_indexer")

ES_URL = "http://elasticsearch:9200"
INDEX = "profoundd_climate_docs"
BASE = Path("/app/data/climate-docs")

DOCS = {
    "ashland-ceap": {
        "city": "Ashland",
        "document_name": "Climate and Energy Action Plan (CEAP)",
        "filename": "ashland-ceap.pdf",
        "source_url": "https://ashlandoregon.gov/DocumentCenter/View/1733/CEAP-With-Appendices-",
        "adopted_date": "2017-03-07",
        "tags": ["climate", "ashland", "ceap", "climate-action-plan"],
    },
    "grantspass-seap": {
        "city": "Grants Pass",
        "document_name": "Sustainability and Energy Action Plan",
        "filename": "grantspass-seap.pdf",
        "source_url": "https://grantspassoregon.gov/DocumentCenter/View/28972/Final-Sustainability-and-Energy-Action-Plan",
        "adopted_date": "2023-05-17",
        "tags": ["climate", "grants-pass", "seap", "sustainability"],
    },
    "medford-cfa-study": {
        "city": "Medford",
        "document_name": "Climate-Friendly Areas Evaluation Report",
        "filename": "medford-cfa-study.pdf",
        "source_url": "https://www.oregon.gov/lcd/CL/Documents/MedfordCFAStudy.pdf",
        "adopted_date": "2023-12-01",
        "tags": ["climate", "medford", "cfec", "cfa", "climate-friendly-areas"],
    },
    "medford-tsp": {
        "city": "Medford",
        "document_name": "Transportation System Plan 2018-2038",
        "filename": "medford-tsp.pdf",
        "source_url": "https://scholarsbank.uoregon.edu/items/d55a3411-66b0-4d1d-810b-49c677daf74f",
        "adopted_date": "2018-12-06",
        "tags": ["climate", "medford", "tsp", "transportation", "streets"],
    },
}

MAPPING = {
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "city": {"type": "keyword"},
            "document_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "page_number": {"type": "integer"},
            "title": {"type": "text"},
            "content": {"type": "text"},
            "source_url": {"type": "keyword"},
            "adopted_date": {"type": "date"},
            "file_path": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "result_type": {"type": "keyword"},
            "indexed_at": {"type": "date"},
        }
    }
}


def ensure_index():
    r = requests.head(f"{ES_URL}/{INDEX}")
    if r.status_code == 404:
        r = requests.put(f"{ES_URL}/{INDEX}", json=MAPPING)
        r.raise_for_status()
        logger.info("Created index %s", INDEX)
    else:
        logger.info("Index %s already exists", INDEX)


def extract_pages(pdf_path):
    reader = PdfReader(str(pdf_path))
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as e:
            logger.warning("Page %d extract failed for %s: %s", i, pdf_path.name, e)
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
    ok = sum(1 for it in items if it.get("index", {}).get("status", 500) in (200, 201))
    return ok


def index_document(doc_id, meta):
    pdf_path = BASE / meta["filename"]
    if not pdf_path.is_file():
        logger.error("Missing %s", pdf_path)
        return 0

    logger.info("Indexing %s (%s)", doc_id, meta["document_name"])
    batch = []
    total_ok = 0
    total_pages = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for page_no, text in extract_pages(pdf_path):
        total_pages += 1
        if len(text) < 20:
            skipped += 1
            continue
        es_doc = {
            "_id": f"{doc_id}-p{page_no:04d}",
            "doc_id": doc_id,
            "city": meta["city"],
            "document_name": meta["document_name"],
            "page_number": page_no,
            "title": f"{meta['city']} — {meta['document_name']} — Page {page_no}",
            "content": text,
            "source_url": meta["source_url"],
            "adopted_date": meta["adopted_date"],
            "file_path": str(pdf_path),
            "tags": meta["tags"],
            "result_type": "climate-doc",
            "indexed_at": now,
        }
        batch.append(es_doc)
        if len(batch) >= 50:
            total_ok += bulk_index(batch)
            batch = []

    if batch:
        total_ok += bulk_index(batch)

    logger.info(
        "[%s] Indexed %d pages (skipped %d empty/image-only, total %d)",
        doc_id, total_ok, skipped, total_pages,
    )
    return total_ok


def main():
    ensure_index()
    grand = 0
    for doc_id, meta in DOCS.items():
        grand += index_document(doc_id, meta)
    # Refresh so counts are live
    requests.post(f"{ES_URL}/{INDEX}/_refresh")
    logger.info("DONE. Total pages indexed: %d", grand)
    r = requests.get(f"{ES_URL}/{INDEX}/_count")
    logger.info("Current index count: %s", r.json().get("count"))


if __name__ == "__main__":
    main()
