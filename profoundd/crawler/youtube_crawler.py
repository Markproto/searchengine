"""
YouTube Data API crawler — discovers videos based on popular Profoundd searches.

Runs daily (via scheduler). Pulls the top search queries from SearchLog,
searches YouTube for matching videos, and indexes them to ES.

Requires: YOUTUBE_API_KEY (free, 10K queries/day)
Set via /admin/ai-settings or YOUTUBE_API_KEY env var.

YouTube Data API docs: https://developers.google.com/youtube/v3/docs/search/list
"""
import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_URL = "https://www.googleapis.com/youtube/v3/videos"

# Max queries to run per daily cycle (stay well under 10K/day quota)
MAX_QUERIES_PER_RUN = 50
MAX_VIDEOS_PER_QUERY = 5


def get_api_key():
    """Get YouTube Data API key from DB or env."""
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        try:
            from profoundd.utils.models import SiteSetting
            key = SiteSetting.get("youtube_api_key", "")
        except Exception:
            pass
    return key


def search_youtube(query, api_key, max_results=5, published_after=None):
    """Search YouTube for videos matching query.

    Returns list of dicts with: title, url, description, channel, published_at, thumbnail
    """
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "order": "relevance",
        "relevanceLanguage": "en",
        "key": api_key,
    }
    if published_after:
        params["publishedAfter"] = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = requests.get(YOUTUBE_SEARCH_URL, params=params, timeout=15)
        if resp.status_code == 403:
            logger.warning("YouTube API quota exceeded or key invalid")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("YouTube search failed for '%s': %s", query, e)
        return []

    results = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        if not video_id:
            continue
        results.append({
            "title": snippet.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "summary": snippet.get("description", "")[:500],
            "source_name": snippet.get("channelTitle", "YouTube"),
            "published_at": snippet.get("publishedAt", ""),
            "image_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "category": "youtube",
            "source_credibility": 7,
            "tags": ["youtube-api", "video"],
            "result_type": "article",
        })
    return results


def get_top_queries(db_session, days=7, limit=50):
    """Get the most popular search queries from the last N days."""
    from profoundd.utils.models import SearchLog
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db_session.query(
        SearchLog.query.label("q"),
        func.count(SearchLog.id).label("c"),
    ).filter(
        SearchLog.searched_at >= cutoff,
    ).group_by(SearchLog.query).order_by(func.count(SearchLog.id).desc()).limit(limit).all()

    # Filter: skip very short, skip common noise
    skip = {"test", "a", "the", "and", "or", "not", "profoundd", ""}
    return [r.q for r in rows if r.q.lower().strip() not in skip and len(r.q.strip()) >= 3]


def run_youtube_crawl(search_engine, db_session):
    """Main entry: search YouTube for popular queries, index new videos.

    Returns number of videos indexed.
    """
    api_key = get_api_key()
    if not api_key:
        logger.debug("YouTube API key not configured, skipping")
        return 0

    queries = get_top_queries(db_session, days=7, limit=MAX_QUERIES_PER_RUN)
    if not queries:
        logger.debug("No search queries to check")
        return 0

    # Only look for videos from last 7 days
    published_after = datetime.now(timezone.utc) - timedelta(days=7)

    total_indexed = 0
    seen_urls = set()

    for query in queries:
        videos = search_youtube(
            query, api_key,
            max_results=MAX_VIDEOS_PER_QUERY,
            published_after=published_after,
        )
        if not videos:
            continue

        to_index = []
        for v in videos:
            url = v.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Check if already in ES
            doc_id = hashlib.md5(url.encode()).hexdigest()
            try:
                if search_engine.es.exists(index=search_engine.index_name, id=doc_id):
                    continue
            except Exception:
                pass

            v["crawled_at"] = datetime.now(timezone.utc).isoformat()
            to_index.append(v)

        if to_index:
            try:
                count = search_engine.bulk_index(to_index)
                total_indexed += count
                logger.info("YouTube: '%s' → %d new videos indexed", query[:40], count)
            except Exception as e:
                logger.warning("YouTube bulk index failed for '%s': %s", query[:40], e)

    logger.info("YouTube crawl done: %d videos indexed from %d queries", total_indexed, len(queries))
    return total_indexed
