"""
Background scheduler for automated crawling.
Uses APScheduler to run crawls at configured intervals.
Only one gunicorn worker starts the scheduler (file-lock guard).
"""
import logging
import os
import fcntl
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = None
_lock_file = None


def init_scheduler(app):
    """Initialize the background scheduler with the Flask app context.

    Uses a file lock so only one gunicorn worker runs the scheduler.
    """
    global _scheduler, _lock_file

    if _scheduler is not None:
        return _scheduler

    # Only one worker should run the scheduler
    lock_path = os.path.join(app.instance_path, ".scheduler.lock")
    os.makedirs(app.instance_path, exist_ok=True)
    _lock_file = open(lock_path, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Scheduler already running in another worker, skipping")
        _lock_file.close()
        _lock_file = None
        return None

    _scheduler = BackgroundScheduler(daemon=True)

    interval_minutes = app.config.get("CRAWL_INTERVAL_MINUTES", 60)

    _scheduler.add_job(
        func=_run_scheduled_crawl,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scheduled_crawl",
        name=f"Crawl all sources every {interval_minutes} minutes",
        replace_existing=True,
        kwargs={"app": app},
    )

    # Daily OSM business data refresh
    osm_refresh_hours = app.config.get("OSM_REFRESH_HOURS", 24)
    _scheduler.add_job(
        func=_run_osm_refresh,
        trigger=IntervalTrigger(hours=osm_refresh_hours),
        id="osm_refresh",
        name=f"Refresh OSM business data every {osm_refresh_hours} hours",
        replace_existing=True,
        kwargs={"app": app},
    )

    _scheduler.start()
    logger.info("Scheduler started: crawl every %d min, OSM refresh every %dh", interval_minutes, osm_refresh_hours)
    return _scheduler


def _run_scheduled_crawl(app):
    """Run a full crawl within the app context."""
    from time import time

    with app.app_context():
        from profoundd.search.engine import SearchEngine
        from profoundd.crawler.feed_crawler import FeedCrawler
        from profoundd.utils.models import db, Source, CrawlLog

        engine = SearchEngine(app.config.get("ELASTICSEARCH_URL"))
        if not engine.is_available():
            logger.error("Scheduled crawl skipped: Elasticsearch unavailable")
            return

        engine.create_index()
        crawler = FeedCrawler(search_engine=engine)

        start = time()

        # Use DB sources if seeded, otherwise defaults
        if Source.query.count() > 0:
            sources = Source.query.filter_by(is_active=True).all()
            count = crawler.crawl_custom_sources(sources)
        else:
            count = crawler.crawl_all()

        duration = time() - start
        stats = crawler.get_stats()

        # Auto-recategorize articles matching special section keywords
        from profoundd.config.sources import SPECIAL_SECTION_KEYWORDS
        for cat_key, keywords in SPECIAL_SECTION_KEYWORDS.items():
            recategorized = engine.recategorize_by_keywords(keywords, cat_key)
            if recategorized:
                logger.info("Auto-tagged %d articles into '%s'", recategorized, cat_key)

        # Log the crawl with full stats
        log = CrawlLog(
            articles_found=stats.get("found", count),
            articles_new=stats.get("new", 0),
            articles_duplicate=stats.get("duplicate", 0),
            errors=stats.get("errors", 0),
            status="success" if count > 0 else "empty",
            trigger="scheduler",
            duration_seconds=round(duration, 1),
        )
        db.session.add(log)
        db.session.commit()
        logger.info("Scheduled crawl complete: %d found, %d new, %d errors in %.1fs",
                     stats.get("found", 0), stats.get("new", 0),
                     stats.get("errors", 0), duration)


def _run_osm_refresh(app):
    """Download latest OSM extracts and re-index businesses."""
    with app.app_context():
        from profoundd.search.engine import SearchEngine
        from profoundd.crawler.osm_crawler import import_all_states
        from profoundd.utils.models import db, SiteSetting
        from datetime import datetime, timezone

        engine = SearchEngine(app.config.get("ELASTICSEARCH_URL"))
        if not engine.is_available():
            logger.error("OSM refresh skipped: Elasticsearch unavailable")
            return

        data_dir = app.config.get("OSM_DATA_DIR", "data/osm")
        results = import_all_states(engine, data_dir)
        total = sum(r.get("indexed", 0) for r in results)

        SiteSetting.set(
            "osm_last_import",
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )
        db.session.commit()
        logger.info("OSM refresh complete: %d businesses across %d states", total, len(results))


def _run_cleanup(app):
    """Run daily cleanup within the app context."""
    with app.app_context():
        from profoundd.search.engine import SearchEngine
        engine = SearchEngine(app.config.get("ELASTICSEARCH_URL"))
        if engine.is_available():
            deleted = engine.delete_old_articles(days=30)
            logger.info("Daily cleanup: deleted %d old articles", deleted)


def shutdown_scheduler():
    """Shut down the scheduler and release the file lock."""
    global _scheduler, _lock_file
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    if _lock_file:
        fcntl.flock(_lock_file, fcntl.LOCK_UN)
        _lock_file.close()
        _lock_file = None
