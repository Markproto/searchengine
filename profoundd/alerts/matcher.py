"""
Run saved alerts against Profoundd's index and email new matches.

Called from the scheduler (every 15 min). For each enabled alert, compares
recent results against a rolling seen_urls list to avoid re-emailing the
same articles.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone, timedelta

from profoundd.alerts.email_sender import send_alert_email
from profoundd.search.engine import SearchEngine
from profoundd.utils.models import db, SavedAlert, PublicUser, SiteSetting

logger = logging.getLogger(__name__)

# How long between checks for each frequency
FREQUENCY_HOURS = {
    "immediate": 0.25,  # every 15 min
    "daily": 24,
    "weekly": 168,
}

MAX_RESULTS_PER_EMAIL = 10
MAX_SEEN_URLS = 500


def _url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _get_due_alerts():
    """Return alerts that are due for a check based on their frequency."""
    now = datetime.now(timezone.utc)
    alerts = db.session.query(SavedAlert).filter_by(enabled=True).all()
    due = []
    for a in alerts:
        hours_between = FREQUENCY_HOURS.get(a.frequency, 24)
        if a.last_checked_at is None:
            due.append(a)
            continue
        # Normalize timezone — SQLite returns naive datetimes
        lc = a.last_checked_at
        if lc.tzinfo is None:
            lc = lc.replace(tzinfo=timezone.utc)
        if (now - lc).total_seconds() >= hours_between * 3600:
            due.append(a)
    return due


def _run_alert(engine, alert):
    """Run one alert's search, filter out already-seen URLs, return new matches."""
    try:
        results = engine.search(
            query=alert.query,
            category=alert.category if alert.category != "all" else None,
            page=1,
            per_page=30,
            sort_by="date",
        )
    except Exception as e:
        logger.warning("Alert %d search failed: %s", alert.id, e)
        return []

    articles = results.get("articles", [])
    if not articles:
        return []

    # Load seen set
    try:
        seen = set(json.loads(alert.seen_urls or "[]"))
    except Exception:
        seen = set()

    fresh = []
    new_hashes = []
    for a in articles:
        url = a.get("url") or ""
        if not url:
            continue
        h = _url_hash(url)
        if h in seen:
            continue
        fresh.append(a)
        new_hashes.append(h)
        if len(fresh) >= MAX_RESULTS_PER_EMAIL:
            break

    # Update seen list (cap at MAX_SEEN_URLS, drop oldest)
    combined = list(seen) + new_hashes
    if len(combined) > MAX_SEEN_URLS:
        combined = combined[-MAX_SEEN_URLS:]
    alert.seen_urls = json.dumps(combined)

    return fresh


def match_and_send_alerts(engine=None):
    """Main entrypoint — call from scheduler. Returns number of emails sent."""
    if engine is None:
        es_url = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
        engine = SearchEngine(es_url)
    if not engine.is_available():
        logger.debug("Alert matcher: ES unavailable, skipping")
        return 0

    due = _get_due_alerts()
    if not due:
        return 0

    logger.info("Alert matcher: %d alerts due for check", len(due))
    emails_sent = 0
    now = datetime.now(timezone.utc)
    domain = SiteSetting.get("domain", "profoundd.com") or "profoundd.com"

    for alert in due:
        alert.last_checked_at = now
        matches = _run_alert(engine, alert)

        if matches:
            user = db.session.query(PublicUser).get(alert.user_id)
            if user and user.email:
                ok = send_alert_email(
                    to_email=user.email,
                    alert=alert,
                    articles=matches,
                    domain=domain,
                )
                if ok:
                    alert.last_emailed_at = now
                    alert.emails_sent = (alert.emails_sent or 0) + 1
                    emails_sent += 1
                    logger.info(
                        "Alert %d (%s) emailed %d new matches to %s",
                        alert.id, alert.query[:50], len(matches), user.email,
                    )
                else:
                    logger.warning("Alert %d email send failed to %s", alert.id, user.email)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning("Alert matcher commit failed: %s", e)

    return emails_sent
