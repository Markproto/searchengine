"""
Shared helpers for archive collection indexers (PHMPT, ACIP, NIH RePORTER,
plus the original FBI Vault and SPLC Wayback indexers).

All collection-specific indexers should call:
    init_state(state_dir, table_name='items')
    ensure_archive_index(es_url)
    bulk_index(es_url, docs)
    extract_pdf_text(pdf_bytes)

Each indexer brings its own iteration logic, doc shape, and rate-limit
discipline; this module covers only the boilerplate.
"""
import hashlib
import io
import json
import logging
import sqlite3
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ES_INDEX = "profoundd_archive_docs"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
MAX_TEXT_CHARS = 200_000


# Mapping mirrors fbi_vault_indexer's; ensure_archive_index is idempotent
# and matches the runtime mapping used by engine.py / search_archive_docs().
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


def init_state(state_dir, db_filename, key_column="url"):
    """Create the per-collection state DB. Returns the open connection.
    `key_column` is the unique identifier (URL for PHMPT/ACIP, project_id
    for NIH grants)."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / db_filename)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS items (
            {key_column} TEXT PRIMARY KEY,
            status TEXT,
            indexed_at TEXT,
            page_count INTEGER,
            text_chars INTEGER,
            error TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON items(status)")
    conn.commit()
    return conn


def ensure_archive_index(es_url):
    """Idempotent ES index create."""
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


def bulk_index(es_url, docs):
    """Bulk-index a list of docs (each must have a `doc_id`)."""
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


def extract_pdf_text(pdf_bytes, max_chars=MAX_TEXT_CHARS):
    """Best-effort PDF → plain text using PyPDF2.
    Returns (text, page_count). Returns ("", 0) on failure."""
    try:
        from PyPDF2 import PdfReader
    except Exception as e:
        logger.warning("PyPDF2 unavailable: %s", e)
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


def make_doc_id(collection, key):
    """Stable per-collection doc id for ES."""
    return hashlib.md5(f"{collection}::{key}".encode()).hexdigest()
