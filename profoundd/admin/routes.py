"""
Admin panel routes for managing sources, rankings, and monitoring.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

import requests
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from profoundd.utils.models import db, Source, Article, AdminUser, SearchLog, CrawlLog, SourceSubmission, SiteSetting, ResearchDocument, AdminRankingAction, BobStory, NewsroomNote, SourceNote, PageView, ArticleClick
from profoundd.config.settings import get_config
from profoundd.config.sources import ALL_SOURCES, CATEGORIES, SPECIAL_SECTION_KEYWORDS
from profoundd.search.engine import SearchEngine
from profoundd.crawler.feed_crawler import FeedCrawler

logger = logging.getLogger(__name__)


def get_anthropic_key():
    """Get Anthropic API key from DB, falling back to environment variable."""
    import os
    key = SiteSetting.get("ai_anthropic_key", "")
    if not key:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    return key


def fetch_og_image(url):
    """Try to extract the og:image from a URL. Returns the image URL or None."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Profoundd/1.0)"
        })
        resp.raise_for_status()
        # Look for <meta property="og:image" content="...">
        match = re.search(
            r'<meta\s+[^>]*property=["\']og:image["\']\s+[^>]*content=["\']([^"\']+)["\']',
            resp.text, re.IGNORECASE
        )
        if not match:
            # Try reversed attribute order: content before property
            match = re.search(
                r'<meta\s+[^>]*content=["\']([^"\']+)["\']\s+[^>]*property=["\']og:image["\']',
                resp.text, re.IGNORECASE
            )
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning("Failed to fetch og:image from %s: %s", url, e)
    return None
config = get_config()
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Rate limiting: {ip: [timestamp, timestamp, ...]}
_login_attempts = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 10
LOGIN_LOCKOUT_SECONDS = 180  # 3 minutes


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


def _is_locked_out(ip):
    """Check if IP is locked out from too many login attempts."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
    # Clean old attempts
    _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
    return len(_login_attempts[ip]) >= LOGIN_MAX_ATTEMPTS


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr
        if _is_locked_out(ip):
            flash("Too many login attempts. Try again in 3 minutes.", "error")
            return render_template("admin/login.html")

        username = request.form.get("username")
        password = request.form.get("password")

        user = db.session.query(AdminUser).filter_by(username=username).first()
        if user and user.check_password(password):
            _login_attempts.pop(ip, None)  # clear on success
            session["admin_logged_in"] = True
            session["admin_user"] = username
            flash("Logged in successfully.", "success")
            return redirect(url_for("admin.dashboard"))

        _login_attempts[ip].append(datetime.now(timezone.utc))
        remaining = LOGIN_MAX_ATTEMPTS - len(_login_attempts[ip])
        if remaining > 0:
            flash(f"Invalid credentials. {remaining} attempts remaining.", "error")
        else:
            flash("Too many login attempts. Try again in 3 minutes.", "error")

    return render_template("admin/login.html")


@admin_bp.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_user", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin.login"))


@admin_bp.route("/")
@login_required
def dashboard():
    """Main admin dashboard with stats."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    es_stats = engine.get_stats() if engine.is_available() else {}

    sources_count = db.session.query(Source).count()
    active_sources = db.session.query(Source).filter_by(is_active=True).count()
    articles_count = db.session.query(Article).count()
    bob_stories_count = db.session.query(BobStory).filter_by(status="published").count()
    recent_searches = db.session.query(SearchLog).order_by(SearchLog.searched_at.desc()).limit(20).all()

    # Category breakdown
    category_stats = {}
    for cat_key, cat_info in CATEGORIES.items():
        count = db.session.query(Source).filter_by(category=cat_key, is_active=True).count()
        category_stats[cat_key] = {"label": cat_info["label"], "count": count, "icon": cat_info["icon"]}

    return render_template("admin/dashboard.html",
                           es_stats=es_stats,
                           es_available=engine.is_available(),
                           sources_count=sources_count,
                           active_sources=active_sources,
                           articles_count=articles_count,
                           bob_stories_count=bob_stories_count,
                           recent_searches=recent_searches,
                           category_stats=category_stats,
                           categories=CATEGORIES)


def _get_day_leaning(day_start, day_end):
    """Compute audience political leaning from article clicks for a date range."""
    clicks = db.session.query(ArticleClick).filter(
        ArticleClick.clicked_at >= day_start,
        ArticleClick.clicked_at < day_end,
        ArticleClick.bias_score.isnot(None),
    ).all()
    result = {"left": {"clicks": 0, "visitors": set()},
              "center": {"clicks": 0, "visitors": set()},
              "right": {"clicks": 0, "visitors": set()}}
    for c in clicks:
        bucket = "left" if c.bias_score <= 3 else ("right" if c.bias_score >= 7 else "center")
        result[bucket]["clicks"] += 1
        if c.visitor_id:
            result[bucket]["visitors"].add(c.visitor_id)
    return {k: {"clicks": v["clicks"], "visitors": len(v["visitors"])} for k, v in result.items()}


@admin_bp.route("/analytics")
@login_required
def analytics():
    """Visitor analytics dashboard."""
    from sqlalchemy import func, distinct
    import json as json_mod

    days = request.args.get("days", 7, type=int)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Total page views in period (humans only)
    total_views = db.session.query(func.count(PageView.id)).filter(
        PageView.viewed_at >= cutoff,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).scalar() or 0

    # Unique visitors in period
    unique_visitors = db.session.query(func.count(distinct(PageView.visitor_id))).filter(
        PageView.viewed_at >= cutoff,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).scalar() or 0

    # Views today
    views_today = db.session.query(func.count(PageView.id)).filter(
        PageView.viewed_at >= today_start,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).scalar() or 0

    # Bot hits in period
    bot_hits = db.session.query(func.count(PageView.id)).filter(
        PageView.viewed_at >= cutoff,
        PageView.is_bot == True,
    ).scalar() or 0

    # Views per day (humans)
    daily_views = db.session.query(
        func.date(PageView.viewed_at).label("day"),
        func.count(PageView.id).label("views"),
        func.count(distinct(PageView.visitor_id)).label("visitors"),
    ).filter(
        PageView.viewed_at >= cutoff,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).group_by(func.date(PageView.viewed_at)).order_by(func.date(PageView.viewed_at)).all()

    # Serialize daily data for JS drilldown
    daily_json = json_mod.dumps([
        {"day": str(row.day), "views": row.views, "visitors": row.visitors}
        for row in daily_views
    ])

    # Top pages
    top_pages = db.session.query(
        PageView.path,
        func.count(PageView.id).label("views"),
        func.count(distinct(PageView.visitor_id)).label("visitors"),
    ).filter(
        PageView.viewed_at >= cutoff,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).group_by(PageView.path).order_by(func.count(PageView.id).desc()).limit(20).all()

    # Top referrers (excluding empty)
    top_referrers = db.session.query(
        PageView.referrer,
        func.count(PageView.id).label("views"),
    ).filter(
        PageView.viewed_at >= cutoff,
        PageView.referrer != "",
        PageView.referrer.isnot(None),
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).group_by(PageView.referrer).order_by(func.count(PageView.id).desc()).limit(15).all()

    # Recent visitors (last 50)
    recent_views = db.session.query(PageView).order_by(
        PageView.viewed_at.desc()
    ).limit(50).all()

    # All-time totals (humans)
    total_all_time = db.session.query(func.count(PageView.id)).filter(
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).scalar() or 0
    unique_all_time = db.session.query(func.count(distinct(PageView.visitor_id))).filter(
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).scalar() or 0

    # Period-level session metrics
    from profoundd.utils.bot_detection import classify_referrer
    period_views = db.session.query(PageView).filter(
        PageView.viewed_at >= cutoff,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).all()

    session_map = defaultdict(list)
    durations = []
    search_count = 0
    new_visitor_ids = set()
    returning_visitor_ids = set()

    for pv in period_views:
        if pv.session_id:
            session_map[pv.session_id].append(pv)
        if pv.duration and pv.duration > 0:
            durations.append(pv.duration)
        ref_info = classify_referrer(pv.referrer)
        if ref_info["source"] == "Search":
            search_count += 1

    total_sessions = len(session_map)
    bounce_count = sum(1 for pvs in session_map.values() if len(pvs) == 1)
    pages_per = [len(pvs) for pvs in session_map.values()]

    period_bounce_rate = round(bounce_count / total_sessions * 100, 1) if total_sessions else 0
    period_avg_pages = round(sum(pages_per) / len(pages_per), 1) if pages_per else 0
    period_avg_duration = round(sum(durations) / len(durations)) if durations else 0
    period_search_pct = round(search_count / len(period_views) * 100, 1) if period_views else 0

    # New vs returning for period
    visitor_ids_in_period = set(pv.visitor_id for pv in period_views if pv.visitor_id)
    for vid in visitor_ids_in_period:
        has_prior = db.session.query(PageView.id).filter(
            PageView.visitor_id == vid,
            PageView.viewed_at < cutoff,
        ).limit(1).first()
        if has_prior:
            returning_visitor_ids.add(vid)
        else:
            new_visitor_ids.add(vid)

    period_new = len(new_visitor_ids)
    period_returning = len(returning_visitor_ids)

    # Audience political leaning (from article clicks, 30-day window)
    leaning_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    leaning_clicks = db.session.query(ArticleClick).filter(
        ArticleClick.clicked_at >= leaning_cutoff,
        ArticleClick.bias_score.isnot(None),
    ).all()
    leaning = {"left": 0, "center": 0, "right": 0}
    leaning_visitors = {"left": set(), "center": set(), "right": set()}
    for click in leaning_clicks:
        if click.bias_score <= 3:
            bucket = "left"
        elif click.bias_score <= 6:
            bucket = "center"
        else:
            bucket = "right"
        leaning[bucket] += 1
        if click.visitor_id:
            leaning_visitors[bucket].add(click.visitor_id)
    leaning_visitor_counts = {k: len(v) for k, v in leaning_visitors.items()}

    return render_template("admin/analytics.html",
                           categories=CATEGORIES,
                           days=days,
                           total_views=total_views,
                           unique_visitors=unique_visitors,
                           views_today=views_today,
                           bot_hits=bot_hits,
                           daily_views=daily_views,
                           daily_json=daily_json,
                           top_pages=top_pages,
                           top_referrers=top_referrers,
                           recent_views=recent_views,
                           total_all_time=total_all_time,
                           unique_all_time=unique_all_time,
                           leaning=leaning,
                           leaning_visitors=leaning_visitor_counts,
                           period_bounce_rate=period_bounce_rate,
                           period_avg_pages=period_avg_pages,
                           period_avg_duration=period_avg_duration,
                           period_search_pct=period_search_pct,
                           period_new=period_new,
                           period_returning=period_returning,
                           total_sessions=total_sessions)


