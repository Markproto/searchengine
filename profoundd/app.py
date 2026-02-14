"""
Main Flask application for Profoundd search engine.
"""
import os
import logging
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, flash, redirect, Response, url_for
from flask_cors import CORS
from flask_login import LoginManager

from profoundd.config.settings import get_config
from profoundd.config.sources import CATEGORIES
from profoundd.utils.models import db, AdminUser, SearchLog, Source, SourceSubmission, SiteSetting, BobStory
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

    # Make categories and SEO tags available to all templates
    @app.context_processor
    def inject_globals():
        # Determine which page we're on for SEO lookup
        page_key = "home"
        if request.path.startswith("/about"):
            page_key = "about"
        elif request.path.startswith("/search"):
            page_key = "search"
        elif request.path.startswith("/submit"):
            page_key = "submit"
        elif request.path.startswith("/newsroom"):
            page_key = "newsroom"

        seo_title = SiteSetting.get(f"seo_{page_key}_title", "")
        seo_desc = SiteSetting.get(f"seo_{page_key}_description", "")
        page_kw = SiteSetting.get(f"seo_{page_key}_keywords", "")
        global_kw = SiteSetting.get("seo_global_keywords", "")
        combined_kw = ", ".join(filter(None, [page_kw, global_kw]))

        # Canonical URL
        domain = app.config.get("DOMAIN", "profoundd.com")
        canonical_url = f"https://{domain}{request.path}"
        if request.query_string:
            canonical_url += f"?{request.query_string.decode('utf-8')}"

        # OG image (admin-configurable, fallback to default)
        og_image = SiteSetting.get("seo_og_image", f"https://{domain}/static/images/og-default.png")

        # Google Search Console verification
        gsc_verification = SiteSetting.get("google_site_verification", "")

        return {
            "categories": CATEGORIES,
            "seo_title": seo_title,
            "seo_description": seo_desc,
            "seo_keywords": combined_kw,
            "canonical_url": canonical_url,
            "og_image": og_image,
            "site_domain": domain,
            "gsc_verification": gsc_verification,
            "is_admin": request.path.startswith("/admin"),
        }

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
            if not trending:
                trending = search_engine.get_trending(size=9, hours=720)
            if not trending:
                trending = search_engine.get_latest(size=9)
        return render_template("index.html", categories=CATEGORIES, trending=trending)

    @app.route("/search")
    def search():
        """Main search endpoint."""
        from profoundd.search.external_providers import (
            fetch_all_enhanced, fetch_searxng, fetch_brave_web, boost_known_domains,
        )

        query = request.args.get("q", "").strip()
        category = request.args.get("category", "all")
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort", "relevance")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")

        if not query:
            return render_template("search.html", results=None, categories=CATEGORIES,
                                   query="", category=category, enhanced_providers=set(),
                                   web_fallback=False)

        results = search_engine.search(
            query=query,
            category=category,
            page=page,
            sort_by=sort_by,
            date_from=date_from,
            date_to=date_to,
        )

        # Fetch enhanced results from external providers (page 1 only)
        enhanced_providers = set()
        web_fallback = False
        if page == 1:
            enhanced_articles, enhanced_providers = fetch_all_enhanced(query, category)
            if enhanced_articles:
                # Insert enhanced results among the top results
                existing_urls = {a.get("url") for a in results.get("articles", [])}
                insert_pos = min(3, len(results.get("articles", [])))
                for ea in enhanced_articles:
                    if ea.get("url") not in existing_urls:
                        results["articles"].insert(insert_pos, ea)
                        insert_pos += 1
                results["enhanced_providers"] = list(enhanced_providers)

            # Web fallback: supplement with outside news when local results are
            # insufficient OR when relevance is weak (top score below threshold).
            # This ensures searches like "Lindsey Graham loses House" still show
            # relevant external articles even if 6 loosely-related local results exist.
            local_count = len(results.get("articles", []))
            top_score = results.get("top_score", 0)
            LOW_RELEVANCE_THRESHOLD = 15  # ES scores below this indicate weak matches
            needs_web = local_count < 5 or top_score < LOW_RELEVANCE_THRESHOLD
            if needs_web:
                web_results = []
                searxng_url = SiteSetting.get("searxng_url", "")
                if searxng_url:
                    web_results = fetch_searxng(query, searxng_url, max_results=10)
                if not web_results:
                    # Brave backup when SearXNG is down or returns nothing
                    web_results = fetch_brave_web(query, max_results=10)
                if web_results:
                    # Boost results from domains in Profoundd's source list
                    web_results = boost_known_domains(web_results)
                    web_fallback = True
                    existing_urls = {a.get("url") for a in results.get("articles", [])}
                    for wr in web_results:
                        if wr.get("url") not in existing_urls:
                            results["articles"].append(wr)
                            existing_urls.add(wr.get("url"))

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
                               query=query, category=category, sort_by=sort_by,
                               enhanced_providers=enhanced_providers,
                               web_fallback=web_fallback)

    @app.route("/category/<category_name>")
    def category_page(category_name):
        """Browse a specific category."""
        if category_name not in CATEGORIES:
            return render_template("404.html", categories=CATEGORIES), 404

        trending = []
        if search_engine.is_available():
            trending = search_engine.get_trending(category=category_name, size=20, hours=72)
            if not trending:
                trending = search_engine.get_trending(category=category_name, size=20, hours=720)
            if not trending:
                # Final fallback: just get latest articles, no time filter
                trending = search_engine.get_latest(category=category_name, size=20)

        cat_info = CATEGORIES[category_name]
        return render_template("category.html", category_name=category_name,
                               category=cat_info, articles=trending, categories=CATEGORIES)

    @app.route("/article/<doc_id>")
    def article_detail(doc_id):
        """Permalink page for Profoundd research articles."""
        article_url = f"profoundd://research/{doc_id}"
        article = search_engine.get_article(article_url)
        if not article:
            return render_template("404.html", categories=CATEGORIES), 404
        return render_template("article.html", article=article, doc_id=doc_id,
                               categories=CATEGORIES)

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
        cat_counts = search_engine.get_category_counts() if es_ok else {}
        status = "healthy" if es_ok else "degraded"
        code = 200 if es_ok else 503

        return jsonify({
            "status": status,
            "version": "1.0.0",
            "elasticsearch": {
                "available": es_ok,
                "articles": es_stats.get("total_articles", 0),
                "by_category": cat_counts,
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
            domain_clean = domain.replace("www.", "").lower()
            name = domain_clean.split(".")[0].title()
            # Check for duplicate submissions (exact URL or same domain)
            existing_url = db.session.query(SourceSubmission).filter(
                SourceSubmission.url == url
            ).first()
            if existing_url:
                flash("This URL has already been submitted. We're reviewing it!", "info")
                return redirect("/submit")
            existing_domain = db.session.query(SourceSubmission).filter(
                SourceSubmission.url.ilike(f"%{domain_clean}%")
            ).first()
            if existing_domain:
                flash("A source from this website has already been submitted. We're reviewing it!", "info")
                return redirect("/submit")
            # Also check if it's already an active source
            active_source = db.session.query(Source).filter(
                Source.url.ilike(f"%{domain_clean}%"),
                Source.is_active == True,
            ).first()
            if active_source:
                flash("Great news — we already crawl this source!", "info")
                return redirect("/submit")
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
        about_html = SiteSetting.get("about_page_html", "")
        return render_template("about.html", categories=CATEGORIES, about_html=about_html)

    # --- NewsRoom Bob public routes ---

    @app.route("/newsroom")
    def newsroom():
        """Public newsroom page listing all Bob stories."""
        page = request.args.get("page", 1, type=int)
        per_page = 12
        query = db.session.query(BobStory).filter_by(status="published")\
            .order_by(BobStory.published_at.desc())
        total = query.count()
        stories = query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page
        return render_template("newsroom.html", stories=stories, categories=CATEGORIES,
                               page=page, pages=pages, total=total)

    @app.route("/newsroom/<slug>")
    def bob_story(slug):
        """Individual Bob story page with full SEO."""
        story = db.session.query(BobStory).filter_by(slug=slug, status="published").first()
        if not story:
            return render_template("404.html", categories=CATEGORIES), 404
        return render_template("bob_story.html", story=story, categories=CATEGORIES)

    @app.route("/robots.txt")
    def robots_txt():
        """Serve robots.txt for search engine crawlers."""
        domain = app.config.get("DOMAIN", "profoundd.com")
        content = f"""User-agent: *
Allow: /
Allow: /search
Allow: /category/
Allow: /article/
Allow: /about
Allow: /submit
Allow: /newsroom
Disallow: /admin/
Disallow: /api/
Disallow: /health

Sitemap: https://{domain}/sitemap.xml
"""
        return Response(content, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        """Dynamic sitemap.xml with all public pages."""
        domain = app.config.get("DOMAIN", "profoundd.com")
        base = f"https://{domain}"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        urls = [
            {"loc": base + "/", "changefreq": "hourly", "priority": "1.0"},
            {"loc": base + "/about", "changefreq": "monthly", "priority": "0.3"},
            {"loc": base + "/submit", "changefreq": "monthly", "priority": "0.3"},
            {"loc": base + "/search", "changefreq": "daily", "priority": "0.7"},
        ]
        for key in CATEGORIES:
            urls.append({"loc": f"{base}/category/{key}", "changefreq": "hourly", "priority": "0.8"})

        # Add NewsRoom Bob stories to sitemap
        urls.append({"loc": base + "/newsroom", "changefreq": "daily", "priority": "0.7"})
        bob_stories = db.session.query(BobStory).filter_by(status="published")\
            .order_by(BobStory.published_at.desc()).limit(200).all()
        for story in bob_stories:
            urls.append({"loc": f"{base}/newsroom/{story.slug}", "changefreq": "weekly", "priority": "0.6"})

        xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for u in urls:
            xml_parts.append("  <url>")
            xml_parts.append(f"    <loc>{u['loc']}</loc>")
            xml_parts.append(f"    <lastmod>{today}</lastmod>")
            xml_parts.append(f"    <changefreq>{u['changefreq']}</changefreq>")
            xml_parts.append(f"    <priority>{u['priority']}</priority>")
            xml_parts.append("  </url>")
        xml_parts.append("</urlset>")

        return Response("\n".join(xml_parts), mimetype="application/xml")

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
