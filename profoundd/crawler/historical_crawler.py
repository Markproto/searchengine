"""
Historical crawler for Profoundd.
Discovers articles from a website's sitemap going back years,
extracts content, and indexes them into Elasticsearch.
"""
import logging
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from time import sleep, time
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from profoundd.config.settings import get_config
from profoundd.search.engine import SearchEngine

logger = logging.getLogger(__name__)
config = get_config()

# Sitemap XML namespaces
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class HistoricalCrawler:
    """Crawls a website's sitemap to discover and index historical articles."""

    def __init__(self, search_engine=None):
        self.search_engine = search_engine or SearchEngine(config.ELASTICSEARCH_URL)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self.delay = max(config.CRAWL_DELAY_SECONDS, 1)
        self._seen_urls = set()

        # Progress tracking (read by status endpoint)
        self.status = "idle"          # idle, discovering, crawling, done, error
        self.progress_message = ""
        self.sitemap_urls_found = 0
        self.urls_in_range = 0
        self.urls_processed = 0
        self.articles_indexed = 0
        self.articles_skipped = 0
        self.errors = 0

    def _fetch(self, url, timeout=30):
        """Fetch a URL with retry logic."""
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=timeout)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt < 2:
                    sleep(2 ** attempt)
                    continue
                logger.error("Failed to fetch %s: %s", url, e)
                return None

    def discover_sitemaps(self, base_url):
        """Find all sitemap URLs for a website."""
        parsed = urlparse(base_url)
        domain_root = f"{parsed.scheme}://{parsed.netloc}"
        sitemaps = []

        self.status = "discovering"
        self.progress_message = "Looking for sitemaps..."

        # 1. Check robots.txt for sitemap references
        robots_url = f"{domain_root}/robots.txt"
        resp = self._fetch(robots_url)
        if resp:
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith("sitemap:"):
                    sm_url = line.split(":", 1)[1].strip()
                    if sm_url and sm_url not in sitemaps:
                        sitemaps.append(sm_url)

        # 2. Try common sitemap locations
        common_paths = [
            "/sitemap.xml",
            "/sitemap_index.xml",
            "/sitemap-index.xml",
            "/sitemaps.xml",
            "/sitemap/sitemap.xml",
            "/news-sitemap.xml",
            "/post-sitemap.xml",
        ]
        for path in common_paths:
            url = domain_root + path
            if url not in sitemaps:
                resp = self._fetch(url, timeout=15)
                if resp and ("<?xml" in resp.text[:200] or "<urlset" in resp.text[:500]
                             or "<sitemapindex" in resp.text[:500]):
                    sitemaps.append(url)

        logger.info("Found %d sitemap(s) for %s", len(sitemaps), domain_root)
        return sitemaps

    def parse_sitemap(self, sitemap_url, date_from=None, date_to=None):
        """
        Parse a sitemap or sitemap index and return URLs within the date range.
        Returns list of dicts: [{"url": ..., "lastmod": ...}, ...]
        """
        resp = self._fetch(sitemap_url)
        if not resp:
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            logger.warning("Could not parse XML from %s", sitemap_url)
            return []

        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        # Sitemap index → recurse into child sitemaps
        if tag == "sitemapindex":
            all_urls = []
            for sitemap_el in root.findall("sm:sitemap", SITEMAP_NS):
                loc = sitemap_el.find("sm:loc", SITEMAP_NS)
                if loc is None or not loc.text:
                    continue
                child_url = loc.text.strip()

                # Check lastmod on the child sitemap itself for quick filtering
                lastmod_el = sitemap_el.find("sm:lastmod", SITEMAP_NS)
                if lastmod_el is not None and lastmod_el.text:
                    child_date = self._parse_date(lastmod_el.text)
                    if child_date:
                        if date_from and child_date < date_from:
                            continue
                        if date_to and child_date > date_to:
                            continue

                self.progress_message = f"Parsing sitemap: {child_url}"
                child_urls = self.parse_sitemap(child_url, date_from, date_to)
                all_urls.extend(child_urls)
                sleep(0.5)  # Be polite when fetching many sitemaps
            return all_urls

        # Regular urlset
        urls = []
        for url_el in root.findall("sm:url", SITEMAP_NS):
            loc = url_el.find("sm:loc", SITEMAP_NS)
            if loc is None or not loc.text:
                continue

            page_url = loc.text.strip()
            lastmod = None
            lastmod_el = url_el.find("sm:lastmod", SITEMAP_NS)
            if lastmod_el is not None and lastmod_el.text:
                lastmod = self._parse_date(lastmod_el.text)

            # Filter by date range
            if lastmod:
                if date_from and lastmod < date_from:
                    continue
                if date_to and lastmod > date_to:
                    continue

            self.sitemap_urls_found += 1
            urls.append({"url": page_url, "lastmod": lastmod})

        return urls

    def _parse_date(self, date_str):
        """Parse various date formats from sitemaps."""
        date_str = date_str.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    def extract_article(self, url, html, source_info):
        """Extract article data from an HTML page."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                                   "aside", "iframe", "noscript"]):
            tag.decompose()

        # Title: og:title > <title> > h1
        title = ""
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        if not title:
            title_tag = soup.find("title")
            if title_tag:
                title = title_tag.get_text(strip=True)
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not title:
            return None

        # Description / summary: og:description > meta description
        summary = ""
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            summary = og_desc["content"].strip()
        if not summary:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                summary = meta_desc["content"].strip()

        # Content: article tag > main tag > body
        content = ""
        article_el = soup.find("article")
        if article_el:
            content = article_el.get_text(separator=" ", strip=True)
        if not content:
            main_el = soup.find("main")
            if main_el:
                content = main_el.get_text(separator=" ", strip=True)
        if not content:
            body_el = soup.find("body")
            if body_el:
                content = body_el.get_text(separator=" ", strip=True)

        # Truncate content to avoid massive documents
        if len(content) > 10000:
            content = content[:10000]
        if len(summary) > 1000:
            summary = summary[:997] + "..."
        if not summary and content:
            summary = content[:500]

        # Author
        author = source_info.get("name", "Unknown")
        author_meta = soup.find("meta", attrs={"name": "author"})
        if author_meta and author_meta.get("content"):
            author = author_meta["content"].strip()

        # Published date: article:published_time > datePublished schema > lastmod
        published_at = None
        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta and pub_meta.get("content"):
            published_at = self._parse_date(pub_meta["content"])
        if not published_at:
            # Try JSON-LD datePublished
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    import json
                    data = json.loads(script.string or "")
                    if isinstance(data, dict) and "datePublished" in data:
                        published_at = self._parse_date(data["datePublished"])
                        break
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "datePublished" in item:
                                published_at = self._parse_date(item["datePublished"])
                                break
                except (json.JSONDecodeError, TypeError):
                    pass

        if not published_at:
            published_at = datetime.now(timezone.utc)

        # Tags from keywords meta
        tags = []
        kw_meta = soup.find("meta", attrs={"name": "keywords"})
        if kw_meta and kw_meta.get("content"):
            tags = [t.strip() for t in kw_meta["content"].split(",") if t.strip()][:5]

        return {
            "title": title,
            "url": url,
            "summary": summary,
            "content": content or summary,
            "author": author,
            "category": source_info.get("category", "news"),
            "source_name": source_info.get("name", "Unknown"),
            "source_credibility": source_info.get("credibility", 5),
            "published_at": published_at.isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "tags": tags,
        }

    def crawl(self, source_info, date_from, date_to, max_pages=500):
        """
        Run a full historical crawl for a source.

        Args:
            source_info: dict with keys name, url, category, credibility
            date_from: datetime - start of date range
            date_to: datetime - end of date range
            max_pages: int - maximum number of pages to crawl
        """
        start_time = time()
        base_url = source_info["url"]

        # Derive the website root from the feed URL
        parsed = urlparse(base_url)
        site_root = f"{parsed.scheme}://{parsed.netloc}"

        logger.info("Starting historical crawl for %s (%s to %s, max %d pages)",
                     source_info["name"], date_from.date(), date_to.date(), max_pages)

        # Step 1: Discover sitemaps
        sitemaps = self.discover_sitemaps(site_root)
        if not sitemaps:
            self.status = "error"
            self.progress_message = f"No sitemaps found for {site_root}"
            logger.warning(self.progress_message)
            return 0

        # Step 2: Parse sitemaps and collect URLs in date range
        self.progress_message = "Parsing sitemaps for URLs in date range..."
        all_urls = []
        for sm_url in sitemaps:
            urls = self.parse_sitemap(sm_url, date_from, date_to)
            all_urls.extend(urls)

        # Deduplicate by URL
        seen = set()
        unique_urls = []
        for entry in all_urls:
            if entry["url"] not in seen:
                seen.add(entry["url"])
                unique_urls.append(entry)

        # Limit to max_pages
        unique_urls = unique_urls[:max_pages]
        self.urls_in_range = len(unique_urls)

        logger.info("Found %d URLs in date range (sitemap total: %d)",
                     len(unique_urls), self.sitemap_urls_found)

        if not unique_urls:
            self.status = "done"
            self.progress_message = "No URLs found in the specified date range."
            return 0

        # Step 3: Crawl each URL
        self.status = "crawling"
        articles_batch = []
        batch_size = 25

        for i, entry in enumerate(unique_urls):
            url = entry["url"]
            self.urls_processed = i + 1
            self.progress_message = f"Crawling {i + 1}/{len(unique_urls)}: {url[:80]}"

            # Skip if already indexed
            if self.search_engine.article_exists(url):
                self.articles_skipped += 1
                continue

            resp = self._fetch(url)
            if not resp:
                self.errors += 1
                continue

            # Skip non-HTML responses
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                continue

            try:
                article = self.extract_article(url, resp.text, source_info)
                if article and article["title"]:
                    # Use lastmod from sitemap if we didn't find a date in the page
                    if entry.get("lastmod") and article["published_at"]:
                        lm = entry["lastmod"]
                        article_dt = self._parse_date(article["published_at"])
                        # If page didn't have a real date, use sitemap lastmod
                        if not article_dt or abs((article_dt - datetime.now(timezone.utc)).days) < 1:
                            article["published_at"] = lm.isoformat()

                    articles_batch.append(article)
                    self.articles_indexed += 1
            except Exception as e:
                logger.error("Error extracting %s: %s", url, e)
                self.errors += 1

            # Bulk index in batches
            if len(articles_batch) >= batch_size:
                self.search_engine.bulk_index(articles_batch)
                articles_batch = []

            sleep(self.delay)

        # Index remaining articles
        if articles_batch:
            self.search_engine.bulk_index(articles_batch)

        duration = time() - start_time
        self.status = "done"
        self.progress_message = (
            f"Done! Indexed {self.articles_indexed} articles in {duration:.0f}s "
            f"({self.articles_skipped} duplicates skipped, {self.errors} errors)"
        )
        logger.info("Historical crawl for %s complete: %s", source_info["name"], self.progress_message)
        return self.articles_indexed

    def get_progress(self):
        """Return current progress as a dict."""
        return {
            "status": self.status,
            "message": self.progress_message,
            "sitemap_urls_found": self.sitemap_urls_found,
            "urls_in_range": self.urls_in_range,
            "urls_processed": self.urls_processed,
            "articles_indexed": self.articles_indexed,
            "articles_skipped": self.articles_skipped,
            "errors": self.errors,
        }