@admin_bp.route("/api/analytics/day/<date_str>")
@login_required
def analytics_day_detail(date_str):
    """Return detailed analytics for a single day as JSON."""
    from sqlalchemy import func, distinct
    from urllib.parse import urlparse
    from profoundd.utils.bot_detection import parse_user_agent, classify_referrer, classify_screen, extract_search_keyword

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify(error="Invalid date"), 400

    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    # All human page views for this day
    views = db.session.query(PageView).filter(
        PageView.viewed_at >= day_start,
        PageView.viewed_at < day_end,
        db.or_(PageView.is_bot == False, PageView.is_bot.is_(None)),
    ).all()

    total = len(views)
    visitor_ids = set()
    session_map = defaultdict(list)  # session_id -> [views]
    hourly = [0] * 24
    browsers = defaultdict(int)
    os_counts = defaultdict(int)
    devices = defaultdict(int)
    screens = defaultdict(int)
    page_counts = defaultdict(int)
    source_counts = defaultdict(int)
    engine_counts = defaultdict(int)
    keywords = defaultdict(int)
    referrer_domains = defaultdict(int)
    durations = []
    languages = defaultdict(int)

    for pv in views:
        # Hourly
        if pv.viewed_at:
            hourly[pv.viewed_at.hour] += 1

        # Visitors
        if pv.visitor_id:
            visitor_ids.add(pv.visitor_id)

        # Sessions
        if pv.session_id:
            session_map[pv.session_id].append(pv)

        # UA parsing
        ua_info = parse_user_agent(pv.user_agent)
        browsers[ua_info["browser"]] += 1
        os_counts[ua_info["os"]] += 1
        devices[ua_info["device"]] += 1

        # Screen size
        screens[classify_screen(pv.screen_width)] += 1

        # Pages
        page_counts[pv.path] += 1

        # Traffic source
        ref_info = classify_referrer(pv.referrer)
        source_counts[ref_info["source"]] += 1
        if ref_info["search_engine"]:
            engine_counts[ref_info["search_engine"]] += 1
        if ref_info["keyword"]:
            keywords[ref_info["keyword"]] += 1

        # Referrer domains
        if pv.referrer:
            try:
                host = urlparse(pv.referrer).hostname
                if host and "profoundd" not in host:
                    referrer_domains[host] += 1
            except Exception:
                pass

        # Duration
        if pv.duration and pv.duration > 0:
            durations.append(pv.duration)

        # Language
        if pv.language:
            languages[pv.language] += 1

    # Session analysis
    total_sessions = len(session_map)
    bounce_count = 0
    pages_per_session_list = []
    landing_pages = defaultdict(int)
    exit_pages = defaultdict(int)

    for sid, pvs in session_map.items():
        pvs_sorted = sorted(pvs, key=lambda p: p.viewed_at or datetime.min.replace(tzinfo=timezone.utc))
        pages_per_session_list.append(len(pvs_sorted))
        if len(pvs_sorted) == 1:
            bounce_count += 1
        landing_pages[pvs_sorted[0].path] += 1
        exit_pages[pvs_sorted[-1].path] += 1

    bounce_rate = round(bounce_count / total_sessions * 100, 1) if total_sessions else 0
    avg_pages = round(sum(pages_per_session_list) / len(pages_per_session_list), 1) if pages_per_session_list else 0
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    # New vs returning visitors
    new_count = 0
    returning_count = 0
    if visitor_ids:
        for vid in visitor_ids:
            has_prior = db.session.query(PageView.id).filter(
                PageView.visitor_id == vid,
                PageView.viewed_at < day_start,
            ).limit(1).first()
            if has_prior:
                returning_count += 1
            else:
                new_count += 1

    # Search traffic percentage
    search_count = source_counts.get("Search", 0)
    search_pct = round(search_count / total * 100, 1) if total else 0

    # Profoundd internal searches for this day
    internal_searches = db.session.query(
        SearchLog.query.label("q"),
        func.count(SearchLog.id).label("count"),
    ).filter(
        SearchLog.searched_at >= day_start,
        SearchLog.searched_at < day_end,
    ).group_by(SearchLog.query).order_by(func.count(SearchLog.id).desc()).limit(20).all()

    def top_n(d, n=15):
        return sorted(d.items(), key=lambda x: -x[1])[:n]

    return jsonify(
        date=date_str,
        total=total,
        unique_visitors=len(visitor_ids),
        hourly=hourly,
        avg_pages=avg_pages,
        bounce_rate=bounce_rate,
        avg_duration=avg_duration,
        search_pct=search_pct,
        new_visitors=new_count,
        returning_visitors=returning_count,
        sources={k: v for k, v in source_counts.items()},
        engines=top_n(engine_counts, 10),
        keywords=top_n(keywords, 15),
        browsers=top_n(browsers, 10),
        os=top_n(os_counts, 10),
        devices=top_n(devices, 5),
        screens=top_n(screens, 6),
        languages=top_n(languages, 10),
        top_pages=top_n(page_counts, 15),
        landing_pages=top_n(landing_pages, 10),
        exit_pages=top_n(exit_pages, 10),
        referrer_domains=top_n(referrer_domains, 10),
        internal_searches=[[r.q, r.count] for r in internal_searches],
        leaning=_get_day_leaning(day_start, day_end),
    )


@admin_bp.route("/sources")
@login_required
def sources_list():
    """List all sources with their rankings."""
    category = request.args.get("category", "all")
    query = db.session.query(Source)
    if category != "all":
        query = query.filter_by(category=category)
    sources = query.order_by(Source.credibility.desc()).all()
    return render_template("admin/sources.html", sources=sources, categories=CATEGORIES,
                           current_category=category)


@admin_bp.route("/sources/add", methods=["GET", "POST"])
@login_required
def add_source():
    """Add a new source."""
    if request.method == "POST":
        source = Source(
            name=request.form["name"],
            url=request.form["url"],
            category=request.form["category"],
            feed_type=request.form.get("feed_type", "rss"),
            credibility=int(request.form.get("credibility", 5)),
            bias_score=int(request.form.get("bias_score", 5)),
            update_frequency=int(request.form.get("update_frequency", 5)),
            credibility_weight=float(request.form.get("credibility_weight", 0.4)),
            recency_weight=float(request.form.get("recency_weight", 0.3)),
            relevance_weight=float(request.form.get("relevance_weight", 0.3)),
            sponsor_tags=request.form.get("sponsor_tags", "").strip(),
            subcategory=request.form.get("subcategory", "").strip(),
        )
        db.session.add(source)
        db.session.commit()
        flash(f"Source '{source.name}' added successfully.", "success")
        return redirect(url_for("admin.sources_list"))

    return render_template("admin/source_form.html", source=None, categories=CATEGORIES)


@admin_bp.route("/sources/<int:source_id>/edit", methods=["GET", "POST"])
@login_required
def edit_source(source_id):
    """Edit an existing source."""
    source = db.session.get(Source, source_id)
    if not source:
        return "Not found", 404

    if request.method == "POST":
        old_credibility = source.credibility
        source.name = request.form["name"]
        source.url = request.form["url"]
        source.category = request.form["category"]
        source.feed_type = request.form.get("feed_type", "rss")
        source.credibility = int(request.form.get("credibility", 5))
        source.bias_score = int(request.form.get("bias_score", 5))
        source.update_frequency = int(request.form.get("update_frequency", 5))
        source.credibility_weight = float(request.form.get("credibility_weight", 0.4))
        source.recency_weight = float(request.form.get("recency_weight", 0.3))
        source.relevance_weight = float(request.form.get("relevance_weight", 0.3))
        source.sponsor_tags = request.form.get("sponsor_tags", "").strip()
        source.subcategory = request.form.get("subcategory", "").strip()
        source.is_active = "is_active" in request.form
        db.session.commit()

        # If credibility changed, propagate to all articles in Elasticsearch
        if source.credibility != old_credibility:
            engine = SearchEngine(config.ELASTICSEARCH_URL)
            if engine.is_available():
                count = engine.update_credibility_by_source(source.name, source.credibility)
                flash(f"Source '{source.name}' updated. Credibility synced to {count} articles.", "success")
            else:
                flash(f"Source '{source.name}' updated. (ES unavailable — articles not synced)", "warning")
        else:
            flash(f"Source '{source.name}' updated.", "success")
        return redirect(url_for("admin.sources_list"))

    return render_template("admin/source_form.html", source=source, categories=CATEGORIES)


