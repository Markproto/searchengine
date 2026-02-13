"""
Multi-domain RSS/Atom feed crawler for Profoundd.
Fetches articles from configured sources and indexes them in Elasticsearch.
Includes URL-based deduplication and crawl statistics.
"""
import logging
import hashlib
from datetime import datetime, timezone
from time import sleep, time

import feedparser
import requests
from bs4 import BeautifulSoup

from profoundd.config.settings import get_config
from profoundd.config.sources import ALL_SOURCES, SPECIAL_SECTION_KEYWORDS
from profoundd.search.engine import SearchEngine

logger = logging.getLogger(__name__)
config = get_config()


class FeedCrawler:
    """Crawls RSS/Atom feeds and indexes articles."""

    def __init__(self, search_engine=None, db_session=None):
        self.search_engine = search_engine or SearchEngine(config.ELASTICSEARCH_URL)
        self.db_session = db_session
        self.user_agent = config.USER_AGENT
        self.delay = config.CRAWL_DELAY_SECONDS
        self.max_per_feed = config.MAX_ARTICLES_PER_FEED
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        # Deduplication: track seen URLs within a crawl run
        self._seen_urls = set()
        # Statistics
        self.stats = {"found": 0, "new": 0, "duplicate": 0, "errors": 0}

    @staticmethod
    def _match_special_category(title, summary, content="", tags=None):
        """Check if article text matches any special section keywords.

        Returns the special category key if matched, otherwise None.
        Checks title, summary, content, and tags for keyword matches.
        """
        tag_text = " ".join(tags) if tags else ""
        text = f"{title} {summary} {content} {tag_text}".lower()
        for cat_key, keywords in SPECIAL_SECTION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return cat_key
        return None

    def _is_duplicate(self, url):
        """Check if URL has already been seen in this crawl run or exists in ES."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        if url_hash in self._seen_urls:
            return True
        self._seen_urls.add(url_hash)
        # Also check Elasticsearch — skip articles already indexed
        if self.search_engine.article_exists(url):
            return True
        return False

    def fetch_feed(self, source):
        """Fetch and parse an RSS/Atom feed with retry."""
        for attempt in range(3):
            try:
                response = self.session.get(source["url"], timeout=30)
                response.raise_for_status()
                feed = feedparser.parse(response.content)

                if feed.bozo and not feed.entries:
                    logger.warning("Failed to parse feed: %s (%s)", source["name"], feed.bozo_exception)
                    return []

                return feed.entries[:self.max_per_feed]
            except requests.RequestException as e:
                if attempt < 2:
                    sleep(2 ** attempt)
                    continue
                logger.error("Failed to fetch %s after %d attempts: %s", source["name"], attempt + 1, e)
                self.stats["errors"] += 1
                return []

    def extract_article(self, entry, source):
        """Extract article data from a feed entry."""
        # Get the best available date
        published = None
        for date_field in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, date_field, None)
            if parsed:
                try:
                    published = datetime(*parsed[:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    pass
                break

        if not published:
            published = datetime.now(timezone.utc)

        # Get summary/description
        summary = ""
        if hasattr(entry, "summary"):
            summary = BeautifulSoup(entry.summary, "html.parser").get_text(strip=True)
        elif hasattr(entry, "description"):
            summary = BeautifulSoup(entry.description, "html.parser").get_text(strip=True)

        # Truncate summary
        if len(summary) > 1000:
            summary = summary[:997] + "..."

        # Get content if available
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = BeautifulSoup(entry.content[0].get("value", ""), "html.parser").get_text(strip=True)

        # Get link
        link = getattr(entry, "link", "")
        if not link and hasattr(entry, "links") and entry.links:
            link = entry.links[0].get("href", "")

        title = getattr(entry, "title", "Untitled")
        title = BeautifulSoup(title, "html.parser").get_text(strip=True)

        author = getattr(entry, "author", source["name"])

        # Extract tags/keywords if available
        tags = []
        if hasattr(entry, "tags") and entry.tags:
            tags = [t.get("term", "") for t in entry.tags[:5]]

        # Determine category — check for special section keyword matches.
        # For articles from any feed, if keywords match a special section, re-categorize.
        # _matched_special is also used by crawl_source to filter out non-matching
        # articles from feeds assigned to special categories.
        matched_special = self._match_special_category(title, summary, content, tags)
        category = matched_special or source["category"]

        return {
            "title": title,
            "url": link,
            "summary": summary,
            "content": content or summary,
            "author": author,
            "category": category,
            "source_name": source["name"],
            "source_credibility": source.get("credibility", 5),
            "published_at": published.isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "tags": tags,
        }

    def crawl_source(self, source):
        """Crawl a single source and return deduplicated articles."""
        logger.info("Crawling: %s (%s)", source["name"], source["category"])
        entries = self.fetch_feed(source)
        articles = []
        # If this source is assigned to a special category, only keep articles
        # that actually match the keywords — otherwise the whole general feed
        # (e.g. Daily Mail) would flood the special section with irrelevant stories.
        source_is_special = source["category"] in SPECIAL_SECTION_KEYWORDS

        for entry in entries:
            try:
                article = self.extract_article(entry, source)
                if not article["url"] or not article["title"]:
                    continue

                self.stats["found"] += 1

                # For special-category sources, only keep articles whose keywords
                # actually matched.  When no keyword matches, extract_article falls
                # back to source["category"] — but that would flood the section with
                # unrelated articles from general feeds like Daily Mail.
                if source_is_special:
                    keyword_matched = self._match_special_category(
                        article["title"], article["summary"],
                        article.get("content", ""), article.get("tags", [])
                    )
                    if not keyword_matched:
                        continue

                if self._is_duplicate(article["url"]):
                    self.stats["duplicate"] += 1
                    continue

                self.stats["new"] += 1
                articles.append(article)
            except Exception as e:
                logger.error("Failed to extract article from %s: %s", source["name"], e)
                self.stats["errors"] += 1

        logger.info("Extracted %d articles from %s (%d skipped as duplicates)",
                     len(articles), source["name"],
                     self.stats["duplicate"])
        return articles

    def crawl_category(self, category):
        """Crawl all sources in a specific category."""
        sources = [s for s in ALL_SOURCES if s["category"] == category]
        all_articles = []

        for source in sources:
            articles = self.crawl_source(source)
            all_articles.extend(articles)
            sleep(self.delay)  # Be polite

        # Bulk index
        if all_articles:
            indexed = self.search_engine.bulk_index(all_articles)
            logger.info("Category '%s': indexed %d articles", category, indexed)

        return all_articles

    def crawl_all(self):
        """Crawl all configured sources."""
        logger.info("Starting full crawl of %d sources", len(ALL_SOURCES))
        start_time = time()
        total_articles = []

        for source in ALL_SOURCES:
            articles = self.crawl_source(source)
            total_articles.extend(articles)
            sleep(self.delay)

        # Bulk index everything
        if total_articles:
            indexed = self.search_engine.bulk_index(total_articles)
            duration = time() - start_time
            logger.info("Full crawl complete in %.1fs: indexed %d/%d articles (dupes: %d, errors: %d)",
                         duration, indexed, len(total_articles),
                         self.stats["duplicate"], self.stats["errors"])

        return len(total_articles)

    def crawl_custom_sources(self, sources):
        """Crawl a custom list of sources (from database)."""
        source_list = list(sources)
        logger.info("crawl_custom_sources called with %d sources", len(source_list))
        if not source_list:
            logger.warning("No sources to crawl — check that sources are seeded and active")
            return 0
        all_articles = []
        for source in source_list:
            source_dict = {
                "name": source.name,
                "url": source.url,
                "category": source.category,
                "credibility": source.credibility,
                "feed_type": source.feed_type,
            }
            articles = self.crawl_source(source_dict)
            all_articles.extend(articles)
            sleep(self.delay)

        if all_articles:
            indexed = self.search_engine.bulk_index(all_articles)
            logger.info("Custom crawl: indexed %d new articles (found %d, dupes %d, errors %d)",
                        indexed, self.stats["found"], self.stats["duplicate"], self.stats["errors"])
        else:
            logger.info("Custom crawl: no new articles (found %d, dupes %d, errors %d)",
                        self.stats["found"], self.stats["duplicate"], self.stats["errors"])

        return len(all_articles)

    def get_stats(self):
        """Return crawl statistics."""
        return dict(self.stats)


def run_crawl(category=None):
    """Entry point for running a crawl (used by scheduler/cron)."""
    logging.basicConfig(level=logging.INFO)
    crawler = FeedCrawler()

    # Ensure index exists
    if crawler.search_engine.is_available():
        crawler.search_engine.create_index()
    else:
        logger.error("Elasticsearch is not available!")
        return 0

    if category:
        articles = crawler.crawl_category(category)
        return len(articles)
    else:
        return crawler.crawl_all()


if __name__ == "__main__":
    import sys
    cat = sys.argv[1] if len(sys.argv) > 1 else None
    count = run_crawl(category=cat)
    print(f"Crawled {count} articles")
