"""
Background scheduler for automated crawling.
Uses APScheduler to run crawls at configured intervals.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)
_scheduler = None


def init_scheduler(app):
    """Initialize the background scheduler with the Flask app context."""
    global _scheduler

    if _scheduler is not None:
        return _scheduler

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

    # Cleanup old articles daily
    _scheduler.add_job(
        func=_run_cleanup,
        trigger=IntervalTrigger(days=1),
        id="daily_cleanup",
        name="Delete articles older than 30 days",
        replace_existing=True,
        kwargs={"app": app},
    )

    _scheduler.start()
    logger.info("Scheduler started: crawl every %d min, cleanup daily", interval_minutes)
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


def _run_cleanup(app):
    """Run daily cleanup within the app context."""
    with app.app_context():
        from profoundd.search.engine import SearchEngine
        engine = SearchEngine(app.config.get("ELASTICSEARCH_URL"))
        if engine.is_available():
            deleted = engine.delete_old_articles(days=30)
            logger.info("Daily cleanup: deleted %d old articles", deleted)


def shutdown_scheduler():
    """Shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