@admin_bp.route("/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(source_id):
    source = db.session.get(Source, source_id)
    if not source:
        return "Not found", 404
    # Deactivate instead of hard-delete so seed won't re-add it
    source.is_active = False
    db.session.commit()
    flash(f"Source '{source.name}' removed. It will not be re-added on re-seed.", "warning")
    return redirect(url_for("admin.sources_list"))


@admin_bp.route("/sources/seed", methods=["POST"])
@login_required
def seed_sources():
    """Populate sources from the default config and sync credibility ratings."""
    added = 0
    updated = 0
    for src in ALL_SOURCES:
        existing = db.session.query(Source).filter_by(url=src["url"]).first()
        if not existing:
            source = Source(
                name=src["name"],
                url=src["url"],
                category=src["category"],
                credibility=src.get("credibility", 5),
                feed_type=src.get("feed_type", "rss"),
                sponsor_tags=src.get("sponsors", ""),
                subcategory=src.get("subcategory", ""),
            )
            db.session.add(source)
            added += 1
        else:
            # Skip sources that were manually deactivated by admin
            if not existing.is_active:
                continue
            # Sync fields from central config so code stays authoritative
            changed = False
            if existing.credibility != src.get("credibility", 5):
                existing.credibility = src.get("credibility", 5)
                changed = True
            if existing.name != src["name"]:
                existing.name = src["name"]
                changed = True
            if existing.category != src["category"]:
                existing.category = src["category"]
                changed = True
            if existing.feed_type != src.get("feed_type", "rss"):
                existing.feed_type = src.get("feed_type", "rss")
                changed = True
            new_sponsors = src.get("sponsors", "")
            if (existing.sponsor_tags or "") != new_sponsors:
                existing.sponsor_tags = new_sponsors
                changed = True
            new_subcategory = src.get("subcategory", "")
            if (existing.subcategory or "") != new_subcategory:
                existing.subcategory = new_subcategory
                changed = True
            if changed:
                updated += 1
    db.session.commit()
    flash(f"Seeded {added} new sources, updated {updated} existing from config.", "success")
    return redirect(url_for("admin.sources_list"))


@admin_bp.route("/crawl", methods=["POST"])
@login_required
def trigger_crawl():
    """Trigger a manual crawl in a background thread."""
    import threading
    from flask import current_app

    category = request.form.get("category", None)
    engine = SearchEngine(config.ELASTICSEARCH_URL)

    if not engine.is_available():
        flash("Elasticsearch is not available. Cannot crawl.", "error")
        return redirect(url_for("admin.dashboard"))

    engine.create_index()

    # Snapshot source data while we have app context
    has_db_sources = db.session.query(Source).count() > 0
    source_ids = None
    if has_db_sources:
        query = db.session.query(Source).filter_by(is_active=True)
        if category and category != "all":
            query = query.filter_by(category=category)
        source_ids = [s.id for s in query.all()]

    app = current_app._get_current_object()
    cat = category

    def run_crawl():
        from time import time
        with app.app_context():
            crawler = FeedCrawler(search_engine=engine)
            start = time()
            if source_ids:
                sources = db.session.query(Source).filter(Source.id.in_(source_ids)).all()
                count = crawler.crawl_custom_sources(sources)
            else:
                count = crawler.crawl_all() if not cat else len(crawler.crawl_category(cat))
            duration = time() - start
            stats = crawler.get_stats()
            crawl_log = CrawlLog(
                articles_found=stats["found"],
                articles_new=stats["new"],
                articles_duplicate=stats["duplicate"],
                errors=stats["errors"],
                status="success" if count > 0 else "empty",
                trigger="manual",
                category=cat if cat and cat != "all" else None,
                duration_seconds=round(duration, 1),
            )
            db.session.add(crawl_log)
            db.session.commit()
            app.logger.info(f"Background crawl done: {count} articles in {duration:.0f}s")

    threading.Thread(target=run_crawl, daemon=True).start()
    flash("Crawl started in background. Check Crawl Logs for results.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/cleanup", methods=["POST"])
@login_required
def cleanup_old():
    """Delete old articles."""
    days = int(request.form.get("days", 30))
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    deleted = engine.delete_old_articles(days=days)
    flash(f"Cleaned up {deleted} articles older than {days} days.", "info")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/sync-sponsors", methods=["POST"])
@login_required
def sync_sponsors():
    """Backfill sponsor tags onto all existing articles in Elasticsearch."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        flash("Elasticsearch is not available.", "error")
        return redirect(url_for("admin.dashboard"))

    # Get all sources with sponsor tags
    sources = db.session.query(Source).filter(Source.sponsor_tags != "", Source.sponsor_tags.isnot(None)).all()
    total_updated = 0
    for src in sources:
        sponsors = [s.strip() for s in src.sponsor_tags.split(",") if s.strip()]
        if sponsors:
            updated = engine.update_sponsors_by_source(src.name, sponsors)
            total_updated += updated

    flash(f"Synced sponsor tags to {total_updated} articles across {len(sources)} sources.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/sync-subcategories", methods=["POST"])
@login_required
def sync_subcategories():
    """Backfill subcategory tags onto all existing legislative articles in Elasticsearch."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        flash("Elasticsearch is not available.", "error")
        return redirect(url_for("admin.dashboard"))

    # Get all sources with a subcategory set
    sources = db.session.query(Source).filter(
        Source.subcategory != "", Source.subcategory.isnot(None)
    ).all()
    total_updated = 0
    for src in sources:
        if src.subcategory:
            updated = engine.update_subcategory_by_source(src.name, src.subcategory)
            total_updated += updated

    flash(f"Synced subcategories to {total_updated} articles across {len(sources)} sources.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/deduplicate", methods=["POST"])
@login_required
def deduplicate():
    """Remove duplicate articles by title, keeping earliest version."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        flash("Elasticsearch is not available.", "error")
        return redirect(url_for("admin.dashboard"))
    deleted, groups = engine.deduplicate_titles()
    flash(f"Dedup complete: removed {deleted} duplicates across {groups} title groups.", "info")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/crawl-history")
@login_required
def crawl_history():
    """View crawl history."""
    logs = db.session.query(CrawlLog).order_by(CrawlLog.started_at.desc()).limit(50).all()
    return render_template("admin/crawl_history.html", logs=logs, categories=CATEGORIES)


@admin_bp.route("/submissions")
@login_required
def submissions_list():
    """View public source submissions."""
    submissions = db.session.query(SourceSubmission).order_by(
        SourceSubmission.submitted_at.desc()
    ).all()
    return render_template("admin/submissions.html", submissions=submissions, categories=CATEGORIES)


@admin_bp.route("/submissions/<int:id>/review", methods=["POST"])
@login_required
def review_submission(id):
    """Approve or reject a source submission."""
    sub = db.session.query(SourceSubmission).get(id)
    if not sub:
        flash("Submission not found.", "error")
        return redirect(url_for("admin.submissions_list"))

    action = request.form.get("action")
    if action == "approve":
        # Create a new Source from the submission
        existing = db.session.query(Source).filter_by(url=sub.url).first()
        if existing:
            flash(f"Source '{sub.name}' already exists.", "warning")
        else:
            source = Source(
                name=sub.name,
                url=sub.url,
                category=sub.category,
                feed_type=sub.feed_type,
                credibility=7,
            )
            db.session.add(source)
            flash(f"Approved and added '{sub.name}' as a new source.", "success")
        sub.status = "approved"
        sub.reviewed_at = datetime.now(timezone.utc)
    elif action == "reject":
        sub.status = "rejected"
        sub.reviewed_at = datetime.now(timezone.utc)
        flash(f"Rejected '{sub.name}'.", "info")

    db.session.commit()
    return redirect(url_for("admin.submissions_list"))


# --- API endpoints for AJAX ---

@admin_bp.route("/api/stats")
@login_required
def api_stats():
    """Get stats as JSON."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    return jsonify({
        "elasticsearch": engine.get_stats() if engine.is_available() else {},
        "sources": db.session.query(Source).count(),
        "active_sources": db.session.query(Source).filter_by(is_active=True).count(),
    })


@admin_bp.route("/about", methods=["GET", "POST"])
@login_required
def edit_about():
    """Edit the public About page — one big HTML document."""
    if request.method == "POST":
        SiteSetting.set("about_page_html", request.form.get("about_page_html", ""))
        flash("About page updated.", "success")
        return redirect(url_for("admin.edit_about"))

    content = SiteSetting.get("about_page_html", "")
    return render_template("admin/edit_about.html", content=content, categories=CATEGORIES)


@admin_bp.route("/seo", methods=["GET", "POST"])
@login_required
def edit_seo():
    """Edit SEO meta tags for each page."""
    pages = ["home", "about", "search", "submit"]

    if request.method == "POST":
        for page in pages:
            SiteSetting.set(f"seo_{page}_title", request.form.get(f"{page}_title", "").strip())
            SiteSetting.set(f"seo_{page}_description", request.form.get(f"{page}_description", "").strip())
            SiteSetting.set(f"seo_{page}_keywords", request.form.get(f"{page}_keywords", "").strip())
        SiteSetting.set("seo_global_keywords", request.form.get("global_keywords", "").strip())
        flash("SEO settings updated.", "success")
        return redirect(url_for("admin.edit_seo"))

    seo = {}
    for page in pages:
        seo[page] = {
            "title": SiteSetting.get(f"seo_{page}_title", ""),
            "description": SiteSetting.get(f"seo_{page}_description", ""),
            "keywords": SiteSetting.get(f"seo_{page}_keywords", ""),
        }
    seo["global_keywords"] = SiteSetting.get("seo_global_keywords", "")

    return render_template("admin/edit_seo.html", seo=seo, categories=CATEGORIES)


