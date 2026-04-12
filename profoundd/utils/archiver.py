"""
Web article archiver — backs up web-indexed articles to WD drive.
Articles tagged "web-indexed" that haven't been refreshed in 30 days get
exported to JSONL on Azure7's WD drive for backup. Articles are KEPT in ES
permanently — no deletion.
"""
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ARCHIVE_DAYS = 30
AZURE7_ARCHIVE_PATH = "mark@100.71.230.11:/mnt/backup/profoundd-archive/web-articles/"
MAX_PER_RUN = 500


def archive_stale_web_articles(search_engine):
    """
    Find web-indexed articles older than ARCHIVE_DAYS, export to JSONL,
    rsync to Azure7 WD drive for backup. Articles remain in ES permanently.
    Returns (archived_count, failed_count).
    """
    logger.info("Starting web article archive check (stale > %d days)...", ARCHIVE_DAYS)

    # Query ES for web-indexed articles with old crawled_at
    try:
        result = search_engine.es.search(
            index=search_engine.index_name,
            body={
                "size": MAX_PER_RUN,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"tags": "web-indexed"}},
                        ],
                        "filter": [
                            {"range": {"crawled_at": {"lt": f"now-{ARCHIVE_DAYS}d"}}},
                        ],
                        # Don't archive admin-saved articles
                        "must_not": [
                            {"term": {"tags": "admin-saved"}},
                        ],
                    }
                },
                "_source": True,
                "sort": [{"crawled_at": "asc"}],
            },
        )
    except Exception as e:
        logger.error("Archive query failed: %s", e)
        return 0, 0

    hits = result["hits"]["hits"]
    if not hits:
        logger.info("No stale web articles to archive.")
        return 0, 0

    logger.info("Found %d stale web-indexed articles to archive", len(hits))

    # Export to JSONL
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    local_path = os.path.join(tempfile.gettempdir(), f"web-archive-{date_str}.jsonl")

    articles_to_delete = []
    with open(local_path, "w") as f:
        for hit in hits:
            doc = hit["_source"]
            doc["_es_id"] = hit["_id"]
            doc["_archived_at"] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(doc) + "\n")
            articles_to_delete.append(doc.get("url", ""))

    logger.info("Exported %d articles to %s", len(articles_to_delete), local_path)

    # Rsync to Azure7 WD drive
    try:
        subprocess.run(
            ["ssh", "mark@100.71.230.11", "mkdir", "-p",
             "/mnt/backup/profoundd-archive/web-articles/"],
            timeout=30, check=False,
        )
        subprocess.run(
            ["rsync", "-az", local_path, AZURE7_ARCHIVE_PATH],
            timeout=120, check=True,
        )
        logger.info("Synced archive to Azure7 WD drive")
    except Exception as e:
        logger.warning("Archive rsync to Azure7 failed (will retry next run): %s", e)
        # Don't delete from ES if rsync failed — we'd lose the data
        os.remove(local_path)
        return 0, len(articles_to_delete)

    # Articles stay in ES permanently — no deletion
    os.remove(local_path)

    logger.info("Backed up %d articles to WD drive (kept in ES)", len(articles_to_delete))
    return len(articles_to_delete), 0


def backfill_popular_web_articles(search_engine, db_session, max_articles=20):
    """
    Fetch full text for popular web-indexed articles that only have snippets.
    Only backfills articles that have been clicked 2+ times.
    """
    from profoundd.utils.models import ArticleClick
    from profoundd.crawler.feed_crawler import FeedCrawler
    from sqlalchemy import func

    logger.info("Starting web article backfill...")

    # Find clicked web-indexed URLs
    clicked = (
        db_session.query(ArticleClick.article_url, func.count(ArticleClick.id).label("clicks"))
        .filter(ArticleClick.article_url.isnot(None))
        .group_by(ArticleClick.article_url)
        .having(func.count(ArticleClick.id) >= 2)
        .order_by(func.count(ArticleClick.id).desc())
        .limit(max_articles * 3)
        .all()
    )

    if not clicked:
        logger.info("No popular web articles to backfill.")
        return 0

    popular_urls = {row.article_url for row in clicked}

    # Query ES for web-indexed articles with short/empty content
    try:
        result = search_engine.es.search(
            index=search_engine.index_name,
            body={
                "size": max_articles * 2,
                "query": {
                    "bool": {
                        "must": [{"term": {"tags": "web-indexed"}}],
                        "must_not": [
                            {"exists": {"field": "content"}},
                        ],
                    }
                },
                "_source": ["url", "title", "summary", "source_name", "content"],
            },
        )
    except Exception as e:
        logger.error("Backfill query failed: %s", e)
        return 0

    # Filter to only popular (clicked) articles
    candidates = []
    for hit in result["hits"]["hits"]:
        url = hit["_source"].get("url", "")
        content = hit["_source"].get("content", "")
        if url in popular_urls and len(content or "") < 200:
            candidates.append(hit["_source"])

    if not candidates:
        logger.info("No popular articles need backfill.")
        return 0

    logger.info("Backfilling %d popular web articles with full text", min(len(candidates), max_articles))

    crawler = FeedCrawler(search_engine=search_engine)
    backfilled = 0

    for article in candidates[:max_articles]:
        url = article.get("url", "")
        try:
            full_text = crawler.fetch_full_text(url)
            if full_text and len(full_text) > 200:
                article["content"] = full_text
                article["crawled_at"] = datetime.now(timezone.utc).isoformat()
                search_engine.index_article(article)
                backfilled += 1
                logger.debug("Backfilled: %s (%d chars)", url, len(full_text))
        except Exception as e:
            logger.debug("Backfill failed for %s: %s", url, e)

    logger.info("Backfilled %d articles with full text", backfilled)
    return backfilled
