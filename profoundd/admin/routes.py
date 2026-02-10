"""
Admin panel routes for managing sources, rankings, and monitoring.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify

from profoundd.utils.models import db, Source, Article, AdminUser, SearchLog, CrawlLog, SourceSubmission, SiteSetting
from profoundd.config.settings import get_config
from profoundd.config.sources import ALL_SOURCES, CATEGORIES
from profoundd.search.engine import SearchEngine
from profoundd.crawler.feed_crawler import FeedCrawler

logger = logging.getLogger(__name__)
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
                           recent_searches=recent_searches,
                           category_stats=category_stats,
                           categories=CATEGORIES)


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
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        category = request.form.get("category", "news")
        source_name = request.form.get("source_name", "").strip() or "Profoundd Research"

        if not title or not content:
            flash("Title and content are required.", "error")
            return redirect(url_for("admin.research_library"))

        if not engine.is_available():
            flash("Elasticsearch is not available.", "error")
            return redirect(url_for("admin.research_library"))

        engine.create_index()

        # Build a unique URL for this document
        import hashlib
        doc_hash = hashlib.md5(f"{title}{content[:200]}".encode()).hexdigest()[:12]
        doc_url = f"profoundd://research/{doc_hash}"

        # Extract first paragraph or 300 chars as summary
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        summary = lines[0][:500] if lines else title

        article_data = {
            "title": title,
            "summary": summary,
            "content": content,
            "author": "Admin",
            "category": category,
            "source_name": source_name,
            "source_credibility": 9,  # Admin-curated gets top credibility
            "url": doc_url,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }

        result = engine.index_article(article_data)
        if result:
            flash(f"Research document '{title}' indexed successfully.", "success")
        else:
            flash("Failed to index document.", "error")

        return redirect(url_for("admin.research_library"))

    return render_template("admin/research.html", categories=CATEGORIES)


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
        flash("Settings saved.", "success")
        return redirect(url_for("admin.ai_settings"))

    return render_template("admin/ai_settings.html",
                           anthropic_key=SiteSetting.get("ai_anthropic_key", ""),
                           anthropic_model=SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-5-20250929"),
                           xai_key=SiteSetting.get("ai_xai_key", ""),
                           xai_model=SiteSetting.get("ai_xai_model", "grok-2-latest"),
                           default_provider=SiteSetting.get("ai_default_provider", "anthropic"),
                           searxng_url=SiteSetting.get("searxng_url", ""),
                           categories=CATEGORIES)


@admin_bp.route("/analyze-url", methods=["GET", "POST"])
@login_required
def analyze_url():
    """Analyze a URL with AI, review, and index into search."""
    from profoundd.search.ai_analyzer import (
        fetch_url_content, analyze_with_anthropic, analyze_with_xai
    )

    anthropic_key = SiteSetting.get("ai_anthropic_key", "")
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
