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

# Patterns that indicate an entry is an ad / sponsored content.
# Checked case-insensitively against the title and summary.
AD_FILTER_PATTERNS = [
    "#ad",
    "#sponsored",
    "sponsored by",
    "brought to you by",
    "paid promotion",
    "paid partnership",
    "this video is sponsored",
    "thanks to our sponsor",
    "use code ",
    "use my code",
    "use my link",
    "check out our sponsor",
    "affiliate link",
    "promo code",
]


class FeedCrawler:
    """Crawls RSS/Atom feeds and indexes articles."""

    # Minimum content length (chars) before we try full-text extraction
    MIN_CONTENT_LENGTH = 200

    def __init__(self, search_engine=None, db_session=None):
        self.search_engine = search_engine or SearchEngine(config.ELASTICSEARCH_URL)
        self.db_session = db_session
        self.user_agent = config.USER_AGENT
        self.delay = config.CRAWL_DELAY_SECONDS
        self.max_per_feed = config.MAX_ARTICLES_PER_FEED
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        # Deduplication: track seen URLs and titles within a crawl run
        self._seen_urls = set()
        self._seen_titles = set()
        # Statistics
        self.stats = {"found": 0, "new": 0, "duplicate": 0, "errors": 0, "full_text": 0}

    @staticmethod
    def _is_ad_content(title, summary=""):
        """Return True if the title or summary matches known ad/sponsored patterns."""
        text = f"{title} {summary}".lower()
        return any(pattern in text for pattern in AD_FILTER_PATTERNS)

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

    def _is_title_duplicate(self, title):
        """Check if an article with this title was already seen or indexed.

        First-come-first-serve: the first source to publish a headline wins.
        Later sources with the same headline are skipped entirely.
        """
        normalized = title.strip().lower()
        if normalized in self._seen_titles:
            return True
        # Check ES for articles indexed in previous crawl runs
        if self.search_engine.title_exists(title):
            self._seen_titles.add(normalized)
            return True
        self._seen_titles.add(normalized)
        return False

    def fetch_full_text(self, url, max_chars=5000):
        """
        Fetch the full article text from a URL when the RSS feed only
        provides a short summary/teaser. Extracts the main article content
        from the HTML, skipping nav bars, ads, footers, etc.
        """
        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text[:300000], "lxml")

            # Remove non-content elements
            for tag in soup(["script", "style", "nav", "footer", "header",
                             "aside", "iframe", "form", "noscript",
                             "figure", "figcaption"]):
                tag.decompose()

            # Remove common ad/sidebar class patterns
            for el in soup.find_all(class_=lambda c: c and any(
                x in str(c).lower() for x in [
                    "sidebar", "comment", "social", "share", "related",
                    "newsletter", "subscribe", "popup", "modal", "cookie",
                    "promo", "advert", "sponsor", "widget", "menu",
                ])):
                el.decompose()

            # Try to find the main article content using common selectors
            article_text = ""
            for selector in [
                "article",
                '[role="main"]',
                ".article-body",
                ".article-content",
                ".post-content",
                ".entry-content",
                ".story-body",
                ".story-content",
                ".content-body",
                ".field-body",
                "main",
                "#article-body",
                "#content",
            ]:
                found = soup.select_one(selector)
                if found:
                    text = found.get_text(separator="\n", strip=True)
                    if len(text) > len(article_text):
                        article_text = text

            # Fallback: use the largest block of text from <p> tags
            if len(article_text) < self.MIN_CONTENT_LENGTH:
                paragraphs = soup.find_all("p")
                p_text = "\n".join(p.get_text(strip=True) for p in paragraphs
                                   if len(p.get_text(strip=True)) > 30)
                if len(p_text) > len(article_text):
                    article_text = p_text

            # Truncate to max_chars
            if len(article_text) > max_chars:
                article_text = article_text[:max_chars] + "..."

            return article_text.strip()

        except Exception as e:
            logger.debug("Full-text fetch failed for %s: %s", url, e)
            return ""

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

        # Extract image URL from feed metadata (no images stored on server)
        image_url = ""
        # 1. media:content (most common in RSS)
        if hasattr(entry, "media_content") and entry.media_content:
            for mc in entry.media_content:
                if mc.get("medium") == "image" or (mc.get("type", "").startswith("image")):
                    image_url = mc.get("url", "")
                    break
            if not image_url:
                image_url = entry.media_content[0].get("url", "")
        # 2. media:thumbnail
        if not image_url and hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
            image_url = entry.media_thumbnail[0].get("url", "")
        # 3. enclosures (podcasts/media feeds)
        if not image_url and hasattr(entry, "enclosures") and entry.enclosures:
            for enc in entry.enclosures:
                if enc.get("type", "").startswith("image"):
                    image_url = enc.get("href", "") or enc.get("url", "")
                    break
        # 4. <image> tag in entry or feed-level image in summary/content HTML
        if not image_url:
            html_to_check = getattr(entry, "summary", "") or ""
            if html_to_check:
                soup = BeautifulSoup(html_to_check, "html.parser")
                img_tag = soup.find("img", src=True)
                if img_tag and img_tag["src"].startswith("http"):
                    image_url = img_tag["src"]

        # Extract tags/keywords if available
        tags = []
        if hasattr(entry, "tags") and entry.tags:
            tags = [t.get("term", "") for t in entry.tags[:5]]

        # If summary and content are too short, fetch the full article text
        best_text = content or summary
        if len(best_text) < self.MIN_CONTENT_LENGTH and link:
            full_text = self.fetch_full_text(link)
            if full_text and len(full_text) > len(best_text):
                content = full_text
                # Also use a better summary if the feed one was too short
                if len(summary) < 100:
                    summary = full_text[:500].rsplit(" ", 1)[0] + "..." if len(full_text) > 500 else full_text
                self.stats["full_text"] = self.stats.get("full_text", 0) + 1
                logger.debug("Full-text extracted for: %s (%d chars)", title[:50], len(content))

        # Determine category — check for special section keyword matches.
        # For articles from any feed, if keywords match a special section, re-categorize.
        # _matched_special is also used by crawl_source to filter out non-matching
        # articles from feeds assigned to special categories.
        matched_special = self._match_special_category(title, summary, content, tags)
        category = matched_special or source["category"]

        # Sponsor disclosure tags (e.g. "Pfizer")
        sponsors = source.get("sponsors", "")
        source_sponsors = [s.strip() for s in sponsors.split(",") if s.strip()] if sponsors else []

        # Subcategory (used by legislative section for finer classification)
        subcategory = source.get("subcategory", "")

        return {
            "title": title,
            "url": link,
            "summary": summary,
            "content": content or summary,
            "author": author,
            "category": category,
            "subcategory": subcategory,
            "source_name": source["name"],
            "source_credibility": source.get("credibility", 5),
            "source_sponsors": source_sponsors,
            "image_url": image_url,
            "published_at": published.isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "tags": tags,
            "_feed_url": source.get("url", ""),
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

                # Skip ads / sponsored content (Redacted YouTube feed only)
                if source["name"] == "Redacted" and self._is_ad_content(article["title"], article["summary"]):
                    logger.debug("Skipping ad content from Redacted: %s", article["title"])
                    continue

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

                if self._is_title_duplicate(article["title"]):
                    self.stats["duplicate"] += 1
                    logger.debug("Title duplicate skipped: '%s' from %s",
                                 article["title"], article["source_name"])
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

    @staticmethod
    def _make_youtube_copies(articles):
        """Create copies of YouTube-sourced articles for the youtube category.

        Articles from YouTube feeds already live in their primary category
        (e.g. politics). This creates a duplicate with category='youtube'
        and a unique URL suffix so both copies coexist in ES.

        Detection: checks article URL for youtube.com/watch or youtu.be,
        AND the feed URL for youtube.com/feeds (catches Atom channel feeds).
        """
        copies = []
        for a in articles:
            url = a.get("url", "")
            feed_url = a.get("_feed_url", "")
            if a.get("category") == "youtube":
                continue  # already in youtube, skip
            is_yt = ("youtube.com/watch" in url or "youtu.be/" in url
                     or "youtube.com/feeds" in feed_url)
            if is_yt:
                copy = dict(a)
                copy["category"] = "youtube"
                copy["url"] = url + "#yt-section"
                copy.pop("subcategory", None)
                copies.append(copy)
        return copies

    @staticmethod
    def _make_rumble_copies(articles):
        """Create copies of Rumble-sourced articles for the rumble category.

        Articles from Rumble feeds already live in their primary category
        (e.g. politics). This creates a duplicate with category='rumble'
        and a unique URL suffix so both copies coexist in ES.

        Detection: checks article URL for rumble.com AND the feed URL
        for openrss.org/rumble (catches OpenRSS-proxied Rumble feeds).
        """
        copies = []
        for a in articles:
            url = a.get("url", "")
            feed_url = a.get("_feed_url", "")
            if a.get("category") == "rumble":
                continue  # already in rumble, skip
            is_rumble = ("rumble.com/" in url
                         or "rumble.com" in feed_url)
            if is_rumble:
                copy = dict(a)
                copy["category"] = "rumble"
                copy["url"] = url + "#rumble-section"
                copy.pop("subcategory", None)
                copies.append(copy)
        return copies

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

            # Cross-post YouTube articles into the youtube category
            yt_copies = self._make_youtube_copies(all_articles)
            if yt_copies:
                yt_indexed = self.search_engine.bulk_index(yt_copies)
                logger.info("YouTube cross-post from '%s': indexed %d copies", category, yt_indexed)

            # Cross-post Rumble articles into the rumble category
            rumble_copies = self._make_rumble_copies(all_articles)
            if rumble_copies:
                rb_indexed = self.search_engine.bulk_index(rumble_copies)
                logger.info("Rumble cross-post from '%s': indexed %d copies", category, rb_indexed)

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

            # Cross-post YouTube articles into the 'youtube' category
            yt_copies = self._make_youtube_copies(total_articles)
            if yt_copies:
                yt_indexed = self.search_engine.bulk_index(yt_copies)
                logger.info("YouTube cross-post: indexed %d copies", yt_indexed)

            # Cross-post Rumble articles into the 'rumble' category
            rumble_copies = self._make_rumble_copies(total_articles)
            if rumble_copies:
                rb_indexed = self.search_engine.bulk_index(rumble_copies)
                logger.info("Rumble cross-post: indexed %d copies", rb_indexed)

            duration = time() - start_time
            logger.info("Full crawl complete in %.1fs: indexed %d/%d articles (dupes: %d, full-text: %d, errors: %d)",
                         duration, indexed, len(total_articles),
                         self.stats["duplicate"], self.stats.get("full_text", 0), self.stats["errors"])

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
                "sponsors": getattr(source, "sponsor_tags", "") or "",
                "subcategory": getattr(source, "subcategory", "") or "",
            }
            articles = self.crawl_source(source_dict)
            all_articles.extend(articles)
            sleep(self.delay)

        if all_articles:
            indexed = self.search_engine.bulk_index(all_articles)

            # Cross-post YouTube articles into youtube category
            yt_copies = self._make_youtube_copies(all_articles)
            if yt_copies:
                yt_indexed = self.search_engine.bulk_index(yt_copies)
                logger.info("YouTube cross-post (custom): indexed %d copies", yt_indexed)

            # Cross-post Rumble articles into rumble category
            rumble_copies = self._make_rumble_copies(all_articles)
            if rumble_copies:
                rb_indexed = self.search_engine.bulk_index(rumble_copies)
                logger.info("Rumble cross-post (custom): indexed %d copies", rb_indexed)

            logger.info("Custom crawl: indexed %d new articles (found %d, dupes %d, full-text %d, errors %d)",
                        indexed, self.stats["found"], self.stats["duplicate"],
                        self.stats.get("full_text", 0), self.stats["errors"])
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
