"""
Profoundd Domain Crawler — Proactive targeted web crawling.

Crawls configured domains comprehensively, extracts article content,
indexes to Elasticsearch, and discovers new domains via outbound links.

Usage:
    python scripts/domain_crawler.py --config scripts/domain_crawler_config.json \
        --es-url http://127.0.0.1:9201 --state-dir /home/mark/domain-crawler-state \
        --domains all --workers 4

    python scripts/domain_crawler.py --domains judicialwatch.org,unlimitedhangout.com \
        --max-pages 500

Design:
- One SQLite state DB per domain (resumable)
- Respects robots.txt via urllib.robotparser
- Rate limits per-domain (configurable, default 2s)
- Dedup via URL MD5 against ES
- Link discovery: outbound links increment counters in shared candidates DB
- Full-text extraction via BeautifulSoup (same pattern as feed_crawler)
"""
import argparse
import gzip
import hashlib
import json
import logging
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("domain_crawler")

ES_INDEX = "profoundd_articles"

# Skip recording these as candidate domains (noise / already decided not to crawl)
CANDIDATE_SKIP_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "linkedin.com", "pinterest.com", "reddit.com", "quora.com",
    "youtube.com", "youtu.be", "vimeo.com", "api.whatsapp.com", "whatsapp.com",
    "t.me", "telegram.me", "vk.com",
    "amazon.com", "ebay.com", "walmart.com", "etsy.com", "paypal.com",
    "patreon.com", "gofundme.com", "kickstarter.com",
    "wikipedia.org", "en.wikipedia.org",
    "google.com", "bing.com", "yahoo.com", "duckduckgo.com",
    "apple.com", "microsoft.com", "github.com",
    "archive.org", "web.archive.org",
}

def _should_record_candidate(domain):
    if not domain or "." not in domain:
        return False
    d = domain.lower().strip(".")
    if d in CANDIDATE_SKIP_DOMAINS:
        return False
    for skip in CANDIDATE_SKIP_DOMAINS:
        if d.endswith("." + skip):
            return False
    return True

# URL classification patterns (reuse from wayback_newspaper_crawler)
SKIP_PATH_PATTERNS = [
    re.compile(r"\.(css|js|json|xml|rss|atom|ico|svg|png|jpg|jpeg|gif|webp|pdf|zip|mp4|mp3)(\?|$)", re.I),
    re.compile(r"/(wp-admin|wp-content|wp-includes)/", re.I),
    re.compile(r"/(author|tag|category|search|login|register|cart|checkout|subscribe)/?(\?|$)", re.I),
    re.compile(r"#.*", re.I),
    re.compile(r"\?.*=", re.I),  # Skip most query-string pages
]

ARTICLE_PATH_HINTS = [
    re.compile(r"/\d{4}/\d{1,2}/\d{1,2}/", re.I),  # Date paths
    re.compile(r"/(news|article|story|blog|post|opinion|analysis|commentary)/[^/]+/?$", re.I),
    re.compile(r"/[a-z0-9-]{20,}/?$", re.I),  # Slug-like
]


def ua_headers(ua):
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()


def is_article_url(url, domain):
    """Heuristic: does this URL look like an article we want to index?"""
    parsed = urlparse(url)
    if parsed.netloc.replace("www.", "") != domain.replace("www.", ""):
        return False
    path = parsed.path
    if not path or path == "/":
        return False
    for pattern in SKIP_PATH_PATTERNS:
        if pattern.search(url):
            return False
    for pattern in ARTICLE_PATH_HINTS:
        if pattern.search(path):
            return True
    # Fallback: path must have 2+ segments with decent slug
    segments = [s for s in path.strip("/").split("/") if s]
    if len(segments) >= 2 and any(len(s) > 10 for s in segments):
        return True
    return False


# ---------------------------------------------------------------------------
# State DB (per-domain)
# ---------------------------------------------------------------------------

