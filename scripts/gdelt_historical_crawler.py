#!/usr/bin/env python3
"""
GDELT Historical News Crawler — builds a news archive back to 2015 (and 1995 via Wayback).

Phase 1: Downloads GDELT v2 GKG (Global Knowledge Graph) files, extracts article URLs
         matching our source domains, fetches full text, and indexes to Elasticsearch.
Phase 2: Uses Wayback Machine CDX API to go back further (1995-2015).

Runs on Apollo9. Resumable via SQLite state DB. Designed for overnight/multi-day runs.

Usage:
    python scripts/gdelt_historical_crawler.py \
        --es-url http://localhost:9201 \
        --state-db /home/mark/gdelt-crawler-state.db \
        --log /home/mark/gdelt-crawler.log \
        --fetch-workers 8 \
        --start-date 2026-04-01 \
        --direction backward
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
GDELT_MASTER_LAST_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
ES_INDEX = "profoundd_articles"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Domains we care about — built from Profoundd's source list + major US news
# This is intentionally broad to capture as much as possible
WANTED_DOMAINS = {
    # Independent / alternative
    "zerohedge.com", "theblaze.com", "dailycaller.com", "dailywire.com",
    "breitbart.com", "infowars.com", "thedailybell.com", "revolver.news",
    "theepochtimes.com", "justthenews.com", "oann.com", "newsmax.com",
    "townhall.com", "pjmedia.com", "therightscoop.com", "freebeacon.com",
    "realclearpolitics.com", "redstate.com", "hotair.com", "twitchy.com",
    "legalinsurrection.com", "spectator.org", "amgreatness.com",
    "americanthinker.com", "frontpagemag.com", "nationalreview.com",
    "thefederalist.com", "westernjournal.com", "washingtontimes.com",
    "nypost.com", "foxnews.com", "foxbusiness.com",
    # Mainstream (tracked for comparison)
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "nytimes.com", "washingtonpost.com", "cnn.com", "msnbc.com",
    "abcnews.go.com", "cbsnews.com", "nbcnews.com", "usatoday.com",
    "politico.com", "thehill.com", "axios.com", "bloomberg.com",
    "wsj.com", "ft.com", "cnbc.com",
    # Tech
    "arstechnica.com", "theverge.com", "wired.com", "techcrunch.com",
    "tomshardware.com", "hackernews.cc", "bleepingcomputer.com",
    # Medical / health
    "mercola.com", "articles.mercola.com", "naturalnews.com",
    "childrenshealthdefense.org", "greenmedinfo.com",
    # Finance
    "marketwatch.com", "seekingalpha.com", "investopedia.com",
    "fool.com", "thestreet.com", "barrons.com",
    # Legal / government
    "scotusblog.com", "law.com", "supremecourt.gov",
    "congress.gov", "govtrack.us",
    # Oregon / local
    "oregonlive.com", "oregoncorner.com", "statesmanjournal.com",
    "kgw.com", "koin.com", "opb.org",
    # Science
    "nature.com", "sciencedaily.com", "newscientist.com",
    "livescience.com", "phys.org", "space.com",
    # Substack
    "substack.com",
}

# Categories by domain pattern
DOMAIN_CATEGORIES = {
    "mercola.com": "medical", "naturalnews.com": "medical",
    "childrenshealthdefense.org": "medical", "greenmedinfo.com": "medical",
    "zerohedge.com": "finance", "marketwatch.com": "finance",
    "seekingalpha.com": "finance", "bloomberg.com": "finance",
    "cnbc.com": "finance", "wsj.com": "finance", "barrons.com": "finance",
    "investopedia.com": "finance", "fool.com": "finance",
    "thestreet.com": "finance", "foxbusiness.com": "finance",
    "arstechnica.com": "tech", "theverge.com": "tech", "wired.com": "tech",
    "techcrunch.com": "tech", "tomshardware.com": "tech",
    "bleepingcomputer.com": "tech", "hackernews.cc": "tech",
    "scotusblog.com": "legal", "law.com": "legal", "supremecourt.gov": "legal",
    "congress.gov": "legislative", "govtrack.us": "legislative",
    "nature.com": "science", "sciencedaily.com": "science",
    "newscientist.com": "science", "livescience.com": "science",
    "phys.org": "science", "space.com": "science",
    "oregonlive.com": "news", "oregoncorner.com": "news",
    "statesmanjournal.com": "news", "kgw.com": "news",
    "koin.com": "news", "opb.org": "news",
}

# Rate limiting
FETCH_DELAY_SEC = 0.5  # between article fetches
GKG_DOWNLOAD_DELAY_SEC = 0.2  # between GKG file downloads


# ---------------------------------------------------------------------------
# SQLite state management
# ---------------------------------------------------------------------------

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS gkg_files (
        url TEXT PRIMARY KEY,
        date_str TEXT,
        status TEXT DEFAULT 'pending',
        articles_found INTEGER DEFAULT 0,
        processed_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS articles (
        url TEXT PRIMARY KEY,
        title TEXT,
        source_domain TEXT,
        published_at TEXT,
        gdelt_date TEXT,
        category TEXT,
        status TEXT DEFAULT 'pending',
        content_length INTEGER DEFAULT 0,
        indexed_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gkg_status ON gkg_files(status)")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# GDELT GKG processing
# ---------------------------------------------------------------------------

def get_gkg_file_list(master_url=GDELT_MASTER_URL):
    """Fetch GDELT v2 master file list, return GKG file URLs sorted by date."""
    req = urllib.request.Request(master_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        lines = resp.read().decode("utf-8", errors="replace").strip().split("\n")
    gkg_files = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 3 and ".gkg.csv" in parts[2]:
            url = parts[2]
            # Extract date from URL: 20150218230000.gkg.csv.zip
            fname = url.rsplit("/", 1)[-1]
            date_str = fname[:8]  # YYYYMMDD
            gkg_files.append((date_str, url))
    return sorted(gkg_files, key=lambda x: x[0], reverse=True)  # newest first


def download_and_parse_gkg(url, wanted_domains):
    """Download a GKG CSV, extract article URLs matching wanted domains.
    Returns list of (article_url, date_str, source_domain)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        # Decompress if gzipped/zipped
        if url.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if not names:
                    return []
                data = zf.read(names[0])
        elif url.endswith(".gz"):
            data = gzip.decompress(data)

        text = data.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter="\t")

        articles = []
        for row in reader:
            if len(row) < 5:
                continue
            # GKG v2 columns: GKGRECORDID(0), DATE(1), SourceCollectionIdentifier(2),
            # SourceCommonName(3), DocumentIdentifier(4), ...
            doc_url = row[4].strip() if len(row) > 4 else ""
            date_str = row[1][:8] if len(row) > 1 and len(row[1]) >= 8 else ""

            if not doc_url or not doc_url.startswith("http"):
                continue

            try:
                domain = urlparse(doc_url).netloc.lower().replace("www.", "")
            except Exception:
                continue

            if domain in wanted_domains or any(d in domain for d in wanted_domains):
                articles.append((doc_url, date_str, domain))

        return articles
    except Exception as e:
        return []