@admin_bp.route("/research", methods=["GET", "POST"])
@login_required
def research_library():
    """Add research documents directly to the search index."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)

    if request.method == "POST":
        action = request.form.get("action", "add")

        if action == "delete":
            doc_id = request.form.get("doc_id", type=int)
            doc = db.session.query(ResearchDocument).get(doc_id)
            if doc:
                # Remove from ES
                if engine.is_available():
                    import hashlib
                    es_id = hashlib.md5(doc.doc_url.encode()).hexdigest()
                    try:
                        engine.es.delete(index=engine.index_name, id=es_id, ignore=[404])
                    except Exception:
                        pass
                db.session.delete(doc)
                db.session.commit()
                flash(f"Deleted '{doc.title}'.", "success")
            return redirect(url_for("admin.research_library"))

        if action == "reindex":
            # Re-index all research docs from DB into ES
            if engine.is_available():
                engine.create_index()
                docs = db.session.query(ResearchDocument).all()
                count = 0
                for doc in docs:
                    if engine.index_article(doc.to_es_doc()):
                        count += 1
                flash(f"Re-indexed {count}/{len(docs)} research documents.", "success")
            else:
                flash("Elasticsearch is not available.", "error")
            return redirect(url_for("admin.research_library"))

        # Default: add new document
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category = request.form.get("category", "news")
        source_name = request.form.get("source_name", "").strip() or "Profoundd Research"
        source_url = request.form.get("source_url", "").strip() or None
        image_url = request.form.get("image_url", "").strip() or None

        if not title or not content:
            flash("Title and content are required.", "error")
            return redirect(url_for("admin.research_library"))

        if not engine.is_available():
            flash("Elasticsearch is not available.", "error")
            return redirect(url_for("admin.research_library"))

        engine.create_index()

        # Auto-fetch og:image from source URL if no explicit image provided
        if source_url and not image_url:
            image_url = fetch_og_image(source_url)

        # Build a unique URL for this document
        import hashlib
        doc_hash = hashlib.md5(f"{title}{content[:200]}".encode()).hexdigest()[:12]
        doc_url = f"profoundd://research/{doc_hash}"

        # Extract first paragraph or 300 chars as summary
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        summary = lines[0][:500] if lines else title

        # Save to database first
        existing = db.session.query(ResearchDocument).filter_by(doc_url=doc_url).first()
        if not existing:
            doc = ResearchDocument(
                title=title,
                content=content,
                summary=summary,
                category=category,
                source_name=source_name,
                doc_url=doc_url,
                source_url=source_url,
                image_url=image_url,
            )
            db.session.add(doc)
            db.session.commit()

        # Index into ES
        article_data = (existing or doc).to_es_doc()
        result = engine.index_article(article_data)
        if result:
            flash(f"Research document '{title}' saved and indexed.", "success")
        else:
            flash("Saved to database but failed to index in ES. Use Re-index to retry.", "warning")

        return redirect(url_for("admin.research_library"))

    # Show existing research documents
    docs = db.session.query(ResearchDocument).order_by(ResearchDocument.created_at.desc()).all()
    return render_template("admin/research.html", categories=CATEGORIES, documents=docs)


@admin_bp.route("/ai-settings", methods=["GET", "POST"])
@login_required
def ai_settings():
    """Configure AI provider API keys."""
    if request.method == "POST":
        SiteSetting.set("ai_anthropic_key", request.form.get("anthropic_api_key", "").strip())
        SiteSetting.set("ai_anthropic_model", request.form.get("anthropic_model", "claude-sonnet-4-5-20250929"))
        SiteSetting.set("ai_xai_key", request.form.get("xai_api_key", "").strip())
        SiteSetting.set("ai_xai_model", request.form.get("xai_model", "grok-2-latest"))
        SiteSetting.set("ai_default_provider", request.form.get("default_ai_provider", "anthropic"))
        SiteSetting.set("searxng_url", request.form.get("searxng_url", "").strip().rstrip("/"))
        SiteSetting.set("congress_gov_api_key", request.form.get("congress_gov_api_key", "").strip())
        SiteSetting.set("source_research_guidelines", request.form.get("source_research_guidelines", "").strip())
        flash("Settings saved.", "success")
        return redirect(url_for("admin.ai_settings"))

    return render_template("admin/ai_settings.html",
                           anthropic_key=get_anthropic_key(),
                           anthropic_model=SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929"),
                           xai_key=SiteSetting.get("ai_xai_key", ""),
                           xai_model=SiteSetting.get("ai_xai_model", "grok-2-latest"),
                           default_provider=SiteSetting.get("ai_default_provider", "anthropic"),
                           searxng_url=SiteSetting.get("searxng_url", ""),
                           congress_gov_api_key=SiteSetting.get("congress_gov_api_key", ""),
                           source_research_guidelines=SiteSetting.get("source_research_guidelines", ""),
                           categories=CATEGORIES)


@admin_bp.route("/blocked-domains", methods=["GET", "POST"])
@login_required
def blocked_domains():
    """Manage domains blocked from external search results."""
    if request.method == "POST":
        domains_text = request.form.get("blocked_domains", "").strip()
        SiteSetting.set("blocked_domains", domains_text)
        flash("Blocked domains updated.", "success")
        return redirect(url_for("admin.blocked_domains"))

    blocked = SiteSetting.get("blocked_domains", "")
    return render_template("admin/blocked_domains.html",
                           blocked_domains=blocked,
                           categories=CATEGORIES)


@admin_bp.route("/analyze-url", methods=["GET", "POST"])
@login_required
def analyze_url():
    """Analyze a URL with AI, review, and index into search."""
    from profoundd.search.ai_analyzer import (
        fetch_url_content, analyze_with_anthropic, analyze_with_xai
    )

    anthropic_key = get_anthropic_key()
    xai_key = SiteSetting.get("ai_xai_key", "")
    default_provider = SiteSetting.get("ai_default_provider", "anthropic")
    has_ai_key = bool(anthropic_key or xai_key)

    if request.method == "POST":
        step = request.form.get("step")

        if step == "analyze":
            # Step 1: Fetch URL and run AI analysis
            url = request.form.get("url", "").strip()
            provider = request.form.get("provider", default_provider)

            if not url:
                flash("Please enter a URL.", "error")
                return redirect(url_for("admin.analyze_url"))

            # Fetch content
            content_data, error = fetch_url_content(url)
            if error:
                flash(f"Could not fetch URL: {error}", "error")
                return redirect(url_for("admin.analyze_url"))

            # Run AI analysis
            if provider == "xai" and xai_key:
                model = SiteSetting.get("ai_xai_model", "grok-2-latest")
                analysis, error = analyze_with_xai(content_data, xai_key, model)
            elif anthropic_key:
                model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")
                analysis, error = analyze_with_anthropic(content_data, anthropic_key, model)
            else:
                flash("No API key configured for the selected provider.", "error")
                return redirect(url_for("admin.analyze_url"))

            if error:
                flash(error, "error")
                return redirect(url_for("admin.analyze_url"))

            return render_template("admin/analyze_url.html",
                                   analysis=analysis,
                                   has_ai_key=has_ai_key,
                                   default_provider=default_provider,
                                   categories=CATEGORIES)

        elif step == "approve":
            # Step 2: Admin approved — index into ES
            engine = SearchEngine(config.ELASTICSEARCH_URL)
            if not engine.is_available():
                flash("Elasticsearch is not available.", "error")
                return redirect(url_for("admin.analyze_url"))

            engine.create_index()

            title = request.form.get("title", "").strip()
            summary = request.form.get("summary", "").strip()
            category = request.form.get("category", "news")
            source_name = request.form.get("source_name", "").strip()
            credibility = int(request.form.get("credibility", 7))
            tags = request.form.get("tags", "").strip()
            url = request.form.get("url", "").strip()

            if not title:
                flash("Title is required.", "error")
                return redirect(url_for("admin.analyze_url"))

            article_data = {
                "title": title,
                "summary": summary,
                "content": summary,
                "author": "AI Analysis",
                "category": category,
                "source_name": source_name or "Analyzed Source",
                "source_credibility": credibility,
                "url": url,
                "tags": [t.strip() for t in tags.split(",") if t.strip()],
                "published_at": datetime.now(timezone.utc).isoformat(),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
            }

            result = engine.index_article(article_data)
            if result:
                flash(f"'{title}' approved and indexed into search.", "success")
            else:
                flash("Failed to index article.", "error")

            return redirect(url_for("admin.analyze_url"))

    return render_template("admin/analyze_url.html",
                           analysis=None,
                           has_ai_key=has_ai_key,
                           default_provider=default_provider,
                           categories=CATEGORIES)


# --- Historical Crawl ---

# Store active historical crawlers for progress tracking
_active_historical_crawls = {}


@admin_bp.route("/sources/<int:source_id>/historical-crawl", methods=["GET", "POST"])
@login_required
def historical_crawl(source_id):
    """Launch a historical crawl for a specific source."""
    source = db.session.get(Source, source_id)
    if not source:
        flash("Source not found.", "error")
        return redirect(url_for("admin.sources_list"))

    if request.method == "POST":
        import threading
        from flask import current_app
        from profoundd.crawler.historical_crawler import HistoricalCrawler

        date_from_str = request.form.get("date_from", "")
        date_to_str = request.form.get("date_to", "")
        max_pages = int(request.form.get("max_pages", 500))

        if not date_from_str or not date_to_str:
            flash("Both start and end dates are required.", "error")
            return redirect(url_for("admin.historical_crawl", source_id=source_id))

        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for("admin.historical_crawl", source_id=source_id))

        if date_from > date_to:
            flash("Start date must be before end date.", "error")
            return redirect(url_for("admin.historical_crawl", source_id=source_id))

        engine = SearchEngine(config.ELASTICSEARCH_URL)
        if not engine.is_available():
            flash("Elasticsearch is not available.", "error")
            return redirect(url_for("admin.historical_crawl", source_id=source_id))

        engine.create_index()
        crawler = HistoricalCrawler(search_engine=engine)
        _active_historical_crawls[source_id] = crawler

        source_info = {
            "name": source.name,
            "url": source.url,
            "category": source.category,
            "credibility": source.credibility,
        }

        app = current_app._get_current_object()
        sid = source_id

        def run_historical():
            with app.app_context():
                try:
                    count = crawler.crawl(source_info, date_from, date_to, max_pages)
                    progress = crawler.get_progress()
                    crawl_log = CrawlLog(
                        articles_found=progress["urls_in_range"],
                        articles_new=progress["articles_indexed"],
                        articles_duplicate=progress["articles_skipped"],
                        errors=progress["errors"],
                        status="success" if count > 0 else "empty",
                        trigger="historical",
                        category=source_info["category"],
                        duration_seconds=0,
                    )
                    db.session.add(crawl_log)
                    db.session.commit()
                except Exception as e:
                    crawler.status = "error"
                    crawler.progress_message = str(e)
                    logger.error("Historical crawl failed for source %d: %s", sid, e)

        threading.Thread(target=run_historical, daemon=True).start()
        flash("Historical crawl started. Monitor progress below.", "success")
        return redirect(url_for("admin.historical_crawl", source_id=source_id))

    # Check if there's an active crawl for this source
    active_crawler = _active_historical_crawls.get(source_id)
    progress = active_crawler.get_progress() if active_crawler else None

    return render_template("admin/historical_crawl.html",
                           source=source,
                           progress=progress,
                           categories=CATEGORIES)


@admin_bp.route("/sources/<int:source_id>/historical-crawl/status")
@login_required
def historical_crawl_status(source_id):
    """AJAX endpoint for historical crawl progress."""
    crawler = _active_historical_crawls.get(source_id)
    if not crawler:
        return jsonify({"status": "idle", "message": "No active crawl."})
    return jsonify(crawler.get_progress())


@admin_bp.route("/api/update-credibility", methods=["POST"])
@login_required
def api_update_credibility():
    """Update credibility for ALL articles from a source (not just one article).

    Accepts either an article URL (looks up its source_name) or a direct
    source_name.  Updates every article from that source in Elasticsearch
    and syncs the credibility to the Source database record so future
    crawls inherit the new value.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    article_url = data.get("url", "").strip()
    source_name = data.get("source_name", "").strip()
    credibility = data.get("credibility")

    if not article_url and not source_name:
        return jsonify({"error": "URL or source_name is required"}), 400
    try:
        credibility = int(credibility)
    except (TypeError, ValueError):
        return jsonify({"error": "Credibility must be a number"}), 400
    if not 1 <= credibility <= 10:
        return jsonify({"error": "Credibility must be between 1 and 10"}), 400

    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        return jsonify({"error": "Elasticsearch not available"}), 503

    # Resolve the source name from the article if not provided directly
    if not source_name and article_url:
        article = engine.get_article(article_url)
        if not article:
            return jsonify({"error": "Article not found"}), 404
        source_name = article.get("source_name", "")

    if not source_name:
        return jsonify({"error": "Could not determine source name"}), 400

    # Update ALL articles from this source in Elasticsearch
    updated = engine.update_credibility_by_source(source_name, credibility)

    # Sync to the Source database record so future crawls use the new value
    db_source = db.session.query(Source).filter_by(name=source_name).first()
    if db_source:
        db_source.credibility = credibility
        db.session.commit()

    return jsonify({
        "success": True,
        "credibility": credibility,
        "source_name": source_name,
        "articles_updated": updated,
    })


