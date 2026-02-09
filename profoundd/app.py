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
from profoundd.utils.models import db, AdminUser, SearchLog
from profoundd.search.engine import SearchEngine
from profoundd.admin.routes import admin_bp

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

    # Init extensions
    db.init_app(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "admin.login"

    @login_manager.user_loader
    def load_user(user_id):
        return AdminUser.query.get(int(user_id))

    # Register blueprints
    app.register_blueprint(admin_bp)

    # Initialize search engine
    search_engine = SearchEngine(app.config.get("ELASTICSEARCH_URL", "http://localhost:9200"))

    # Create tables and default admin
    with app.app_context():
        os.makedirs("data", exist_ok=True)
        db.create_all()
        _ensure_admin(app.config)

    # --- Routes ---

    @app.route("/")
    def index():
        """Homepage with search bar and category cards."""
        trending = []
        if search_engine.is_available():
            trending = search_engine.get_trending(size=6)
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

    @app.route("/api/search")
    def api_search():
        """JSON API for search (for future apps/integrations)."""
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
    if AdminUser.query.count() == 0:
        admin = AdminUser(username=config.get("ADMIN_USERNAME", "admin"))
        admin.set_password(config.get("ADMIN_PASSWORD", "changeme"))
        db.session.add(admin)
        db.session.commit()
        logger.info("Created default admin user: %s", admin.username)


# Entry point
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
