"""
Download all WEF PDFs enumerated from Wayback CDX, with local persistence.

Strategy:
1. CDX listing of reports.weforum.org/docs/*.pdf serves as the manifest
2. For each PDF: try reports.weforum.org direct; on failure use Wayback
   raw (id_) URL — the archive copy is immutable so the file stays
   accessible even if WEF deletes it.
3. State DB tracks (filename, wayback_ts, source_used, status, size_bytes)
   so reruns are resumable and skip completed downloads.
4. Skips language variants (filename suffixed _AR/_DE/_ES/etc.) — primary
   English version covers the same content.

Run inside the container:
    docker exec -d profoundd python /app/scripts/wef_downloader.py

Background-friendly — logs to /app/data/wef-docs/wef-downloader.log.
"""
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = Path("/app/data/wef-docs")
BASE.mkdir(parents=True, exist_ok=True)
LOG_PATH = BASE / "wef-downloader.log"
STATE_DB = BASE / "state.db"
MANIFEST_PATH = BASE / "cdx-manifest.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger("wef_downloader")

DELAY_SECONDS = 2.0
TIMEOUT = 60
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TRANSLATION_PATTERNS = [
    re.compile(
        r"_(AR|DE|ES|FR|IT|PT|PTBR|JP|JA|ZH|CN|KO|RU|NL|SV|NO|DA|PL|TR|FI|HE|HI|TH|VI|ID|UK|CS)\.pdf$",
        re.IGNORECASE,
    ),
    re.compile(
        r"_(Arabic|Deutsch|German|French|Francais|Français|Spanish|Espanol|Español|Italian|Italiano|"
        r"Portuguese|Portugues|Português|Japanese|Chinese|Korean|Russian|Hebrew|Hindi|Thai|"
        r"Vietnamese|Indonesian|Bahasa|Ukrainian|Czech|Polish|Turkish|Dutch|Swedish|Norwegian|Danish|"
        r"PTBR|Brasileiro)"
        r"(_Bahasa)?\.pdf$",
        re.IGNORECASE,
    ),
]


def is_translation(filename):
    return any(p.search(filename) for p in TRANSLATION_PATTERNS)