@admin_bp.route("/api/update-boost", methods=["POST"])
@login_required
def api_update_boost():
    """AJAX endpoint to promote/demote an article's ranking. Logs action for The Man (AI training)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    article_url = data.get("url", "").strip()
    direction = data.get("direction")  # "promote" or "demote"
    search_query = data.get("search_query", "").strip()

    if not article_url or direction not in ("promote", "demote"):
        return jsonify({"error": "URL and direction (promote/demote) required"}), 400

    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        return jsonify({"error": "Elasticsearch not available"}), 503

    # Get current article data
    article = engine.get_article(article_url)
    if not article:
        return jsonify({"error": "Article not found"}), 404

    old_boost = article.get("admin_boost", 5) or 5
    new_boost = old_boost + (1 if direction == "promote" else -1)
    new_boost = max(1, min(10, new_boost))

    if not engine.update_boost(article_url, new_boost):
        return jsonify({"error": "Failed to update"}), 500

    # Log the action for The Man (AI training)
    action_log = AdminRankingAction(
        article_url=article_url,
        article_title=article.get("title", ""),
        source_name=article.get("source_name", ""),
        category=article.get("category", ""),
        action=direction,
        old_boost=old_boost,
        new_boost=new_boost,
        search_query=search_query,
    )
    db.session.add(action_log)
    db.session.commit()

    return jsonify({"success": True, "boost": new_boost})


# --- Add Category to Article ---

@admin_bp.route("/api/add-category", methods=["POST"])
@login_required
def api_add_category():
    """AJAX endpoint to add an article to an additional category."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    article_url = data.get("url", "").strip()
    category = data.get("category", "").strip()

    if not article_url or not category:
        return jsonify({"error": "URL and category required"}), 400

    if category not in CATEGORIES:
        return jsonify({"error": f"Unknown category: {category}"}), 400

    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        return jsonify({"error": "Elasticsearch not available"}), 503

    success = engine.add_category(article_url, category)
    if not success:
        return jsonify({"error": "Failed to add category — article not found"}), 404

    return jsonify({
        "success": True,
        "category": category,
        "label": CATEGORIES[category]["label"],
        "message": f"Added to {CATEGORIES[category]['label']}",
    })


# --- Newsroom Notes ---

@admin_bp.route("/api/newsroom-note", methods=["POST"])
@login_required
def save_newsroom_note():
    """Add or update an editorial note on an article."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    article_url = data.get("url", "").strip()
    note_text = data.get("note_text", "").strip()
    article_title = data.get("article_title", "").strip()

    if not article_url or not note_text:
        return jsonify({"error": "URL and note text are required"}), 400

    note = db.session.query(NewsroomNote).filter_by(article_url=article_url).first()
    if note:
        note.note_text = note_text
        note.updated_at = datetime.now(timezone.utc)
    else:
        note = NewsroomNote(
            article_url=article_url,
            article_title=article_title,
            note_text=note_text,
        )
        db.session.add(note)

    db.session.commit()
    return jsonify({"success": True, "note_id": note.id, "note_text": note.note_text})


@admin_bp.route("/api/newsroom-note/enhance", methods=["POST"])
@login_required
def enhance_newsroom_note():
    """Use AI to improve/expand an editorial note."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    note_text = data.get("note_text", "").strip()
    article_title = data.get("article_title", "").strip()
    article_summary = data.get("article_summary", "").strip()

    if not note_text:
        return jsonify({"error": "Note text is required"}), 400

    api_key = get_anthropic_key()
    model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")
    if not api_key:
        return jsonify({"error": "No AI API key configured."}), 400

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"You are NewsRoom Bob, an AI journalist for Profoundd search engine. "
            f"An editor has written a short note about an article. Rewrite this note in your voice — "
            f"direct, informative, and clear. Keep the editor's intent and opinion intact but make it "
            f"read like a professional editorial note. Keep it concise (2-4 sentences max). "
            f"Do NOT add any preamble, just return the improved note.\n\n"
            f"Article title: {article_title}\n"
            f"Article summary: {article_summary[:500]}\n\n"
            f"Editor's note: {note_text}"
        )

        message = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        enhanced = message.content[0].text.strip()
        return jsonify({"success": True, "enhanced_text": enhanced})
    except Exception as e:
        return jsonify({"error": f"AI enhancement failed: {e}"}), 500


@admin_bp.route("/api/newsroom-note", methods=["DELETE"])
@login_required
def delete_newsroom_note():
    """Delete an editorial note from an article."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    article_url = data.get("url", "").strip()
    if not article_url:
        return jsonify({"error": "URL is required"}), 400

    note = db.session.query(NewsroomNote).filter_by(article_url=article_url).first()
    if note:
        db.session.delete(note)
        db.session.commit()

    return jsonify({"success": True})


# --- Source Notes (credibility notes on content sources) ---

@admin_bp.route("/api/source-note", methods=["POST"])
@login_required
def save_source_note():
    """Add or update a credibility note on a content source."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    source_name = data.get("source_name", "").strip()
    note_text = data.get("note_text", "").strip()
    stance = data.get("stance", "neutral").strip()

    if not source_name or not note_text:
        return jsonify({"error": "Source name and note text are required"}), 400
    if stance not in ("trustworthy", "caution", "neutral"):
        stance = "neutral"

    note = db.session.query(SourceNote).filter_by(source_name=source_name).first()
    if note:
        note.note_text = note_text
        note.stance = stance
        note.updated_at = datetime.now(timezone.utc)
    else:
        note = SourceNote(source_name=source_name, note_text=note_text, stance=stance)
        db.session.add(note)

    db.session.commit()
    return jsonify({"success": True, "note_id": note.id, "note_text": note.note_text, "stance": note.stance})