def init_state_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS urls (
        url TEXT PRIMARY KEY,
        depth INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        indexed_at TEXT,
        error TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON urls(status)")
    conn.commit()
    return conn


_cands_path = None  # set by init_candidates_db
_cands_lock = None


def init_candidates_db(db_path):
    """Init candidates DB. Each record_candidate() call opens its own
    connection for thread safety. Returns the path for later use."""
    global _cands_path, _cands_lock
    import threading
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS candidates (
        domain TEXT PRIMARY KEY,
        backlink_count INTEGER DEFAULT 0,
        first_seen TEXT,
        last_seen TEXT,
        status TEXT DEFAULT 'candidate',
        sample_urls TEXT
    )""")
    conn.commit()
    conn.close()
    _cands_path = str(db_path)
    _cands_lock = threading.Lock()
    return _cands_path


def record_candidate(_unused, domain, linking_url):
    """Thread-safe candidate recording. Opens own connection per-call."""
    if not _cands_path:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _cands_lock:
        conn = sqlite3.connect(_cands_path, timeout=10)
        try:
            row = conn.execute(
                "SELECT backlink_count, sample_urls FROM candidates WHERE domain=?",
                (domain,),
            ).fetchone()
            if row:
                count = row[0] + 1
                samples = json.loads(row[1] or "[]")
                if len(samples) < 5 and linking_url not in samples:
                    samples.append(linking_url)
                conn.execute(
                    "UPDATE candidates SET backlink_count=?, last_seen=?, sample_urls=? WHERE domain=?",
                    (count, now, json.dumps(samples), domain),
                )
            else:
                conn.execute(
                    "INSERT INTO candidates (domain, backlink_count, first_seen, last_seen, sample_urls) VALUES (?, 1, ?, ?, ?)",
                    (domain, now, now, json.dumps([linking_url])),
                )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

_robots_cache = {}

class SimpleRobots:
    """Minimal robots.txt parser — more lenient than urllib's.
    Default-allow unless explicit Disallow matches the path.
    """
    def __init__(self, text, ua):
        self.disallows = []
        self.allows = []
        ua_lower = ua.split("/")[0].lower()
        current_uas = []
        in_matching_block = False
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                current_uas = []
                in_matching_block = False
                continue
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "user-agent":
                current_uas.append(val.lower())
                in_matching_block = any(
                    u == "*" or ua_lower in u or u in ua_lower for u in current_uas
                )
            elif in_matching_block and key == "disallow":
                if val:
                    self.disallows.append(val)
            elif in_matching_block and key == "allow":
                if val:
                    self.allows.append(val)

    def can_fetch(self, url):
        path = urlparse(url).path or "/"
        # Allow rules take precedence when matched
        for a in self.allows:
            if path.startswith(a):
                return True
        for d in self.disallows:
            if path.startswith(d):
                return False
        return True


def get_robots(domain, ua):
    if domain in _robots_cache:
        return _robots_cache[domain]
    text = ""
    try:
        resp = requests.get(
            f"https://{domain}/robots.txt",
            headers=ua_headers(ua),
            timeout=10,
            allow_redirects=True,
        )
        if resp.status_code == 200:
            text = resp.text[:50000]
    except Exception as e:
        logger.debug("robots.txt fetch failed for %s: %s", domain, e)
    rp = SimpleRobots(text, ua)
    _robots_cache[domain] = rp
    return rp


def can_fetch(rp, ua, url):
    try:
        return rp.can_fetch(url)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Fetch + extract
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0"
)


def fetch_page(url, ua, timeout=15):
    """Fetch a URL. Falls back to a browser-style UA on 403/406/429 since
    several news sites (heritage.org, realclearpolitics, mercola, mailtribune,
    newsmax) hard-block any UA containing 'Bot'. We still send the bot UA
    first so well-behaved sites can identify us in their logs."""
    resp = requests.get(url, headers=ua_headers(ua), timeout=timeout, allow_redirects=True)
    if resp.status_code in (403, 406, 429):
        # One-shot retry with a Firefox-like UA
        resp = requests.get(
            url, headers=ua_headers(_BROWSER_UA),
            timeout=timeout, allow_redirects=True,
        )
    resp.raise_for_status()
    ct = resp.headers.get("Content-Type", "")
    if "html" not in ct.lower():
        return None
    return resp.text


def extract_links(soup, url, self_domain, seed_depth=False):
    """Extract internal article links and outbound domains from parsed HTML.

    seed_depth=True relaxes the internal-link filter so a homepage with
    non-article-shaped paths (e.g. /opinion vs /opinion/2026/05/04/headline)
    still feeds the crawl queue instead of stalling at one URL.
    """
    internal_links = set()
    outbound_domains = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:") or href.startswith("tel:"):
            continue
        try:
            abs_url = urljoin(url, href)
            parsed = urlparse(abs_url)
        except (ValueError, Exception):
            # urlparse raises ValueError on malformed IPv6-style brackets
            # ("Invalid IPv6 URL"); skip those hrefs entirely.
            continue
        if not parsed.netloc or parsed.scheme not in ("http", "https"):
            continue
        netloc = parsed.netloc.replace("www.", "")
        if netloc == self_domain or netloc.endswith("." + self_domain):
            clean = abs_url.split("#")[0]
            if any(skip.search(clean) for skip in SKIP_PATH_PATTERNS):
                continue
            if is_article_url(clean, self_domain):
                internal_links.add(clean)
            elif seed_depth:
                # On the homepage / seed page, queue every same-domain link
                # that isn't an asset or admin URL — many newsroom sites use
                # short slugs the article-pattern regex won't match.
                internal_links.add(clean)
            else:
                # Deeper levels: still crawl category/archive pages for discovery
                lower = clean.lower()
                if any(seg in lower for seg in ["/page/", "/archive", "/news", "/blog", "/opinion", "/article", "/story", "/post"]):
                    internal_links.add(clean)
        else:
            outbound_domains.add(netloc)
    return internal_links, outbound_domains


def extract_article(html, url, seed_depth=False):
    """Extract title, content, summary, and links from HTML.
    Returns a dict with links always populated; content fields may be empty
    if the page isn't an article (e.g. a category index).

    seed_depth=True relaxes link discovery for homepage / seed pages.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove junk
    for tag in soup(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
        tag.decompose()

    try:
        parsed_self = urlparse(url)
    except ValueError:
        return {
            "title": "", "content": "", "summary": "", "published_at": "",
            "internal_links": set(), "outbound_domains": set(),
            "is_article": False,
        }
    self_domain = parsed_self.netloc.replace("www.", "")

    # Always extract links (so category/archive pages can still feed crawl queue)
    internal_links, outbound_domains = extract_links(soup, url, self_domain, seed_depth=seed_depth)

    result = {
        "title": "",
        "content": "",
        "summary": "",
        "published_at": "",
        "internal_links": internal_links,
        "outbound_domains": outbound_domains,
        "is_article": False,
    }

    # Title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)
    result["title"] = title[:300]

    # Main content — try article/main first, fallback to body
    main = soup.find("article") or soup.find("main") or soup.body
    if not main:
        return result

    # Extract text from paragraphs
    paragraphs = [p.get_text(" ", strip=True) for p in main.find_all("p") if p.get_text(strip=True)]
    content = "\n".join(paragraphs)[:50000]

    if len(content) < 200:
        return result  # Not an article, but we still have links

    result["content"] = content
    result["summary"] = content[:500].rsplit(" ", 1)[0] + "..."
    result["is_article"] = True

    # Published date from meta tags
    for selector in [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"name": "article:published_time"}),
        ("meta", {"property": "og:published_time"}),
        ("meta", {"name": "pubdate"}),
        ("meta", {"name": "date"}),
        ("time", {"datetime": True}),
    ]:
        el = soup.find(*selector)
        if el:
            published = el.get("content") or el.get("datetime") or ""
            if published:
                result["published_at"] = published
                break

    return result


