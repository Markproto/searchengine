"""
Cache warmer — pre-caches the top ~100 searches from multiple sources.
Run via cron 2-3x daily to keep popular queries instant.

Sources:
  1. Our own SearchLog (top queries from past 7 days)
  2. Google Trends RSS (daily US trending searches, free, no auth)
  3. Hot topics from our own Elasticsearch article index (recent headlines)
  4. Evergreen queries (always-relevant topics for our audience)

Usage:
  docker exec profoundd python -m profoundd.search.cache_warmer
"""
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

# Topics our libertarian/conservative audience always searches for
EVERGREEN_QUERIES = [
    "trump", "tariffs", "gold prices", "silver prices", "bitcoin",
    "second amendment", "gun rights", "federal reserve",
    "inflation", "border security", "immigration",
    "supreme court", "congress", "executive orders",
    "oregon politics", "oregon news", "oregon legislature",
    "vaccine", "free speech", "election",
    "epstein", "deep state", "government spending",
    "crypto", "economy", "stock market",
    "polymarket", "prediction markets",
]

# Filter out sports/celebrity/entertainment trends that don't match our niche
SKIP_PATTERNS = re.compile(
    r"\b(nfl|nba|mlb|nhl|soccer|football|basketball|baseball|hockey|"
    r"serie a|premier league|la liga|bundesliga|champions league|"
    r"kardashian|taylor swift|beyonce|grammys|oscars|emmys|"
    r"bachelor|bachelorette|american idol|dancing with|"
    r"wordle|fortnite|minecraft|roblox)\b",
    re.IGNORECASE,
)

TARGET_QUERIES = 100
WARM_DELAY = 0.5  # seconds between requests to avoid self-DoS


def get_searchlog_queries(top_n=30):
    """Top queries from our own users (past 7 days)."""
    try:
        from profoundd.utils.models import db, SearchLog
        from sqlalchemy import func

        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        rows = (
            db.session.query(SearchLog.query, func.count(SearchLog.id).label("cnt"))
            .filter(SearchLog.searched_at >= week_ago)
            .group_by(SearchLog.query)
            .order_by(func.count(SearchLog.id).desc())
            .limit(top_n)
            .all()
        )
        queries = [q for q, _ in rows if len(q) < 200]
        logger.info("SearchLog: %d queries from past 7 days", len(queries))

        # Also grab all-time top queries as fallback
        if len(queries) < 10:
            all_time = (
                db.session.query(SearchLog.query, func.count(SearchLog.id).label("cnt"))
                .group_by(SearchLog.query)
                .order_by(func.count(SearchLog.id).desc())
                .limit(top_n)
                .all()
            )
            for q, _ in all_time:
                if q not in queries and len(q) < 200:
                    queries.append(q)
            logger.info("SearchLog: padded to %d with all-time queries", len(queries))

        return queries
    except Exception as e:
        logger.warning("SearchLog fetch failed: %s", e)
        return []


