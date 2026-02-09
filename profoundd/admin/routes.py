"""
Admin panel routes for managing sources, rankings, and monitoring.
"""
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from profoundd.utils.models import db, Source, Article, AdminUser, SearchLog
from profoundd.config.settings import get_config
from profoundd.config.sources import ALL_SOURCES, CATEGORIES
from profoundd.search.engine import SearchEngine
from profoundd.crawler.feed_crawler import FeedCrawler

logger = logging.getLogger(__name__)
config = get_config()
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = AdminUser.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["admin_logged_in"] = True
            session["admin_user"] = username
            flash("Logged in successfully.", "success")
            return redirect(url_for("admin.dashboard"))

        flash("Invalid credentials.", "error")

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

    sources_count = Source.query.count()
    active_sources = Source.query.filter_by(is_active=True).count()
    articles_count = Article.query.count()
    recent_searches = SearchLog.query.order_by(SearchLog.searched_at.desc()).limit(20).all()

    # Category breakdown
    category_stats = {}
    for cat_key, cat_info in CATEGORIES.items():
        count = Source.query.filter_by(category=cat_key, is_active=True).count()
        category_stats[cat_key] = {"label": cat_info["label"], "count": count, "icon": cat_info["icon"]}

    return render_template("admin/dashboard.html",
                           es_stats=es_stats,
                           es_available=engine.is_available(),
                           sources_count=sources_count,
                           active_sources=active_sources,
                           articles_count=articles_count,
                           recent_searches=recent_searches,
                           category_stats=category_stats,
                           categories=CATEGORIES)


@admin_bp.route("/sources")
@login_required
def sources_list():
    """List all sources with their rankings."""
    category = request.args.get("category", "all")
    query = Source.query
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
    source = Source.query.get_or_404(source_id)

    if request.method == "POST":
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
        source.is_active = "is_active" in request.form
        db.session.commit()
        flash(f"Source '{source.name}' updated.", "success")
        return redirect(url_for("admin.sources_list"))

    return render_template("admin/source_form.html", source=source, categories=CATEGORIES)


@admin_bp.route("/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(source_id):
    source = Source.query.get_or_404(source_id)
    db.session.delete(source)
    db.session.commit()
    flash(f"Source '{source.name}' deleted.", "warning")
    return redirect(url_for("admin.sources_list"))


@admin_bp.route("/sources/seed", methods=["POST"])
@login_required
def seed_sources():
    """Populate sources from the default config."""
    added = 0
    for src in ALL_SOURCES:
        existing = Source.query.filter_by(url=src["url"]).first()
        if not existing:
            source = Source(
                name=src["name"],
                url=src["url"],
                category=src["category"],
                credibility=src.get("credibility", 5),
                feed_type=src.get("feed_type", "rss"),
            )
            db.session.add(source)
            added += 1
    db.session.commit()
    flash(f"Seeded {added} new sources from default config.", "success")
    return redirect(url_for("admin.sources_list"))


@admin_bp.route("/crawl", methods=["POST"])
@login_required
def trigger_crawl():
    """Trigger a manual crawl."""
    category = request.form.get("category", None)
    engine = SearchEngine(config.ELASTICSEARCH_URL)

    if not engine.is_available():
        flash("Elasticsearch is not available. Cannot crawl.", "error")
        return redirect(url_for("admin.dashboard"))

    engine.create_index()
    crawler = FeedCrawler(search_engine=engine)

    # Use database sources if available, otherwise defaults
    if Source.query.count() > 0:
        sources = Source.query.filter_by(is_active=True)
        if category and category != "all":
            sources = sources.filter_by(category=category)
        count = crawler.crawl_custom_sources(sources.all())
    else:
        count = crawler.crawl_all() if not category else len(crawler.crawl_category(category))

    flash(f"Crawl complete: {count} articles indexed.", "success")
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


# --- API endpoints for AJAX ---

@admin_bp.route("/api/stats")
@login_required
def api_stats():
    """Get stats as JSON."""
    engine = SearchEngine(config.ELASTICSEARCH_URL)
    return jsonify({
        "elasticsearch": engine.get_stats() if engine.is_available() else {},
        "sources": Source.query.count(),
        "active_sources": Source.query.filter_by(is_active=True).count(),
    })
