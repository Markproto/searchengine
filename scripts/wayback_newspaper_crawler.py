#!/usr/bin/env python3
"""
Wayback Machine Newspaper Recovery Crawler

Recovers articles from defunct local newspapers via the Internet Archive's
Wayback Machine. Designed for Mail Tribune and Ashland Daily Tidings but
works for any domain.

Phase 1: CDX dump — pull all unique archived URLs for each domain
Phase 2: Filter — keep only article-like URLs, skip nav/images/junk
Phase 3: Fetch — download archived HTML from Wayback, extract text
Phase 4: Index — push to Profoundd's Elasticsearch

Resumable via SQLite state DB. Polite rate limiting (5 req/sec default).

Usage:
    python scripts/wayback_newspaper_crawler.py \
        --es-url http://localhost:9201 \
        --state-db /home/mark/wayback-newspapers.db \
        --log /home/mark/wayback-newspapers.log \
        --workers 5
"""
import argparse
import hashlib
import json
import logging
import re
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, unquote

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_PREFIX = "https://web.archive.org/web"
USER_AGENT = "ProfounddArchiver/1.0 (news preservation project; mark.hutto@protonmail.com)"
ES_INDEX = "profoundd_articles"

# Newspapers to recover
NEWSPAPERS = [
    {
        "domain": "mailtribune.com",
        "name": "Mail Tribune",
        "category": "news",
        "credibility": 8,
        "region": "Southern Oregon",
    },
    {
        "domain": "dailytidings.com",
        "name": "Ashland Daily Tidings",
        "category": "news",
        "credibility": 8,
        "region": "Southern Oregon",
    },
]

# URL patterns that are NOT articles (navigation, assets, etc.)
SKIP_PATTERNS = [
    r"\.(css|js|jpg|jpeg|png|gif|ico|svg|woff|woff2|ttf|eot|pdf|xml|json|rss|atom)(\?|$)",
    r"/(wp-content|wp-includes|wp-admin|assets|static|themes|plugins)/",
    r"/(feed|rss|sitemap|robots\.txt|favicon)",
    r"/tag/|/tags/|/category/|/categories/|/author/|/page/\d+",
    r"/(search|login|register|subscribe|newsletter|contact|about|privacy|terms|advertis)",
    r"/\?s=|/\?p=\d+&preview",
    r"/(classifieds|obituaries/\?|calendar/\?|events/\?)",
    r"#",  # fragment-only URLs
    r"\?utm_",  # tracking params (keep base URL)
]

# URL patterns that ARE likely articles
ARTICLE_PATTERNS = [
    r"/\d{4}/\d{2}/\d{2}/",  # /2021/03/15/headline-here
    r"/news/|/sports/|/opinion/|/business/|/entertainment/|/lifestyle/",
    r"/local/|/region/|/community/|/politics/|/crime/|/courts/",
    r"/top-stories/|/breaking/|/features/|/columns/",
    r"/obituaries/\d|/obituaries/[a-z]",  # individual obits (not listing pages)
    r"/[a-z-]+-[a-z-]+",  # slug-like paths
]

FETCH_DELAY_SEC = 0.2  # 5 req/sec to be polite to archive.org


# ---------------------------------------------------------------------------
# SQLite state
# ---------------------------------------------------------------------------

