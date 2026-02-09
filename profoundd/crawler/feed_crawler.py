"""
Multi-domain RSS/Atom feed crawler for Profoundd.
Fetches articles from configured sources and indexes them in Elasticsearch.
"""
import logging
import hashlib
from datetime import datetime, timezone
from time import sleep

import feedparser
import requests
from bs4 import BeautifulSoup

from profoundd.config.settings import get_config
from profoundd.config.sources import ALL_SOURCES
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

    def fetch_feed(self, source):
        """Fetch and parse an RSS/Atom feed."""
        try:
            response = self.session.get(source["url"], timeout=30)
            response.raise_for_status()
            feed = feedparser.parse(response.content)

            if feed.bozo and not feed.entries:
                logger.warning("Failed to parse feed: %s (%s)", source["name"], feed.bozo_exception)
                return []

            return feed.entries[:self.max_per_feed]
        except requests.RequestException as e:
            logger.error("Failed to fetch %s: %s", source["name"], e)
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

        return {
            "title": title,
            "url": link,
            "summary": summary,
            "content": content or summary,
            "author": author,
            "category": source["category"],
            "source_name": source["name"],
            "source_credibility": source.get("credibility", 5),
            "published_at": published.isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }

    def crawl_source(self, source):
        """Crawl a single source and return articles."""
        logger.info("Crawling: %s (%s)", source["name"], source["category"])
        entries = self.fetch_feed(source)
        articles = []

        for entry in entries:
            try:
                article = self.extract_article(entry, source)
                if article["url"] and article["title"]:
                    articles.append(article)
            except Exception as e:
                logger.error("Failed to extract article from %s: %s", source["name"], e)

        logger.info("Extracted %d articles from %s", len(articles), source["name"])
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
        total_articles = []

        for source in ALL_SOURCES:
            articles = self.crawl_source(source)
            total_articles.extend(articles)
            sleep(self.delay)

        # Bulk index everything
        if total_articles:
            indexed = self.search_engine.bulk_index(total_articles)
            logger.info("Full crawl complete: indexed %d/%d articles", indexed, len(total_articles))

        return len(total_articles)

    def crawl_custom_sources(self, sources):
        """Crawl a custom list of sources (from database)."""
        all_articles = []
        for source in sources:
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
            self.search_engine.bulk_index(all_articles)

        return len(all_articles)


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
