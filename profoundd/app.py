"""
Main Flask application for Profoundd search engine.
"""
import os
import logging
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_login import LoginManager

from profoundd.config.settings import get_config
from profoundd.config.sources import CATEGORIES
from profoundd.utils.models import db, AdminUser, SearchLog, SourceSubmission
from profoundd.search.engine import SearchEngine
from profoundd.admin.routes import admin_bp
from profoundd.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def create_app(config_override=None):
    """Application factory."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "frontend", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "frontend", "static"),
    )

    # Load config
    config = config_override or get_config()
    app.config.from_object(config)
    CORS(app)

    # Setup logging
    setup_logging()

    # Init extensions
    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "admin.login"

    @login_manager.user_loader
    def load_user(user_id):
        return AdminUser.query.get(int(user_id))

    # Register blueprints
    app.register_blueprint(admin_bp)

    # Make categories available to all templates
    @app.context_processor
    def inject_categories():
        return {"categories": CATEGORIES}

    # Initialize search engine
    search_engine = SearchEngine(app.config.get("ELASTICSEARCH_URL", "http://localhost:9200"))

    # Create tables and default admin
    with app.app_context():
        from profoundd.config.settings import BASE_DIR
        os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
        db.create_all()
        _ensure_admin(app.config)

    # Start background scheduler (only in production, not in testing)
    if not app.config.get("TESTING"):
        try:
            from profoundd.utils.scheduler import init_scheduler
            init_scheduler(app)
        except Exception as e:
            logger.warning("Could not start scheduler: %s", e)

    # --- Routes ---

    @app.route("/")
    def index():
        """Homepage with search bar and category cards."""
        trending = []
        if search_engine.is_available():
            trending = search_engine.get_trending(size=9)
        return render_template("index.html", categories=CATEGORIES, trending=trending)

    @app.route("/search")
    def search():
        """Main search endpoint."""
        query = request.args.get("q", "").strip()
        category = request.args.get("category", "all")
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort", "relevance")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")

        if not query:
            return render_template("search.html", results=None, categories=CATEGORIES,
                                   query="", category=category)

        results = search_engine.search(
            query=query,
            category=category,
            page=page,
            sort_by=sort_by,
            date_from=date_from,
            date_to=date_to,
        )

        # Log the search
        log = SearchLog(
            query=query,
            category=category,
            results_count=results.get("total", 0),
            ip_address=request.remote_addr,
        )
        db.session.add(log)
        db.session.commit()

        return render_template("search.html", results=results, categories=CATEGORIES,
                               query=query, category=category, sort_by=sort_by)

    @app.route("/category/<category_name>")
    def category_page(category_name):
        """Browse a specific category."""
        if category_name not in CATEGORIES:
            return render_template("404.html", categories=CATEGORIES), 404

        trending = []
        if search_engine.is_available():
            trending = search_engine.get_trending(category=category_name, size=20, hours=72)

        cat_info = CATEGORIES[category_name]
        return render_template("category.html", category_name=category_name,
                               category=cat_info, articles=trending, categories=CATEGORIES)

    # --- API Routes ---

    @app.route("/api/search")
    def api_search():
        """JSON API for search."""
        query = request.args.get("q", "").strip()
        category = request.args.get("category", "all")
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort", "relevance")

        if not query:
            return jsonify({"error": "Query parameter 'q' is required"}), 400

        results = search_engine.search(query=query, category=category, page=page, sort_by=sort_by)
        return jsonify(results)

    @app.route("/api/trending")
    def api_trending():
        """Get trending articles."""
        category = request.args.get("category", "all")
        hours = request.args.get("hours", 24, type=int)
        trending = search_engine.get_trending(category=category, hours=hours)
        return jsonify({"articles": trending, "category": category})

    @app.route("/api/suggest")
    def api_suggest():
        """Search suggestions based on indexed titles."""
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify({"suggestions": []})

        suggestions = search_engine.get_suggestions(query)
        return jsonify({"suggestions": suggestions})

    @app.route("/api/sources")
    def api_sources():
        """List all configured categories and source counts."""
        from profoundd.utils.models import Source
        cat_data = {}
        for key, cat in CATEGORIES.items():
            count = db.session.query(Source).filter_by(category=key, is_active=True).count()
            cat_data[key] = {"label": cat["label"], "sources": count}
        return jsonify({"categories": cat_data})

    @app.route("/health")
    def health_check():
        """Health check endpoint for monitoring."""
        es_ok = search_engine.is_available()
        es_stats = search_engine.get_stats() if es_ok else {}
        status = "healthy" if es_ok else "degraded"
        code = 200 if es_ok else 503

        return jsonify({
            "status": status,
            "version": "1.0.0",
            "elasticsearch": {
                "available": es_ok,
                "articles": es_stats.get("total_articles", 0),
            },
            "categories": len(CATEGORIES),
        }), code

    @app.route("/submit", methods=["GET", "POST"])
    def submit_source():
        """Public source submission form — just a URL is enough."""
        if request.method == "POST":
            from urllib.parse import urlparse
            url = request.form.get("url", "").strip()
            if not url:
                flash("Please enter a URL.", "error")
                return redirect("/submit")
            # Auto-extract site name from domain
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path
            name = domain.replace("www.", "").split(".")[0].title()
            submission = SourceSubmission(
                name=name,
                url=url,
                category="news",  # admin assigns the real category on review
                reason=request.form.get("reason", "").strip(),
                submitted_by=request.form.get("submitted_by", "").strip() or "Anonymous",
                ip_address=request.remote_addr,
            )
            db.session.add(submission)
            db.session.commit()
            flash("Thanks! Your suggestion has been submitted for review.", "success")
            return redirect("/submit")
        return render_template("submit.html", categories=CATEGORIES)

    @app.route("/about")
    def about():
        return render_template("about.html", categories=CATEGORIES)

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html", categories=CATEGORIES), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    return app


def _ensure_admin(config):
    """Create default admin user if none exists."""
    if db.session.query(AdminUser).count() == 0:
        admin = AdminUser(username=config.get("ADMIN_USERNAME", "admin"))
        admin.set_password(config.get("ADMIN_PASSWORD", "changeme"))
        db.session.add(admin)
        db.session.commit()
        logger.info("Created default admin user: %s", admin.username)


# Entry point
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