# ---------------------------------------------------------------------------
# Article fetching
# ---------------------------------------------------------------------------

def fetch_article_text(url, timeout=30):
    """Fetch a URL and extract article text. Returns (title, text, published_at) or None."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            ct = resp.headers.get("Content-Type", "")
            if "html" not in ct.lower() and "text" not in ct.lower():
                return None
            html = resp.read(500_000).decode("utf-8", errors="replace")

        # Extract title
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()[:500]

        # Extract article text (simple: strip tags from <article> or <body>)
        # Try <article> first, then <main>, then <body>
        text = ""
        for tag in ("article", "main", "body"):
            m = re.search(
                rf"<{tag}[^>]*>(.*?)</{tag}>", html,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                raw = m.group(1)
                # Remove script/style blocks
                raw = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", "", raw, flags=re.IGNORECASE | re.DOTALL)
                # Strip remaining tags
                raw = re.sub(r"<[^>]+>", " ", raw)
                # Clean whitespace
                raw = re.sub(r"\s+", " ", raw).strip()
                if len(raw) > 200:
                    text = raw[:50000]
                    break

        if not text or len(text) < 100:
            return None

        # Try to extract published date from meta tags
        published_at = ""
        for pattern in [
            r'property="article:published_time"\s+content="([^"]+)"',
            r'name="date"\s+content="([^"]+)"',
            r'name="publishdate"\s+content="([^"]+)"',
            r'"datePublished"\s*:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                published_at = m.group(1)[:30]
                break

        return (title, text, published_at)
    except Exception:
        return None


def fetch_from_wayback(url, timeout=30):
    """Try fetching from Internet Archive's Wayback Machine."""
    try:
        wb_url = f"https://web.archive.org/web/2/{url}"
        return fetch_article_text(wb_url, timeout=timeout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Elasticsearch indexing
# ---------------------------------------------------------------------------

def index_article_to_es(es, article_data):
    """Index a single article to ES. Returns True on success."""
    doc_id = hashlib.md5(article_data["url"].encode()).hexdigest()
    try:
        es.index(index=ES_INDEX, id=doc_id, document=article_data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main crawler
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="GDELT Historical News Crawler")
    p.add_argument("--es-url", default="http://localhost:9201")
    p.add_argument("--state-db", default="/home/mark/gdelt-crawler-state.db")
    p.add_argument("--log", default="/home/mark/gdelt-crawler.log")
    p.add_argument("--fetch-workers", type=int, default=6)
    p.add_argument("--start-date", default="2026-04-01",
                   help="Start from this date and go backward (YYYY-MM-DD)")
    p.add_argument("--stop-date", default="2015-02-18",
                   help="Stop at this date (GDELT v2 starts Feb 2015)")
    p.add_argument("--max-gkg-files", type=int, default=0,
                   help="Limit GKG files to process (0=unlimited)")
    p.add_argument("--dry-run", action="store_true",
                   help="Only download GKG and find URLs, don't fetch articles")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    from elasticsearch import Elasticsearch
    es = Elasticsearch(args.es_url, request_timeout=60)

    db = init_db(args.state_db)
    logger.info("=== GDELT Historical Crawler Starting ===")
    logger.info("ES: %s | State DB: %s", args.es_url, args.state_db)
    logger.info("Date range: %s → %s", args.start_date, args.stop_date)

    # Phase 1: Build GKG file list and populate DB
    pending_gkg = db.execute(
        "SELECT COUNT(*) FROM gkg_files WHERE status='pending'"
    ).fetchone()[0]

    if pending_gkg == 0:
        logger.info("Fetching GDELT master file list...")
        try:
            gkg_files = get_gkg_file_list()
            logger.info("Found %d total GKG files in master list", len(gkg_files))
        except Exception as e:
            logger.error("Failed to fetch master list: %s", e)
            return

        # Filter by date range
        start_d = args.start_date.replace("-", "")
        stop_d = args.stop_date.replace("-", "")
        filtered = [(d, u) for d, u in gkg_files if stop_d <= d <= start_d]
        logger.info("Filtered to %d GKG files in date range", len(filtered))

        # Insert into DB (skip existing)
        for date_str, url in filtered:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO gkg_files (url, date_str, status) VALUES (?, ?, 'pending')",
                    (url, date_str),
                )
            except Exception:
                pass
        db.commit()
    else:
        logger.info("Resuming with %d pending GKG files in DB", pending_gkg)

    # Phase 2: Process GKG files → extract article URLs
    start_time = time.time()
    gkg_processed = 0
    total_articles_found = 0

    while True:
        rows = db.execute(
            "SELECT url, date_str FROM gkg_files WHERE status='pending' ORDER BY date_str DESC LIMIT 100"
        ).fetchall()
        if not rows:
            break

        for gkg_url, date_str in rows:
            articles = download_and_parse_gkg(gkg_url, WANTED_DOMAINS)
            for article_url, a_date, domain in articles:
                cat = DOMAIN_CATEGORIES.get(domain, "news")
                try:
                    db.execute(
                        """INSERT OR IGNORE INTO articles
                           (url, source_domain, gdelt_date, category, status)
                           VALUES (?, ?, ?, ?, 'pending')""",
                        (article_url, domain, a_date, cat),
                    )
                except Exception:
                    pass

            db.execute(
                "UPDATE gkg_files SET status='done', articles_found=?, processed_at=? WHERE url=?",
                (len(articles), datetime.now(timezone.utc).isoformat(), gkg_url),
            )
            db.commit()
            gkg_processed += 1
            total_articles_found += len(articles)

            if gkg_processed % 50 == 0:
                elapsed = time.time() - start_time
                rate = gkg_processed / elapsed * 60 if elapsed else 0
                pending = db.execute(
                    "SELECT COUNT(*) FROM gkg_files WHERE status='pending'"
                ).fetchone()[0]
                logger.info(
                    "GKG progress: %d processed, %d articles found, %d pending, %.1f files/min",
                    gkg_processed, total_articles_found, pending, rate,
                )

            if args.max_gkg_files and gkg_processed >= args.max_gkg_files:
                logger.info("Reached max GKG file limit (%d)", args.max_gkg_files)
                break

            time.sleep(GKG_DOWNLOAD_DELAY_SEC)

        if args.max_gkg_files and gkg_processed >= args.max_gkg_files:
            break

    total_pending = db.execute(
        "SELECT COUNT(*) FROM articles WHERE status='pending'"
    ).fetchone()[0]
    total_done = db.execute(
        "SELECT COUNT(*) FROM articles WHERE status='done'"
    ).fetchone()[0]
    logger.info(
        "GKG scan complete: %d files processed, %d article URLs found (%d pending, %d already done)",
        gkg_processed, total_articles_found, total_pending, total_done,
    )

    if args.dry_run:
        logger.info("Dry run — skipping article fetch phase.")
        return

    # Phase 3: Fetch article content and index to ES
    logger.info("=== Starting article fetch phase ===")
    indexed = 0
    failed = 0
    skipped = 0
    batch_start = time.time()

    while True:
        rows = db.execute(
            "SELECT url, source_domain, gdelt_date, category FROM articles WHERE status='pending' ORDER BY gdelt_date DESC LIMIT 500"
        ).fetchall()
        if not rows:
            break

        def process_one(row):
            url, domain, gdelt_date, category = row
            result = fetch_article_text(url)
            if not result and domain != "substack.com":
                # Try Wayback Machine
                result = fetch_from_wayback(url)
            return (url, domain, gdelt_date, category, result)

        with ThreadPoolExecutor(max_workers=args.fetch_workers) as pool:
            futures = {pool.submit(process_one, r): r for r in rows}

            for future in as_completed(futures):
                url, domain, gdelt_date, category, result = future.result()

                if result is None:
                    db.execute("UPDATE articles SET status='failed' WHERE url=?", (url,))
                    failed += 1
                else:
                    title, text, published_at = result

                    # Use GDELT date if no published_at from article
                    if not published_at and gdelt_date:
                        try:
                            published_at = datetime.strptime(gdelt_date[:8], "%Y%m%d").isoformat()
                        except Exception:
                            published_at = ""

                    summary = " ".join(text[:1000].split())[:500] if text else ""

                    doc = {
                        "title": title,
                        "content": text,
                        "summary": summary,
                        "url": url,
                        "source_name": domain,
                        "category": category,
                        "published_at": published_at,
                        "crawled_at": datetime.now(timezone.utc).isoformat(),
                        "tags": ["gdelt-historical"],
                        "result_type": "article",
                    }

                    if index_article_to_es(es, doc):
                        db.execute(
                            "UPDATE articles SET status='done', title=?, content_length=?, indexed_at=? WHERE url=?",
                            (title, len(text), datetime.now(timezone.utc).isoformat(), url),
                        )
                        indexed += 1
                    else:
                        db.execute("UPDATE articles SET status='es_error' WHERE url=?", (url,))
                        failed += 1

                db.commit()
                total = indexed + failed + skipped
                if total % 100 == 0 and total > 0:
                    elapsed = time.time() - batch_start
                    rate = indexed / elapsed * 3600 if elapsed else 0
                    pending_left = db.execute(
                        "SELECT COUNT(*) FROM articles WHERE status='pending'"
                    ).fetchone()[0]
                    logger.info(
                        "Fetch progress: %d indexed, %d failed, %d pending, %.0f articles/hr",
                        indexed, failed, pending_left, rate,
                    )

                time.sleep(FETCH_DELAY_SEC)

    elapsed = time.time() - start_time
    logger.info(
        "=== GDELT Crawler Done === indexed=%d failed=%d total_time=%.1f hrs",
        indexed, failed, elapsed / 3600,
    )


if __name__ == "__main__":
    main()