def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS urls (
        url TEXT PRIMARY KEY,
        domain TEXT,
        newspaper TEXT,
        timestamp TEXT,
        is_article INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        title TEXT,
        content_length INTEGER DEFAULT 0,
        published_at TEXT,
        fetched_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cdx_progress (
        domain TEXT PRIMARY KEY,
        total_urls INTEGER DEFAULT 0,
        article_urls INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_urls_status ON urls(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_urls_domain ON urls(domain)")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Phase 1: CDX dump
# ---------------------------------------------------------------------------

def cdx_dump(domain, db, logger, page_size=5000):
    """Pull all unique URLs for a domain from Wayback CDX API."""
    logger.info("Starting CDX dump for %s...", domain)

    # Check if already done
    row = db.execute(
        "SELECT status FROM cdx_progress WHERE domain=?", (domain,)
    ).fetchone()
    if row and row[0] == "done":
        count = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=?", (domain,)
        ).fetchone()[0]
        logger.info("CDX dump already complete for %s (%d URLs)", domain, count)
        return count

    page = 0
    total = 0
    while True:
        url = (
            f"{CDX_API}?url={domain}/*&output=json"
            f"&fl=timestamp,original,mimetype,statuscode"
            f"&filter=mimetype:text/html&filter=statuscode:200"
            f"&collapse=urlkey&page={page}&pageSize={page_size}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if "Blocked" in str(e):
                logger.warning("Rate limited on CDX page %d, sleeping 60s...", page)
                time.sleep(60)
                continue
            logger.error("CDX fetch error on page %d: %s", page, e)
            break

        if len(data) <= 1:  # header only
            break

        for row in data[1:]:
            timestamp, original, mimetype, status = row
            # Normalize URL (strip tracking params)
            clean_url = re.sub(r"\?utm_.*$", "", original)
            clean_url = re.sub(r"#.*$", "", clean_url)
            try:
                db.execute(
                    """INSERT OR IGNORE INTO urls
                       (url, domain, timestamp, status)
                       VALUES (?, ?, ?, 'pending')""",
                    (clean_url, domain, timestamp),
                )
            except Exception:
                pass
            total += 1

        db.commit()
        page += 1

        if page % 10 == 0:
            logger.info("CDX %s: page %d, %d URLs so far", domain, page, total)

        time.sleep(0.5)  # polite CDX rate

    db.execute(
        """INSERT OR REPLACE INTO cdx_progress (domain, total_urls, status)
           VALUES (?, ?, 'done')""",
        (domain, total),
    )
    db.commit()
    logger.info("CDX dump complete for %s: %d URLs", domain, total)
    return total


# ---------------------------------------------------------------------------
# Phase 2: Filter article URLs
# ---------------------------------------------------------------------------

def classify_urls(domain, newspaper_name, db, logger):
    """Mark URLs as articles or non-articles based on URL patterns."""
    skip_res = [re.compile(p, re.IGNORECASE) for p in SKIP_PATTERNS]
    article_res = [re.compile(p, re.IGNORECASE) for p in ARTICLE_PATTERNS]

    rows = db.execute(
        "SELECT url FROM urls WHERE domain=? AND is_article=0 AND status='pending'",
        (domain,),
    ).fetchall()

    classified = 0
    kept = 0
    for (url,) in rows:
        path = urlparse(url).path

        # Skip obvious non-articles
        skip = False
        for pat in skip_res:
            if pat.search(url):
                skip = True
                break

        if skip:
            db.execute(
                "UPDATE urls SET is_article=0, status='skip', newspaper=? WHERE url=?",
                (newspaper_name, url),
            )
        else:
            # Check if it looks like an article
            is_art = False
            for pat in article_res:
                if pat.search(path):
                    is_art = True
                    break

            # Also keep if path has enough segments (likely an article slug)
            parts = [p for p in path.strip("/").split("/") if p]
            if not is_art and len(parts) >= 2 and len(parts[-1]) > 10:
                is_art = True

            if is_art:
                db.execute(
                    "UPDATE urls SET is_article=1, newspaper=? WHERE url=?",
                    (newspaper_name, url),
                )
                kept += 1
            else:
                db.execute(
                    "UPDATE urls SET is_article=0, status='skip', newspaper=? WHERE url=?",
                    (newspaper_name, url),
                )

        classified += 1
        if classified % 10000 == 0:
            db.commit()
            logger.info("Classified %d/%d URLs for %s (%d articles)", classified, len(rows), domain, kept)

    db.commit()

    # Update progress
    db.execute(
        "UPDATE cdx_progress SET article_urls=? WHERE domain=?",
        (kept, domain),
    )
    db.commit()
    logger.info("Classification complete for %s: %d articles / %d total", domain, kept, len(rows))
    return kept


# ---------------------------------------------------------------------------
# Phase 3: Fetch from Wayback
# ---------------------------------------------------------------------------

def fetch_wayback_article(url, timestamp):
    """Fetch an article from the Wayback Machine and extract text."""
    wb_url = f"{WAYBACK_PREFIX}/{timestamp}id_/{url}"
    try:
        req = urllib.request.Request(wb_url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None
            html = resp.read(500_000).decode("utf-8", errors="replace")

        # Strip Wayback toolbar injection
        html = re.sub(
            r"<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->",
            "", html, flags=re.DOTALL,
        )

        # Extract title
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            # Clean common suffixes
            for suffix in [" - Mail Tribune", " | Mail Tribune", " - Ashland Daily Tidings",
                           " | Ashland Daily Tidings", " | Ashland Tidings"]:
                if title.endswith(suffix):
                    title = title[: -len(suffix)].strip()
            title = title[:500]

        # Extract article text
        text = ""
        for tag in ("article", "main", '[class*="article"]', '[class*="story"]', "body"):
            # For CSS selector-style, fall back to simpler regex
            if tag.startswith("["):
                pattern = r'class="[^"]*(?:article|story)[^"]*"[^>]*>(.*?)</(?:div|section)'
                m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            else:
                m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
            if m:
                raw = m.group(1)
                raw = re.sub(r"<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", "", raw, flags=re.IGNORECASE | re.DOTALL)
                raw = re.sub(r"<[^>]+>", " ", raw)
                raw = re.sub(r"\s+", " ", raw).strip()
                if len(raw) > 200:
                    text = raw[:50000]
                    break

        if not text or len(text) < 100:
            return None

        # Extract published date
        published_at = ""
        for pattern in [
            r'property="article:published_time"\s+content="([^"]+)"',
            r'name="date"\s+content="([^"]+)"',
            r'"datePublished"\s*:\s*"([^"]+)"',
            r'class="[^"]*date[^"]*"[^>]*>([A-Z][a-z]+ \d{1,2},?\s*\d{4})',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                published_at = m.group(1).strip()[:30]
                break

        # Fall back to date from URL path (/2021/03/15/...)
        if not published_at:
            m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            if m:
                published_at = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        # Fall back to Wayback timestamp
        if not published_at and timestamp:
            try:
                published_at = datetime.strptime(timestamp[:8], "%Y%m%d").strftime("%Y-%m-%d")
            except Exception:
                pass

        # Extract author
        author = ""
        for pattern in [
            r'name="author"\s+content="([^"]+)"',
            r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
            r'class="[^"]*(?:author|byline)[^"]*"[^>]*>(?:By\s+)?([A-Z][a-z]+ [A-Z][a-z]+)',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                author = m.group(1).strip()[:200]
                break

        return {
            "title": title,
            "text": text,
            "published_at": published_at,
            "author": author,
        }

    except (HTTPError, URLError, TimeoutError):
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase 4: Index to ES
# ---------------------------------------------------------------------------

def normalize_date(date_str):
    """Convert various date formats to ISO 8601 for ES."""
    if not date_str:
        return ""
    date_str = date_str.strip()
    # Already ISO?
    if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
        return date_str
    # Common formats from newspaper HTML
    from datetime import datetime as dt
    for fmt in [
        "%B %d, %Y",          # March 15, 2021
        "%B %d,%Y",           # March 15,2021
        "%b %d, %Y",          # Mar 15, 2021
        "%b. %d, %Y",         # Mar. 15, 2021
        "%m/%d/%Y",           # 03/15/2021
        "%m-%d-%Y",           # 03-15-2021
        "%d %B %Y",           # 15 March 2021
        "%Y%m%d",             # 20210315
        "%B %d %Y",           # March 15 2021
    ]:
        try:
            return dt.strptime(date_str[:30], fmt).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            continue
    return ""  # Can't parse — omit rather than crash ES


def index_to_es(es, article_data):
    # Normalize date before indexing
    if article_data.get("published_at"):
        article_data["published_at"] = normalize_date(article_data["published_at"])
    if not article_data.get("published_at"):
        article_data.pop("published_at", None)
    doc_id = hashlib.md5(article_data["url"].encode()).hexdigest()
    try:
        es.index(index=ES_INDEX, id=doc_id, document=article_data)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Wayback Machine Newspaper Recovery")
    p.add_argument("--es-url", default="http://localhost:9201")
    p.add_argument("--state-db", default="/home/mark/wayback-newspapers.db")
    p.add_argument("--log", default="/home/mark/wayback-newspapers.log")
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--max-articles", type=int, default=0, help="Limit articles to fetch (0=all)")
    p.add_argument("--skip-cdx", action="store_true", help="Skip CDX dump (already done)")
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
    logger.info("=== Wayback Newspaper Crawler Starting ===")
    logger.info("ES: %s | State DB: %s | Workers: %d", args.es_url, args.state_db, args.workers)

    # Phase 1 + 2: CDX dump + classify for each newspaper
    for paper in NEWSPAPERS:
        domain = paper["domain"]
        name = paper["name"]

        if not args.skip_cdx:
            cdx_dump(domain, db, logger)

        classify_urls(domain, name, db, logger)

    # Stats
    for paper in NEWSPAPERS:
        total = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=?", (paper["domain"],)
        ).fetchone()[0]
        articles = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=? AND is_article=1",
            (paper["domain"],),
        ).fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=? AND is_article=1 AND status='pending'",
            (paper["domain"],),
        ).fetchone()[0]
        logger.info("%s: %d total URLs, %d articles, %d pending", paper["name"], total, articles, pending)

    # Phase 3 + 4: Fetch and index
    logger.info("=== Starting fetch phase ===")
    start_time = time.time()
    indexed = 0
    failed = 0
    skipped = 0

    # Build paper lookup
    paper_by_domain = {p["domain"]: p for p in NEWSPAPERS}

    while True:
        rows = db.execute(
            """SELECT url, domain, timestamp FROM urls
               WHERE is_article=1 AND status='pending'
               ORDER BY timestamp DESC LIMIT 200""",
        ).fetchall()
        if not rows:
            break

        if args.max_articles and indexed >= args.max_articles:
            logger.info("Reached max articles limit (%d)", args.max_articles)
            break

        def process_one(row):
            url, domain, timestamp = row
            return (url, domain, timestamp, fetch_wayback_article(url, timestamp))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_one, r): r for r in rows}

            for future in as_completed(futures):
                url, domain, timestamp, result = future.result()
                paper = paper_by_domain.get(domain, NEWSPAPERS[0])

                if result is None:
                    db.execute("UPDATE urls SET status='failed' WHERE url=?", (url,))
                    failed += 1
                else:
                    summary = " ".join(result["text"][:1000].split())[:500]

                    doc = {
                        "title": result["title"],
                        "content": result["text"],
                        "summary": summary,
                        "url": url,
                        "author": result.get("author", ""),
                        "source_name": paper["name"],
                        "source_credibility": paper["credibility"],
                        "category": paper["category"],
                        "published_at": result.get("published_at", ""),
                        "crawled_at": datetime.now(timezone.utc).isoformat(),
                        "tags": ["wayback-recovered", "local-news", "southern-oregon"],
                        "result_type": "article",
                    }

                    if index_to_es(es, doc):
                        db.execute(
                            "UPDATE urls SET status='done', title=?, content_length=?, published_at=?, fetched_at=? WHERE url=?",
                            (result["title"], len(result["text"]),
                             result.get("published_at", ""),
                             datetime.now(timezone.utc).isoformat(), url),
                        )
                        indexed += 1
                    else:
                        db.execute("UPDATE urls SET status='es_error' WHERE url=?", (url,))
                        failed += 1

                db.commit()
                total = indexed + failed
                if total % 100 == 0 and total > 0:
                    elapsed = time.time() - start_time
                    rate = indexed / elapsed * 3600 if elapsed else 0
                    pending = db.execute(
                        "SELECT COUNT(*) FROM urls WHERE is_article=1 AND status='pending'"
                    ).fetchone()[0]
                    logger.info(
                        "Progress: %d indexed, %d failed, %d pending, %.0f articles/hr",
                        indexed, failed, pending, rate,
                    )

                time.sleep(FETCH_DELAY_SEC)

    elapsed = time.time() - start_time
    logger.info(
        "=== Wayback Newspaper Crawler Done === indexed=%d failed=%d time=%.1f hrs",
        indexed, failed, elapsed / 3600,
    )

    # Final stats per paper
    for paper in NEWSPAPERS:
        done = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=? AND status='done'",
            (paper["domain"],),
        ).fetchone()[0]
        fail = db.execute(
            "SELECT COUNT(*) FROM urls WHERE domain=? AND status='failed'",
            (paper["domain"],),
        ).fetchone()[0]
        logger.info("%s: %d recovered, %d failed", paper["name"], done, fail)


if __name__ == "__main__":
    main()