@admin_bp.route("/api/source-note/research", methods=["POST"])
@login_required
def research_source_note():
    """Use AI to research a source's credibility and find evidence."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    source_name = data.get("source_name", "").strip()
    admin_claim = data.get("claim", "").strip()
    admin_guidance = data.get("guidance", "").strip()

    if not source_name:
        return jsonify({"error": "Source name is required"}), 400

    api_key = get_anthropic_key()
    model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")
    if not api_key:
        return jsonify({"error": "No AI API key configured."}), 400

    # Load the site-wide editorial constitution
    constitution = SiteSetting.get("source_research_guidelines", "").strip()

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Build the system prompt with the editorial constitution
        system_parts = [
            "You are a media credibility researcher for Profoundd, a news search engine. "
            "Your job is to help editors evaluate the credibility and trustworthiness of news sources."
        ]
        if constitution:
            system_parts.append(
                f"\n\n--- EDITORIAL GUIDELINES (follow these carefully) ---\n{constitution}\n--- END GUIDELINES ---"
            )
        system_prompt = "".join(system_parts)

        # Build the user prompt based on what the admin is asking for
        if admin_claim:
            user_prompt = (
                f"An editor has a claim about the news source '{source_name}': \"{admin_claim}\"\n\n"
                f"Research this claim. Write a concise credibility note (3-5 sentences) that:\n"
                f"1. Addresses whether the claim is supported by known facts\n"
                f"2. Mentions specific incidents, lawsuits, retractions, or awards if relevant\n"
                f"3. Includes URLs to evidence where possible (use real, well-known URLs only)\n"
                f"4. Is fair and factual — acknowledge both strengths and weaknesses\n"
            )
        else:
            user_prompt = (
                f"Write a concise credibility assessment (3-5 sentences) for the news source '{source_name}'.\n\n"
                f"Include:\n"
                f"1. What kind of outlet it is (legacy media, tabloid, independent, etc.)\n"
                f"2. Notable credibility issues OR strengths (retractions, awards, lawsuits, bias ratings)\n"
                f"3. Include URLs to evidence where possible (use real, well-known URLs only)\n"
                f"4. Be fair — acknowledge both strengths and weaknesses\n"
            )

        # Add per-request guidance from the admin
        if admin_guidance:
            user_prompt += (
                f"\n\nAdditional direction from the editor for this specific research:\n\"{admin_guidance}\""
            )

        user_prompt += (
            "\n\nFormat: Write the note as plain text. Include links inline like: "
            "(source: https://example.com/article). Do NOT use markdown."
        )

        message = client.messages.create(
            model=model,
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        research = message.content[0].text.strip()
        return jsonify({"success": True, "research_text": research})
    except Exception as e:
        return jsonify({"error": f"AI research failed: {e}"}), 500


@admin_bp.route("/api/source-note", methods=["DELETE"])
@login_required
def delete_source_note():
    """Delete a credibility note from a source."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    source_name = data.get("source_name", "").strip()
    if not source_name:
        return jsonify({"error": "Source name is required"}), 400

    note = db.session.query(SourceNote).filter_by(source_name=source_name).first()
    if note:
        db.session.delete(note)
        db.session.commit()

    return jsonify({"success": True})


@admin_bp.route("/source-notes")
@login_required
def source_notes_list():
    """Admin page to manage all source credibility notes."""
    import json
    notes = db.session.query(SourceNote).order_by(SourceNote.updated_at.desc()).all()
    note_data = {n.id: {"name": n.source_name, "stance": n.stance, "text": n.note_text} for n in notes}
    return render_template("admin/source_notes.html", notes=notes, note_data_json=json.dumps(note_data), categories=CATEGORIES)


@admin_bp.route("/source-notes/delete/<int:note_id>", methods=["POST"])
@login_required
def source_note_delete(note_id):
    """Delete a source note by ID (form-based)."""
    note = db.session.get(SourceNote, note_id)
    if note:
        db.session.delete(note)
        db.session.commit()
        flash(f"Deleted note for {note.source_name}", "success")
    return redirect(url_for("admin.source_notes_list"))


@admin_bp.route("/source-notes/save", methods=["POST"])
@login_required
def source_note_save():
    """Save or update a source note (form-based)."""
    source_name = request.form.get("source_name", "").strip()
    note_text = request.form.get("note_text", "").strip()
    stance = request.form.get("stance", "neutral").strip()
    orig_name = request.form.get("orig_name", "").strip()

    if not source_name or not note_text:
        flash("Source name and note text are required", "error")
        return redirect(url_for("admin.source_notes_list"))

    if stance not in ("trustworthy", "caution", "neutral"):
        stance = "neutral"

    # If renaming, delete the old one
    if orig_name and orig_name != source_name:
        old = db.session.query(SourceNote).filter_by(source_name=orig_name).first()
        if old:
            db.session.delete(old)

    note = db.session.query(SourceNote).filter_by(source_name=source_name).first()
    if note:
        note.note_text = note_text
        note.stance = stance
        note.updated_at = datetime.now(timezone.utc)
    else:
        note = SourceNote(source_name=source_name, note_text=note_text, stance=stance)
        db.session.add(note)

    db.session.commit()
    flash(f"Saved note for {source_name}", "success")
    return redirect(url_for("admin.source_notes_list"))


@admin_bp.route("/the-man", methods=["GET", "POST"])
@login_required
def the_man():
    """The Man — AI editorial guidance system. View ranking history and set editorial guidelines."""
    from profoundd.search.the_man import run_the_man

    decisions = None
    run_error = None

    if request.method == "POST":
        action = request.form.get("action", "save_guidelines")

        if action == "save_guidelines":
            SiteSetting.set("the_man_guidelines", request.form.get("guidelines", "").strip())
            flash("Editorial guidelines saved.", "success")
            return redirect(url_for("admin.the_man"))

        elif action == "run":
            auto_apply = request.form.get("auto_apply") == "1"
            guidelines = SiteSetting.get("the_man_guidelines", "")
            api_key = get_anthropic_key()
            model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")

            engine = SearchEngine(config.ELASTICSEARCH_URL)
            past_actions = (db.session.query(AdminRankingAction)
                            .order_by(AdminRankingAction.acted_at.desc())
                            .limit(30)
                            .all())

            decisions, run_error = run_the_man(
                engine, guidelines, past_actions, api_key, model, auto_apply=auto_apply
            )

            if run_error:
                flash(f"The Man: {run_error}", "error")
            elif auto_apply and decisions:
                # Log auto-applied actions
                applied_count = sum(1 for d in decisions if d.get("applied"))
                for d in decisions:
                    if d.get("applied") and d["action"] != "skip":
                        log = AdminRankingAction(
                            article_url=d["url"],
                            article_title="",
                            source_name="",
                            category="",
                            action=d["action"],
                            old_boost=d.get("old_boost", 0),
                            new_boost=d.get("new_boost", 0),
                            search_query="[The Man auto-applied]",
                        )
                        db.session.add(log)
                db.session.commit()
                flash(f"The Man applied {applied_count} ranking changes.", "success")

    guidelines = SiteSetting.get("the_man_guidelines", "")
    recent_actions = (db.session.query(AdminRankingAction)
                      .order_by(AdminRankingAction.acted_at.desc())
                      .limit(100)
                      .all())

    # Summary stats
    total_actions = db.session.query(AdminRankingAction).count()
    promotes = db.session.query(AdminRankingAction).filter_by(action="promote").count()
    demotes = db.session.query(AdminRankingAction).filter_by(action="demote").count()

    return render_template("admin/the_man.html",
                           guidelines=guidelines,
                           recent_actions=recent_actions,
                           total_actions=total_actions,
                           promotes=promotes,
                           demotes=demotes,
                           decisions=decisions,
                           run_error=run_error)


# --- NewsRoom Bob ---