# ---------------------------------------------------------------------------
# ES indexing
# ---------------------------------------------------------------------------

def bulk_index_es(es_url, docs):
    if not docs:
        return 0
    payload = []
    for doc in docs:
        doc_id = url_hash(doc["url"])
        payload.append(json.dumps({"index": {"_index": ES_INDEX, "_id": doc_id}}))
        payload.append(json.dumps(doc))
    body = "\n".join(payload) + "\n"
    try:
        resp = requests.post(
            f"{es_url}/_bulk",
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=60,
        )
        if resp.status_code >= 400:
            logger.error("ES bulk error %s: %s", resp.status_code, resp.text[:300])
            return 0
        result = resp.json()
        successes = sum(
            1 for item in result.get("items", [])
            if item.get("index", {}).get("status", 500) in (200, 201)
        )
        return successes
    except Exception as e:
        logger.error("ES bulk failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Crawl a single domain
# ---------------------------------------------------------------------------

def crawl_domain(domain, config, defaults, state_dir, es_url, cands_conn, max_pages_override=None):
    cat = config.get("category", "news")
    credibility = config.get("credibility", 7)
    sponsors = config.get("sponsors", "")
    seeds = config.get("seeds", [f"https://{domain}/"])
    delay = float(config.get("delay_seconds", defaults["delay_seconds"]))
    max_pages = int(max_pages_override or config.get("max_pages_per_domain", defaults["max_pages_per_domain"]))
    max_depth = int(config.get("max_depth", defaults["max_depth"]))
    timeout = int(config.get("request_timeout", defaults["request_timeout"]))
    ua = defaults["user_agent"]

    db_path = Path(state_dir) / f"{domain}.db"
    conn = init_state_db(db_path)

    # Seed URLs — re-queue on every run so homepages get re-fetched and
    # newly-published articles get discovered via their internal links.
    # Without this, a domain stalls at 0 pending once it exhausts its queue.
    for seed in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO urls (url, depth, status) VALUES (?, 0, 'pending')",
            (seed,),
        )
        conn.execute(
            "UPDATE urls SET status='pending' WHERE url=? AND status NOT IN ('pending','fetching')",
            (seed,),
        )
    conn.commit()

    rp = get_robots(domain, ua) if defaults.get("respect_robots", True) else None
    indexed_count = 0
    fetched_count = 0
    batch = []
    start = time.time()

    logger.info("[%s] Starting crawl (max_pages=%d, delay=%.1fs)", domain, max_pages, delay)

    while fetched_count < max_pages:
        row = conn.execute(
            "SELECT url, depth FROM urls WHERE status='pending' ORDER BY depth ASC LIMIT 1"
        ).fetchone()
        if not row:
            break
        url, depth = row

        # Mark as in-progress immediately to avoid re-fetching
        conn.execute("UPDATE urls SET status='fetching' WHERE url=?", (url,))
        conn.commit()

        if rp and not can_fetch(rp, ua, url):
            conn.execute("UPDATE urls SET status='robots_disallowed' WHERE url=?", (url,))
            conn.commit()
            continue

        try:
            html = fetch_page(url, ua, timeout=timeout)
            fetched_count += 1
            if not html:
                conn.execute("UPDATE urls SET status='not_html' WHERE url=?", (url,))
                conn.commit()
                time.sleep(delay)
                continue

            result = extract_article(html, url, seed_depth=(depth == 0))

            # Record discovered outbound domains (always, even from index pages)
            for od in result["outbound_domains"]:
                if od == domain:
                    continue
                if not _should_record_candidate(od):
                    continue
                try:
                    record_candidate(None, od, url)
                except Exception as e:
                    logger.debug("candidate record error: %s", e)

            # Queue internal links (always, even if this page isn't an article)
            if depth < max_depth:
                for link in result["internal_links"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO urls (url, depth, status) VALUES (?, ?, 'pending')",
                        (link, depth + 1),
                    )
                conn.commit()

            # If not an article, mark and continue
            if not result["is_article"]:
                conn.execute("UPDATE urls SET status='not_article' WHERE url=?", (url,))
                conn.commit()
                time.sleep(delay)
                continue

            # Build ES doc
            doc = {
                "title": result["title"],
                "content": result["content"],
                "summary": result["summary"],
                "url": url,
                "source_name": domain,
                "source_credibility": credibility,
                "category": cat,
                "published_at": result["published_at"] or datetime.now(timezone.utc).isoformat(),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
                "tags": ["targeted-crawl", cat],
                "result_type": "article",
            }
            if sponsors:
                doc["source_sponsors"] = [sponsors]
            batch.append(doc)

            conn.execute(
                "UPDATE urls SET status='indexed', indexed_at=? WHERE url=?",
                (datetime.now(timezone.utc).isoformat(), url),
            )
            conn.commit()

            # Flush batch
            if len(batch) >= 50:
                n = bulk_index_es(es_url, batch)
                indexed_count += n
                logger.info("[%s] Indexed %d (total %d, fetched %d)", domain, n, indexed_count, fetched_count)
                batch = []

        except Exception as e:
            conn.execute(
                "UPDATE urls SET status='error', error=? WHERE url=?",
                (str(e)[:500], url),
            )
            conn.commit()
            logger.warning("[%s] Error on %s: %s", domain, url, e)

        time.sleep(delay)

    # Final flush
    if batch:
        indexed_count += bulk_index_es(es_url, batch)

    elapsed = time.time() - start
    logger.info("[%s] Done. Indexed %d articles in %.1f min (fetched %d)",
                domain, indexed_count, elapsed / 60, fetched_count)
    conn.close()
    return indexed_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="scripts/domain_crawler_config.json")
    parser.add_argument("--es-url", default="http://127.0.0.1:9201")
    parser.add_argument("--state-dir", default="domain-crawler-state")
    parser.add_argument("--domains", default="all", help="Comma-separated domains or 'all'")
    parser.add_argument("--workers", type=int, default=4, help="Parallel domains")
    parser.add_argument("--max-pages", type=int, default=None, help="Override per-domain max pages")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    defaults = cfg["defaults"]
    all_domains = cfg["domains"]

    if args.domains == "all":
        targets = list(all_domains.keys())
    else:
        targets = [d.strip() for d in args.domains.split(",")]
        targets = [d for d in targets if d in all_domains]

    if not targets:
        logger.error("No valid domains to crawl")
        sys.exit(1)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    cands_conn = init_candidates_db(state_dir / "candidates.db")

    logger.info("Crawling %d domain(s) with %d worker(s)", len(targets), args.workers)

    total_indexed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                crawl_domain,
                d, all_domains[d], defaults, state_dir, args.es_url, cands_conn, args.max_pages,
            ): d for d in targets
        }
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                n = fut.result()
                total_indexed += n
            except Exception as e:
                logger.error("[%s] Crawler crashed: %s", d, e)

    logger.info("ALL DONE. Total indexed: %d articles across %d domains", total_indexed, len(targets))
    cands_conn.close()


if __name__ == "__main__":
    main()