def init_state():
    conn = sqlite3.connect(STATE_DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS downloads (
            filename TEXT PRIMARY KEY,
            original_url TEXT,
            wayback_ts TEXT,
            source_used TEXT,  -- 'direct' or 'wayback'
            status TEXT,       -- 'pending', 'done', 'failed', 'skipped'
            size_bytes INTEGER,
            error TEXT,
            downloaded_at TEXT
        );
    """)
    conn.commit()
    return conn


def fetch_manifest():
    """Pull the full CDX listing and cache it locally."""
    if MANIFEST_PATH.is_file():
        logger.info("Using cached manifest at %s", MANIFEST_PATH)
        return json.load(open(MANIFEST_PATH))
    url = (
        "https://web.archive.org/cdx/search/cdx"
        "?url=reports.weforum.org/docs/&matchType=prefix"
        "&output=json&collapse=urlkey"
        "&filter=mimetype:application/pdf&filter=statuscode:200"
    )
    logger.info("Fetching CDX manifest from Wayback…")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    data = r.json()
    with open(MANIFEST_PATH, "w") as f:
        json.dump(data, f)
    logger.info("Saved manifest: %d rows", len(data) - 1)
    return data


def clean_filename(original_url):
    """Strip query params and return just the filename component."""
    parsed = urlparse(original_url)
    name = parsed.path.rsplit("/", 1)[-1]
    # URL-decode common escapes
    name = name.replace("%20", "_").replace("%2F", "_")
    return name


def download_direct(original_url):
    """Try the live reports.weforum.org URL."""
    try:
        r = requests.get(
            original_url,
            headers={
                "User-Agent": UA,
                "Accept": "application/pdf,application/x-pdf,*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.weforum.org/publications/",
            },
            timeout=TIMEOUT,
            stream=True,
        )
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("application/pdf"):
            return r
    except Exception as e:
        logger.debug("direct %s failed: %s", original_url, e)
    return None


def download_wayback(original_url, timestamp):
    """Use the Wayback raw (id_) endpoint — immutable snapshot."""
    url = f"https://web.archive.org/web/{timestamp}id_/{original_url}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
            stream=True,
        )
        if r.status_code == 200:
            ctype = r.headers.get("Content-Type", "")
            if ctype.startswith("application/pdf") or "octet-stream" in ctype:
                return r
    except Exception as e:
        logger.debug("wayback %s failed: %s", original_url, e)
    return None


def save_stream(response, path):
    size = 0
    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                size += len(chunk)
    # Sanity check — has to start with %PDF
    with open(path, "rb") as f:
        head = f.read(4)
    if head != b"%PDF":
        path.unlink()
        raise ValueError(f"Not a PDF (got {head!r})")
    return size


def main():
    conn = init_state()
    manifest = fetch_manifest()
    rows = manifest[1:]
    total = len(rows)

    # Dedup: same filename can appear multiple times at different timestamps.
    # Keep the most recent timestamp per filename.
    by_file = {}
    for row in rows:
        _, ts, original, mime, status, digest, length = row
        fname = clean_filename(original)
        if not fname.lower().endswith(".pdf"):
            continue
        if is_translation(fname):
            continue
        # Keep latest timestamp
        existing = by_file.get(fname)
        if existing is None or ts > existing[1]:
            by_file[fname] = (original, ts, length)

    logger.info(
        "Manifest: %d rows -> %d unique English PDFs after dedup + filter",
        total, len(by_file),
    )

    # Mark already-done rows so we skip them
    done_filenames = set(
        r[0] for r in conn.execute(
            "SELECT filename FROM downloads WHERE status='done'"
        ).fetchall()
    )
    logger.info("Already done: %d", len(done_filenames))

    processed = 0
    succeeded = 0
    failed = 0
    for fname, (original, ts, length) in sorted(by_file.items()):
        processed += 1
        if fname in done_filenames:
            continue

        dest = BASE / fname
        logger.info("[%d/%d] %s", processed, len(by_file), fname)

        resp = download_direct(original)
        source = "direct"
        if resp is None:
            resp = download_wayback(original, ts)
            source = "wayback"
        if resp is None:
            failed += 1
            conn.execute(
                "INSERT OR REPLACE INTO downloads (filename, original_url, wayback_ts, source_used, status, error) "
                "VALUES (?, ?, ?, ?, 'failed', 'both_sources_failed')",
                (fname, original, ts, source),
            )
            conn.commit()
            time.sleep(DELAY_SECONDS)
            continue

        try:
            size = save_stream(resp, dest)
            conn.execute(
                "INSERT OR REPLACE INTO downloads (filename, original_url, wayback_ts, source_used, status, size_bytes, downloaded_at) "
                "VALUES (?, ?, ?, ?, 'done', ?, datetime('now'))",
                (fname, original, ts, source, size),
            )
            conn.commit()
            succeeded += 1
            logger.info("  -> %s  %.1f MB  via %s", fname, size / 1024 / 1024, source)
        except Exception as e:
            failed += 1
            conn.execute(
                "INSERT OR REPLACE INTO downloads (filename, original_url, wayback_ts, source_used, status, error) "
                "VALUES (?, ?, ?, ?, 'failed', ?)",
                (fname, original, ts, source, str(e)[:500]),
            )
            conn.commit()
            logger.warning("  save failed: %s", e)

        time.sleep(DELAY_SECONDS)

    logger.info(
        "DONE. processed=%d, new_success=%d, failed=%d",
        processed, succeeded, failed,
    )
    total_done = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM downloads WHERE status='done'"
    ).fetchone()
    logger.info("Total corpus: %d PDFs, %.1f GB", total_done[0], total_done[1] / 1024 / 1024 / 1024)


if __name__ == "__main__":
    main()