@admin_bp.route("/bob/write", methods=["POST"])
def bob_write():
    """Trigger NewsRoom Bob to write a story based on an article. AJAX endpoint."""
    if not session.get("admin_logged_in"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    article_url = data.get("url", "").strip()
    article_title = data.get("title", "").strip()
    source_name = data.get("source_name", "").strip()
    summary = data.get("summary", "").strip()

    # Support multi-category: accept 'categories' list or fall back to single 'category'
    categories = data.get("categories", [])
    if not categories:
        single_cat = data.get("category", "news").strip()
        categories = [single_cat] if single_cat else ["news"]

    primary_category = categories[0]
    extra_categories = categories[1:] if len(categories) > 1 else []

    if not article_url or not article_title:
        return jsonify({"error": "Article URL and title are required"}), 400

    # Check if Bob already wrote about this
    existing = db.session.query(BobStory).filter_by(source_article_url=article_url).first()
    if existing:
        return jsonify({
            "success": True,
            "already_exists": True,
            "slug": existing.slug,
            "message": "Bob already wrote this one!",
        })

    # Get API key
    api_key = get_anthropic_key()
    model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")
    if not api_key:
        return jsonify({"error": "No AI API key configured. Go to Admin > AI Settings."}), 400

    # If we have a URL, try to fetch more content for Bob to work with
    content_for_bob = summary
    if article_url and not article_url.startswith("profoundd://"):
        try:
            from profoundd.search.ai_analyzer import fetch_url_content
            fetched, fetch_err = fetch_url_content(article_url)
            if fetched and fetched.get("text"):
                content_for_bob = fetched["text"]
        except Exception:
            pass  # Fall back to summary

    # Generate the story
    from profoundd.search.newsroom_bob import generate_bob_story, make_slug

    # Look up subcategory from the original article in ES (for legislative items)
    subcategory = ""
    if primary_category in ("legislative", "state-legislative"):
        try:
            _engine = SearchEngine(config.ELASTICSEARCH_URL)
            _orig = _engine.get_article(article_url)
            if _orig:
                subcategory = _orig.get("subcategory", "")
        except Exception:
            pass

    article_data = {
        "title": article_title,
        "url": article_url,
        "source_name": source_name,
        "category": primary_category,
        "content": content_for_bob,
        "summary": summary,
    }

    result, error = generate_bob_story(article_data, api_key, model)
    if error:
        return jsonify({"error": error}), 500

    # Try to grab an image from the original article
    image_url = None
    if article_url and not article_url.startswith("profoundd://"):
        image_url = fetch_og_image(article_url)

    # Create the slug and ensure uniqueness
    slug = make_slug(result["headline"] or article_title)
    base_slug = slug
    counter = 1
    while db.session.query(BobStory).filter_by(slug=slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    # Save to database (store extra categories as comma-separated string)
    story = BobStory(
        title=result["headline"] or article_title,
        slug=slug,
        content=result["body"],
        summary=result["summary"],
        seo_keywords=result["seo_keywords"],
        seo_description=result["seo_description"],
        category=primary_category,
        extra_categories=",".join(extra_categories) if extra_categories else "",
        image_url=image_url,
        source_article_url=article_url,
        source_article_title=article_title,
        source_name=source_name,
    )
    db.session.add(story)
    db.session.commit()

    # Index to Elasticsearch — one doc per selected category
    try:
        engine = SearchEngine(config.ELASTICSEARCH_URL)
        es_doc = story.to_es_doc()
        if subcategory:
            es_doc["subcategory"] = subcategory
        # Primary category: uses profoundd://bob/<slug>
        engine.index_article(es_doc)

        # Extra categories: uses profoundd://bob/<cat>/<slug> for unique doc IDs
        for extra_cat in extra_categories:
            extra_doc = dict(es_doc)
            extra_doc["category"] = extra_cat
            extra_doc["url"] = f"profoundd://bob/{extra_cat}/{slug}"
            # Clear subcategory for non-legislative categories
            if extra_cat not in ("legislative", "state-legislative"):
                extra_doc.pop("subcategory", None)
            engine.index_article(extra_doc)

        # For non-legislative categories, pull the original article from ES
        if article_url and not article_url.startswith("profoundd://"):
            if primary_category in ("legislative", "state-legislative"):
                logger.info("Legislative article — kept original in ES: %s", article_url)
            else:
                engine.delete_article(article_url)
                logger.info("Pulled original article from ES: %s", article_url)
    except Exception as e:
        logger.warning("Could not index Bob story to ES: %s", e)

    cat_count = len(categories)
    return jsonify({
        "success": True,
        "slug": slug,
        "title": story.title,
        "message": f"Bob wrote it! Published to {cat_count} {'category' if cat_count == 1 else 'categories'}.",
    })


@admin_bp.route("/bob/stories")
@login_required
def bob_stories_list():
    """Admin page listing all NewsRoom Bob stories."""
    stories = db.session.query(BobStory).order_by(BobStory.published_at.desc()).all()
    return render_template("admin/bob_stories.html", stories=stories)


@admin_bp.route("/bob/stories/<int:story_id>/delete", methods=["POST"])
@login_required
def bob_story_delete(story_id):
    """Delete a Bob story from DB and ES."""
    story = db.session.query(BobStory).get(story_id)
    if not story:
        flash("Story not found.", "error")
        return redirect(url_for("admin.bob_stories_list"))

    # Remove from Elasticsearch — primary doc + any extra category copies
    try:
        import hashlib
        engine = SearchEngine(config.ELASTICSEARCH_URL)
        # Delete primary doc
        es_url = f"profoundd://bob/{story.slug}"
        es_id = hashlib.md5(es_url.encode()).hexdigest()
        engine.es.delete(index=engine.index_name, id=es_id, ignore=[404])
        # Delete extra category copies
        extra = story.extra_categories if hasattr(story, "extra_categories") and story.extra_categories else ""
        for cat in [c.strip() for c in extra.split(",") if c.strip()]:
            extra_url = f"profoundd://bob/{cat}/{story.slug}"
            extra_id = hashlib.md5(extra_url.encode()).hexdigest()
            engine.es.delete(index=engine.index_name, id=extra_id, ignore=[404])
    except Exception as e:
        logger.warning("Could not remove Bob story from ES: %s", e)

    db.session.delete(story)
    db.session.commit()
    flash(f"Deleted: {story.title}", "success")
    return redirect(url_for("admin.bob_stories_list"))


@admin_bp.route("/bob/stories/<int:story_id>/restore-original", methods=["POST"])
@login_required
def bob_story_restore_original(story_id):
    """Re-index the original source article back into Elasticsearch."""
    story = db.session.query(BobStory).get(story_id)
    if not story:
        flash("Story not found.", "error")
        return redirect(url_for("admin.bob_stories_list"))

    if not story.source_article_url or story.source_article_url.startswith("profoundd://"):
        flash("No external source URL to restore.", "error")
        return redirect(url_for("admin.bob_stories_list"))

    # Fetch content from the original URL
    try:
        from profoundd.search.ai_analyzer import fetch_url_content
        fetched, fetch_err = fetch_url_content(story.source_article_url)
        if not fetched or not fetched.get("text"):
            flash(f"Could not fetch original article: {fetch_err or 'empty content'}", "error")
            return redirect(url_for("admin.bob_stories_list"))
    except Exception as e:
        flash(f"Error fetching original: {e}", "error")
        return redirect(url_for("admin.bob_stories_list"))

    # Build an article doc and re-index it
    article_doc = {
        "title": story.source_article_title or fetched.get("page_title", "Untitled"),
        "url": story.source_article_url,
        "summary": fetched.get("meta_description") or fetched["text"][:500],
        "content": fetched["text"],
        "author": story.source_name or "Unknown",
        "category": story.category or "news",
        "source_name": story.source_name or "Unknown",
        "source_credibility": 5,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "tags": [],
    }

    try:
        engine = SearchEngine(config.ELASTICSEARCH_URL)
        engine.index_article(article_doc)
        flash(f"Restored original article to search index: {article_doc['title'][:60]}", "success")
    except Exception as e:
        flash(f"Failed to re-index original: {e}", "error")

    return redirect(url_for("admin.bob_stories_list"))


# --- Special Section Re-categorization ---

@admin_bp.route("/recategorize-sections", methods=["POST"])
@login_required
def recategorize_sections():
    """Re-categorize existing articles into special sections based on keywords."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    total = 0
    for cat_key, keywords in SPECIAL_SECTION_KEYWORDS.items():
        updated = engine.recategorize_by_keywords(keywords, cat_key)
        total += updated

    # Also update BobStory records in the database
    for cat_key, keywords in SPECIAL_SECTION_KEYWORDS.items():
        for story in db.session.query(BobStory).filter_by(status="published").all():
            text = f"{story.title} {story.summary or ''}".lower()
            if any(kw in text for kw in keywords):
                story.category = cat_key
    db.session.commit()

    flash(f"Re-categorized {total} articles into special sections.", "success")
    return redirect(url_for("admin.dashboard"))


# --- Verification Bot ---

@admin_bp.route("/verify", methods=["GET", "POST"])
@login_required
def verify_claim():
    """Victor the Verifier: analyze a claim from dual perspectives and publish."""
    from profoundd.search.verify_bot import (
        extract_search_queries, search_for_evidence, generate_verification_story,
        fetch_url_content, _extract_pdf_text,
    )
    from profoundd.search.newsroom_bob import make_slug

    api_key = get_anthropic_key()
    model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929")
    congress_key = SiteSetting.get("congress_gov_api_key", "")
    has_ai_key = bool(api_key)

    if request.method == "POST":
        step = request.form.get("step", "analyze")

        if step == "analyze":
            claim_text = request.form.get("claim_text", "").strip()
            if not claim_text:
                flash("Please paste the claim or post to verify.", "error")
                return redirect(url_for("admin.verify_claim"))

            if not api_key:
                flash("No AI API key configured. Go to Admin > AI Settings.", "error")
                return redirect(url_for("admin.verify_claim"))

            # Step 1: Extract claims and search queries
            claims_data = extract_search_queries(claim_text, api_key, model)

            # Step 2: Search for evidence (including bill text and referenced URLs)
            engine = SearchEngine(config.ELASTICSEARCH_URL)
            evidence = search_for_evidence(
                claims_data.get("queries", []), engine,
                congress_api_key=congress_key,
                references=claims_data.get("references", {}),
            )

            # Step 2b: Handle uploaded bill document
            bill_file = request.files.get("bill_file")
            if bill_file and bill_file.filename:
                filename = bill_file.filename.lower()
                file_bytes = bill_file.read()
                uploaded_text = ""

                if filename.endswith(".pdf"):
                    uploaded_text = _extract_pdf_text(file_bytes, max_chars=20000)
                elif filename.endswith((".txt", ".html", ".htm", ".xml")):
                    raw = file_bytes.decode("utf-8", errors="replace")
                    if filename.endswith((".html", ".htm", ".xml")):
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(raw[:500000], "lxml")
                        for tag in soup(["script", "style", "meta", "link"]):
                            tag.decompose()
                        uploaded_text = soup.get_text(separator="\n", strip=True)
                    else:
                        uploaded_text = raw
                    if len(uploaded_text) > 20000:
                        uploaded_text = uploaded_text[:20000] + "\n... [truncated]"

                if uploaded_text and uploaded_text.strip():
                    if "uploaded_documents" not in evidence:
                        evidence["uploaded_documents"] = []
                    evidence["uploaded_documents"].append({
                        "title": f"Uploaded: {bill_file.filename}",
                        "content": uploaded_text,
                    })

            # Step 3: Generate verification story
            result, error = generate_verification_story(
                claim_text, claims_data, evidence, api_key, model,
            )

            if error:
                flash(f"Verification failed: {error}", "error")
                return redirect(url_for("admin.verify_claim"))

            # Convert markdown report to HTML for display
            import markdown
            result["report_html"] = markdown.markdown(
                result["report"], extensions=["tables", "fenced_code"],
            )

            # Count evidence found
            total_evidence = sum(len(v) for v in evidence.values())

            return render_template("admin/verify.html",
                                   categories=CATEGORIES,
                                   has_ai_key=has_ai_key,
                                   result=result,
                                   claim_text=claim_text,
                                   claims_data=claims_data,
                                   evidence=evidence,
                                   total_evidence=total_evidence)

        elif step == "bob_write":
            # Have Bob write a story from Victor's report with specific angle
            import anthropic as _anthropic

            bob_instructions = request.form.get("bob_instructions", "").strip()
            victor_report = request.form.get("victor_report", "").strip()
            victor_headline = request.form.get("victor_headline", "").strip()
            victor_keywords = request.form.get("victor_seo_keywords", "").strip()
            claim_text = request.form.get("claim_text", "").strip()
            bob_categories = request.form.getlist("bob_categories")

            if not bob_categories:
                bob_categories = ["legislative"]

            if not victor_report:
                flash("No report to work from.", "error")
                return redirect(url_for("admin.verify_claim"))

            if not api_key:
                flash("No AI API key configured.", "error")
                return redirect(url_for("admin.verify_claim"))

            # Truncate report if needed
            report_for_bob = victor_report
            if len(report_for_bob) > 15000:
                report_for_bob = report_for_bob[:15000] + "\n\n[Content truncated...]"

            bob_prompt = f"""You are NewsRoom Bob, a sharp, no-nonsense journalist for Profoundd.com.

Below is a verification report by Victor the Verifier analyzing a legislative claim. The report contains both conservative and progressive perspectives with detailed talking points.

YOUR INSTRUCTIONS FROM THE EDITOR:
{bob_instructions or "Write a balanced news story covering the key findings."}

ORIGINAL CLAIM:
{claim_text[:3000]}

VICTOR'S VERIFICATION REPORT:
{report_for_bob}

Based on the editor's instructions, write a FRESH news story. Follow the angle requested — if they say "progressive talking points," emphasize those. If they say "conservative perspective," focus there. If they specify a section, drill into that.

Write your response in EXACTLY this format:

HEADLINE: [A compelling, SEO-friendly headline — clear, not clickbait, under 80 chars]
SUMMARY: [2-3 sentence summary that hooks the reader and covers the key facts]
BODY: [Full article body, 4-8 paragraphs. Use facts and citations from Victor's report. Reference specific sections, pages, and quotes where available. Write in Bob's direct, no-nonsense voice.]
SEO_KEYWORDS: [8-12 comma-separated keywords/phrases relevant to the story]
SEO_DESCRIPTION: [A 150-160 character meta description for search engines]"""

            try:
                client = _anthropic.Anthropic(api_key=api_key)
                msg = client.messages.create(
                    model=model,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": bob_prompt}],
                )
                response_text = msg.content[0].text
            except Exception as e:
                flash(f"Bob couldn't write this: {e}", "error")
                return redirect(url_for("admin.verify_claim"))

            # Parse Bob's response
            from profoundd.search.newsroom_bob import _parse_bob_response
            result_data = _parse_bob_response(response_text)

            slug = make_slug(result_data["headline"] or victor_headline)
            base_slug = slug
            counter = 1
            while db.session.query(BobStory).filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1

            primary_category = bob_categories[0]
            extra_categories = bob_categories[1:] if len(bob_categories) > 1 else []

            story = BobStory(
                title=result_data["headline"] or victor_headline,
                slug=slug,
                content=result_data["body"],
                summary=result_data["summary"],
                seo_keywords=result_data["seo_keywords"] or victor_keywords,
                seo_description=result_data["seo_description"] or "",
                category=primary_category,
                extra_categories=",".join(extra_categories) if extra_categories else "",
                source_article_url=f"profoundd://verify/{slug}",
                source_article_title=victor_headline,
                source_name="NewsRoom Bob (via Victor)",
            )
            db.session.add(story)
            db.session.commit()

            # Index to ES — one doc per selected category
            try:
                engine = SearchEngine(config.ELASTICSEARCH_URL)
                es_doc = story.to_es_doc()
                es_doc["tags"] = ["verification", "bob-story", "fact-check"]
                engine.index_article(es_doc)

                for extra_cat in extra_categories:
                    extra_doc = dict(es_doc)
                    extra_doc["category"] = extra_cat
                    extra_doc["url"] = f"profoundd://bob/{extra_cat}/{slug}"
                    engine.index_article(extra_doc)

                flash(f"Bob wrote '{story.title}' — published to {len(bob_categories)} {'category' if len(bob_categories) == 1 else 'categories'}.", "success")
            except Exception as e:
                flash(f"Saved but failed to index: {e}", "warning")

            return redirect(url_for("admin.bob_stories_list"))

        elif step == "publish":
            # Publish Victor's verification report directly
            headline = request.form.get("headline", "").strip()
            report = request.form.get("report", "").strip()
            summary = request.form.get("summary", "").strip()
            seo_keywords = request.form.get("seo_keywords", "").strip()
            claim_text = request.form.get("claim_text", "").strip()
            publish_categories = request.form.getlist("publish_categories")

            if not publish_categories:
                publish_categories = ["legislative"]

            if not headline or not report:
                flash("Headline and report are required.", "error")
                return redirect(url_for("admin.verify_claim"))

            slug = make_slug(headline)
            base_slug = slug
            counter = 1
            while db.session.query(BobStory).filter_by(slug=slug).first():
                slug = f"{base_slug}-{counter}"
                counter += 1

            primary_category = publish_categories[0]
            extra_categories = publish_categories[1:] if len(publish_categories) > 1 else []

            story = BobStory(
                title=headline,
                slug=slug,
                content=report,
                summary=summary,
                seo_keywords=seo_keywords,
                seo_description=summary[:290] if summary else "",
                category=primary_category,
                extra_categories=",".join(extra_categories) if extra_categories else "",
                source_article_url=f"profoundd://verify/{slug}",
                source_article_title=headline,
                source_name="Victor the Verifier",
            )
            db.session.add(story)
            db.session.commit()

            # Index to ES — one doc per selected category
            try:
                engine = SearchEngine(config.ELASTICSEARCH_URL)
                es_doc = story.to_es_doc()
                es_doc["tags"] = ["verification", "dual-perspective", "fact-check"]
                engine.index_article(es_doc)

                for extra_cat in extra_categories:
                    extra_doc = dict(es_doc)
                    extra_doc["category"] = extra_cat
                    extra_doc["url"] = f"profoundd://bob/{extra_cat}/{slug}"
                    engine.index_article(extra_doc)

                flash(f"Published verification: '{headline}' to {len(publish_categories)} {'category' if len(publish_categories) == 1 else 'categories'}.", "success")
            except Exception as e:
                flash(f"Saved but failed to index: {e}", "warning")

            return redirect(url_for("admin.bob_stories_list"))

    return render_template("admin/verify.html",
                           categories=CATEGORIES,
                           has_ai_key=has_ai_key,
                           result=None,
                           claim_text="",
                           claims_data=None,
                           evidence=None,
                           total_evidence=0)


# ------------------------------------------------------------------ #
#  OSM Local Business Import                                          #
# ------------------------------------------------------------------ #

@admin_bp.route("/osm")
@login_required
def osm_manage():
    """OSM business data management page."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    stats = engine.get_business_stats() if engine.is_available() else {
        "total": 0, "by_state": {}, "by_category": {}, "by_city": {},
    }
    last_import = SiteSetting.get("osm_last_import", "Never")

    from profoundd.crawler.osm_crawler import SUPPORTED_STATES
    return render_template("admin/osm_import.html",
                           stats=stats,
                           supported_states=SUPPORTED_STATES,
                           last_import=last_import)


@admin_bp.route("/osm/import", methods=["POST"])
@login_required
def osm_import_all():
    """Trigger OSM import for all supported states in background."""
    import threading
    from flask import current_app

    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        flash("Elasticsearch is not available. Cannot import.", "error")
        return redirect(url_for("admin.osm_manage"))

    app = current_app._get_current_object()

    def run_import():
        with app.app_context():
            from profoundd.crawler.osm_crawler import import_all_states
            data_dir = app.config.get("OSM_DATA_DIR", "data/osm")
            results = import_all_states(engine, data_dir)
            total = sum(r.get("indexed", 0) for r in results)
            SiteSetting.set(
                "osm_last_import",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            )
            db.session.commit()
            app.logger.info("OSM import complete: %d businesses indexed across %d states",
                            total, len(results))

    threading.Thread(target=run_import, daemon=True).start()
    flash("OSM import started in background for all supported states.", "success")
    return redirect(url_for("admin.osm_manage"))


@admin_bp.route("/osm/import/<state_key>", methods=["POST"])
@login_required
def osm_import_state(state_key):
    """Trigger OSM import for a single state in background."""
    import threading
    from flask import current_app
    from profoundd.crawler.osm_crawler import SUPPORTED_STATES

    if state_key not in SUPPORTED_STATES:
        flash(f"Unknown state: {state_key}", "error")
        return redirect(url_for("admin.osm_manage"))

    engine = SearchEngine(config.ELASTICSEARCH_URL)
    if not engine.is_available():
        flash("Elasticsearch is not available.", "error")
        return redirect(url_for("admin.osm_manage"))

    app = current_app._get_current_object()

    def run_import():
        with app.app_context():
            from profoundd.crawler.osm_crawler import import_state
            data_dir = app.config.get("OSM_DATA_DIR", "data/osm")
            engine.create_business_index()
            result = import_state(engine, state_key, data_dir)
            SiteSetting.set(
                "osm_last_import",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            )
            db.session.commit()
            if result:
                app.logger.info("OSM import %s: %d indexed", state_key, result.get("indexed", 0))

    threading.Thread(target=run_import, daemon=True).start()
    flash(f"OSM import started for {SUPPORTED_STATES[state_key]['label']}.", "success")
    return redirect(url_for("admin.osm_manage"))