def get_google_trends(top_n=30):
    """Daily trending searches from Google Trends RSS (US)."""
    try:
        resp = requests.get(
            "https://trends.google.com/trending/rss?geo=US",
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=10,
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        queries = []
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            if title and not SKIP_PATTERNS.search(title):
                queries.append(title)
            if len(queries) >= top_n:
                break

        logger.info("Google Trends: %d trending queries", len(queries))
        return queries
    except Exception as e:
        logger.warning("Google Trends fetch failed: %s", e)
        return []


def get_hot_article_topics(top_n=30):
    """Extract trending topics from recently indexed articles."""
    try:
        from profoundd.search.engine import SearchEngine
        import os

        es_url = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
        engine = SearchEngine(es_url)

        # Get articles from last 24 hours, sorted by recency
        results = engine.es.search(
            index="profoundd_articles",
            body={
                "size": 200,
                "sort": [{"published_at": {"order": "desc"}}],
                "query": {
                    "range": {
                        "published_at": {
                            "gte": "now-24h",
                        }
                    }
                },
                "_source": ["title"],
            },
        )

        # Extract key phrases from titles
        # Count 2-3 word phrases that appear in multiple headlines
        phrase_counter = Counter()
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "about", "like", "through", "after", "over", "between",
            "out", "against", "during", "before", "above", "below", "up",
            "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
            "this", "that", "these", "those", "it", "its", "his", "her",
            "he", "she", "they", "them", "we", "us", "you", "i", "my", "your",
            "what", "which", "who", "whom", "how", "when", "where", "why",
            "new", "says", "said", "just", "now", "more", "than", "also",
            "get", "gets", "got", "back", "going", "go", "take", "set",
        }

        for hit in results["hits"]["hits"]:
            title = hit["_source"].get("title", "")
            words = [w for w in re.findall(r"[a-zA-Z]+", title.lower()) if w not in stopwords and len(w) > 2]

            # Single important words
            for w in words:
                if len(w) > 3:
                    phrase_counter[w] += 1

            # 2-word phrases
            for i in range(len(words) - 1):
                phrase = f"{words[i]} {words[i+1]}"
                phrase_counter[phrase] += 1

        # Take phrases that appear 3+ times (trending across sources)
        trending = [phrase for phrase, count in phrase_counter.most_common(top_n * 2) if count >= 3]
        queries = trending[:top_n]

        logger.info("Article topics: %d hot phrases from last 24h", len(queries))
        return queries
    except Exception as e:
        logger.warning("Article topic extraction failed: %s", e)
        return []


def deduplicate_queries(all_queries):
    """Deduplicate, normalize, and cap at TARGET_QUERIES."""
    seen = set()
    unique = []
    for q in all_queries:
        normalized = q.strip().lower()
        # Skip very short or very long queries
        if len(normalized) < 3 or len(normalized) > 200:
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(q.strip())
    return unique[:TARGET_QUERIES]


def warm_cache(queries, base_url="http://127.0.0.1:5000"):
    """Hit /search for each query to populate the cache."""
    total = len(queries)
    warmed = 0
    failed = 0

    logger.info("Warming cache with %d queries...", total)

    for i, query in enumerate(queries):
        try:
            resp = requests.get(
                f"{base_url}/search",
                params={"q": query},
                timeout=30,
            )
            if resp.status_code == 200:
                warmed += 1
                # Check if it was already cached
                cache_hit = resp.headers.get("X-Cache", "") == "HIT"
                logger.debug(
                    "[%d/%d] %s — %s (%.1fs)",
                    i + 1, total, query,
                    "CACHED" if cache_hit else "FRESH",
                    resp.elapsed.total_seconds(),
                )
            else:
                failed += 1
                logger.warning("[%d/%d] %s — HTTP %d", i + 1, total, query, resp.status_code)
        except Exception as e:
            failed += 1
            logger.warning("[%d/%d] %s — FAILED: %s", i + 1, total, query, e)

        time.sleep(WARM_DELAY)

    logger.info("Cache warming complete: %d warmed, %d failed out of %d", warmed, failed, total)
    return warmed, failed


def run():
    """Main entry point — gather queries and warm the cache."""
    start = time.time()
    logger.info("=== Cache Warmer Starting ===")

    # Gather queries from all sources (priority order)
    all_queries = []

    # 1. Our users' actual searches (highest priority)
    all_queries.extend(get_searchlog_queries(top_n=30))

    # 2. Evergreen queries our audience cares about
    all_queries.extend(EVERGREEN_QUERIES)

    # 3. Google Trends (filtered for relevance)
    all_queries.extend(get_google_trends(top_n=30))

    # 4. Hot topics from our own article index
    all_queries.extend(get_hot_article_topics(top_n=30))

    # Deduplicate and cap
    queries = deduplicate_queries(all_queries)

    logger.info("Collected %d unique queries to warm", len(queries))

    # Warm the cache
    warmed, failed = warm_cache(queries, base_url="http://127.0.0.1:5000")

    elapsed = time.time() - start
    logger.info(
        "=== Cache Warmer Done === %d warmed, %d failed, %.1f minutes",
        warmed, failed, elapsed / 60,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Need Flask app context for SearchLog DB access
    try:
        from profoundd.app import app
        with app.app_context():
            run()
    except ImportError:
        # Running outside Flask (e.g., standalone) — skip DB sources
        logger.warning("Flask app not available, skipping SearchLog source")
        run()
