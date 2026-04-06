"""
Main Flask application for Profoundd search engine.
"""
import os
import json
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, flash, redirect, Response, url_for, make_response, session
from flask_cors import CORS
from flask_login import LoginManager

from profoundd.config.settings import get_config
from profoundd.config.sources import CATEGORIES
from profoundd.utils.models import db, AdminUser, SearchLog, Source, SourceSubmission, SiteSetting, BobStory, NewsroomNote, SourceNote, PageView, ArticleClick
from profoundd.search.engine import SearchEngine
from profoundd.search.ai_summary import generate_summary as ai_generate_summary, is_available as ai_is_available
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
    from profoundd.auth import auth_bp
    app.register_blueprint(auth_bp)

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

        # Batch-load all notes in one query each (avoids N+1 per-article DB hits)
        _newsroom_notes = {n.article_url: n for n in db.session.query(NewsroomNote).all()}
        _source_notes = {n.source_name: n for n in db.session.query(SourceNote).all()}

        def get_newsroom_note(url):
            """Look up an editorial note for an article URL."""
            if not url:
                return None
            return _newsroom_notes.get(url)

        def get_source_note(source_name):
            """Look up a credibility note for a content source."""
            if not source_name:
                return None
            return _source_notes.get(source_name)

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
            "get_newsroom_note": get_newsroom_note,
            "get_source_note": get_source_note,
            "user_logged_in": session.get("user_logged_in", False),
            "user_email": session.get("user_email", ""),
        }

    # --- Long-lived cache headers for static assets ---
    @app.after_request
    def set_static_cache(response):
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # --- Analytics: record page views ---
    from profoundd.utils.bot_detection import detect_bot

    @app.after_request
    def track_page_view(response):
        """Record bot page views server-side. Human views come from JS tracking."""
        path = request.path
        if (
            request.method != "GET"
            or path.startswith(("/static", "/api/", "/admin", "/health", "/robots", "/sitemap"))
            or response.status_code >= 400
        ):
            return response

        ua = (request.user_agent.string or "")[:500]
        is_bot = detect_bot(ua)

        # Only record bots here; humans are tracked via JS /api/analytics/track
        if not is_bot:
            return response

        visitor_id = request.cookies.get("profoundd_vid") or uuid.uuid4().hex
        try:
            pv = PageView(
                path=path[:1000],
                visitor_id=visitor_id,
                ip_address=request.remote_addr,
                user_agent=ua,
                referrer=(request.referrer or "")[:1000],
                is_bot=True,
            )
            db.session.add(pv)
            db.session.commit()
        except Exception:
            logger.exception("Failed to record bot page view for %s", path)
            db.session.rollback()

        return response

    # --- JS tracking endpoint: record human page views with full metadata ---
    @app.route("/api/analytics/track", methods=["POST"])
    def analytics_track():
        """Record a page view from client-side JS with session/visitor/screen data."""
        # Skip admin sessions
        if session.get("admin_logged_in"):
            return jsonify(ok=True, id=None)

        data = request.get_json(silent=True) or {}
        ua = (request.user_agent.string or "")[:500]

        visitor_id = (data.get("visitorId") or request.cookies.get("profoundd_vid") or uuid.uuid4().hex)[:64]

        try:
            pv = PageView(
                path=(data.get("path") or "/")[:1000],
                visitor_id=visitor_id,
                ip_address=request.remote_addr,
                user_agent=ua,
                referrer=(data.get("referrer") or "")[:1000],
                session_id=(data.get("sessionId") or "")[:64] or None,
                is_bot=detect_bot(ua),
                screen_width=data.get("screenWidth"),
                screen_height=data.get("screenHeight"),
                language=(data.get("language") or "")[:20] or None,
            )
            db.session.add(pv)
            db.session.commit()
            return jsonify(ok=True, id=pv.id)
        except Exception:
            logger.exception("Failed to record tracked page view")
            db.session.rollback()
            return jsonify(ok=False), 500

    # --- Duration update endpoint: called via sendBeacon on page unload ---
    @app.route("/api/analytics/duration", methods=["POST"])
    def analytics_duration():
        """Update page view duration via sendBeacon."""
        # sendBeacon sends text/plain, so handle both content types
        data = request.get_json(silent=True)
        if not data:
            try:
                data = json.loads(request.data)
            except Exception:
                return jsonify(ok=False), 400

        pv_id = data.get("id")
        duration = data.get("duration")
        if not pv_id or not isinstance(duration, (int, float)):
            return jsonify(ok=False), 400

        duration = max(1, min(3600, int(duration)))
        try:
            pv = db.session.get(PageView, pv_id)
            if pv:
                pv.duration = duration
                db.session.commit()
        except Exception:
            db.session.rollback()

        return jsonify(ok=True)

    # --- Click tracking: log outbound article clicks with source bias ---
    @app.route("/api/analytics/click", methods=["POST"])
    def analytics_click():
        """Record an outbound click on a search result for audience leaning."""
        if session.get("admin_logged_in"):
            return jsonify(ok=True)

        data = request.get_json(silent=True) or {}
        source_name = (data.get("source") or "")[:200]
        if not source_name:
            return jsonify(ok=False), 400

        # Look up bias_score from Source table
        src = db.session.query(Source).filter(Source.name == source_name).first()
        bias = src.bias_score if src else 5

        try:
            click = ArticleClick(
                visitor_id=(data.get("visitorId") or "")[:64] or None,
                session_id=(data.get("sessionId") or "")[:64] or None,
                source_name=source_name,
                bias_score=bias,
                article_url=(data.get("url") or "")[:1000],
                search_query=(data.get("query") or "")[:500] or None,
                category=(data.get("category") or "")[:50] or None,
            )
            db.session.add(click)
            db.session.commit()
        except Exception:
            logger.exception("Failed to record article click")
            db.session.rollback()

        return jsonify(ok=True)

    # Initialize search engine
    search_engine = SearchEngine(app.config.get("ELASTICSEARCH_URL", "http://localhost:9200"))
    # Ensure the business index exists alongside the articles index
    if search_engine.is_available():
        search_engine.create_business_index()

    # Create tables and default admin
    with app.app_context():
        from profoundd.config.settings import BASE_DIR
        os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
        db.create_all()
        # Add new columns if missing (SQLite doesn't add new columns via create_all)
        for col_sql in [
            "ALTER TABLE sources ADD COLUMN sponsor_tags VARCHAR(500) DEFAULT ''",
            "ALTER TABLE sources ADD COLUMN subcategory VARCHAR(100) DEFAULT ''",
            "ALTER TABLE bob_stories ADD COLUMN extra_categories VARCHAR(500) DEFAULT ''",
            "ALTER TABLE page_views ADD COLUMN session_id VARCHAR(64)",
            "ALTER TABLE page_views ADD COLUMN is_bot BOOLEAN DEFAULT 0",
            "ALTER TABLE page_views ADD COLUMN duration INTEGER",
            "ALTER TABLE page_views ADD COLUMN screen_width INTEGER",
            "ALTER TABLE page_views ADD COLUMN screen_height INTEGER",
            "ALTER TABLE page_views ADD COLUMN language VARCHAR(20)",
            "ALTER TABLE admin_users ADD COLUMN magic_token VARCHAR(128)",
            "ALTER TABLE admin_users ADD COLUMN magic_token_expires DATETIME",
        ]:
            try:
                db.session.execute(db.text(col_sql))
                db.session.commit()
            except Exception:
                db.session.rollback()
        # Ensure analytics indexes
        try:
            db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_page_views_session_id ON page_views (session_id)"))
            db.session.commit()
        except Exception:
            db.session.rollback()
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

    # --- Location API for local business search ---
    @app.route("/api/set-location", methods=["POST"])
    def set_location():
        """Store user location (GPS or ZIP) in a cookie for local business results."""
        import json as _json
        data = request.get_json(silent=True) or {}

        lat = data.get("lat")
        lon = data.get("lon")
        zip_code = data.get("zip", "").strip()

        if lat is not None and lon is not None:
            # GPS location
            loc = {"lat": float(lat), "lon": float(lon), "source": "gps"}
            # Reverse-lookup city from ZIP data (approximate)
            loc["city"], loc["state"] = _reverse_lookup_city(float(lat), float(lon))
        elif zip_code:
            # ZIP code lookup
            zip_data = _lookup_zip(zip_code)
            if not zip_data:
                return jsonify({"error": "Unknown ZIP code"}), 400
            loc = {
                "lat": zip_data["lat"], "lon": zip_data["lon"],
                "city": zip_data["city"], "state": zip_data["state"],
                "source": "zip",
            }
        else:
            return jsonify({"error": "Provide lat/lon or zip"}), 400

        resp = jsonify({"ok": True, "city": loc.get("city", ""), "state": loc.get("state", "")})
        resp.set_cookie(
            "profoundd_loc", _json.dumps(loc),
            max_age=365 * 24 * 3600,
            httponly=False,  # JS needs to read this for the location indicator
            samesite="Lax",
            secure=request.is_secure,
        )
        return resp

    @app.route("/api/clear-location", methods=["POST"])
    def clear_location():
        """Clear user location cookie."""
        resp = jsonify({"ok": True})
        resp.delete_cookie("profoundd_loc")
        return resp

    def _get_user_location():
        """Read user location from cookie. Returns dict with lat/lon or None."""
        import json as _json
        loc_cookie = request.cookies.get("profoundd_loc")
        if not loc_cookie:
            return None
        try:
            loc = _json.loads(loc_cookie)
            if "lat" in loc and "lon" in loc:
                return loc
        except Exception:
            pass
        return None

    def _lookup_zip(zip_code):
        """Look up a ZIP code from the static JSON file. Returns dict or None."""
        zip_path = os.path.join(os.path.dirname(__file__), "data", "zipcodes.json")
        if not hasattr(_lookup_zip, "_cache"):
            try:
                import json as _json
                with open(zip_path) as f:
                    _lookup_zip._cache = _json.load(f)
            except Exception:
                _lookup_zip._cache = {}
        return _lookup_zip._cache.get(zip_code)

    def _reverse_lookup_city(lat, lon):
        """Approximate reverse city lookup from ZIP data. Returns (city, state)."""
        if not hasattr(_reverse_lookup_city, "_index"):
            # Build a simple index on first call
            zip_path = os.path.join(os.path.dirname(__file__), "data", "zipcodes.json")
            try:
                import json as _json
                with open(zip_path) as f:
                    data = _json.load(f)
                _reverse_lookup_city._index = list(data.values())
            except Exception:
                _reverse_lookup_city._index = []

        # Find nearest ZIP centroid (simple distance, good enough)
        best = None
        best_dist = float("inf")
        for entry in _reverse_lookup_city._index:
            d = (entry["lat"] - lat) ** 2 + (entry["lon"] - lon) ** 2
            if d < best_dist:
                best_dist = d
                best = entry
        if best:
            return best["city"], best["state"]
        return "", ""

    @app.route("/search")
    def search():
        """Main search endpoint."""
        from profoundd.search.external_providers import (
            fetch_all_enhanced, fetch_searxng, fetch_brave_web, boost_known_domains,
            fetch_grokipedia,
        )
        from profoundd.utils.cache import cache_get, cache_set, make_search_key, make_ai_key, AI_SUMMARY_TTL

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

        # Check cache first (non-admin only — admins always get fresh results)
        is_admin = session.get("admin_logged_in", False)
        cache_key = make_search_key(query, category, page, sort_by, date_from, date_to)
        if not is_admin:
            cached_html = cache_get(cache_key)
            if cached_html:
                if isinstance(cached_html, (bytes, memoryview)):
                    cached_html = bytes(cached_html).decode("utf-8")
                resp = make_response(cached_html)
                resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                resp.headers["X-Cache"] = "HIT"
                return resp

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
        web_promoted = False
        if page == 1:
            # --- Fire all external fetches in parallel ---
            # Pre-read DB settings in main thread (Flask app context)
            searxng_url = SiteSetting.get("searxng_url", "")
            from profoundd.search.local_intent import detect_local_intent
            local_intent = detect_local_intent(query)

            with ThreadPoolExecutor(max_workers=4) as pool:
                fut_enhanced = pool.submit(fetch_all_enhanced, query, category)
                fut_grok = pool.submit(fetch_grokipedia, query, max_results=3)

                # Web results: SearXNG primary, Brave fallback
                def _fetch_web():
                    web = []
                    if searxng_url:
                        web = fetch_searxng(query, searxng_url, max_results=10)
                    if not web:
                        web = fetch_brave_web(query, max_results=10)
                    return web
                fut_web = pool.submit(_fetch_web)

                # Local business search (fast ES query, fine in thread)
                business_results = []
                def _fetch_biz():
                    if not local_intent["is_local"]:
                        return []
                    user_loc = _get_user_location()
                    biz_location = {"lat": user_loc["lat"], "lon": user_loc["lon"]} if user_loc else None
                    return search_engine.search_businesses(
                        query=local_intent["clean_query"],
                        location=biz_location,
                        radius_km=80, per_page=5,
                    )
                fut_biz = pool.submit(_fetch_biz)

                # Collect results as they complete
                try:
                    enhanced_articles, enhanced_providers = fut_enhanced.result(timeout=15)
                except Exception as e:
                    logger.warning("Enhanced providers failed: %s", e)
                    enhanced_articles, enhanced_providers = [], set()

                try:
                    grok_results = fut_grok.result(timeout=15)
                except Exception as e:
                    logger.warning("Grokipedia failed: %s", e)
                    grok_results = []

                try:
                    web_results = fut_web.result(timeout=15)
                except Exception as e:
                    logger.warning("Web results failed: %s", e)
                    web_results = []

                try:
                    business_results = fut_biz.result(timeout=15)
                except Exception as e:
                    logger.warning("Business search failed: %s", e)
                    business_results = []

            # --- Merge results (same logic as before, just uses parallel results) ---
            if enhanced_articles:
                existing_urls = {a.get("url") for a in results.get("articles", [])}
                insert_pos = min(3, len(results.get("articles", [])))
                for ea in enhanced_articles:
                    if ea.get("url") not in existing_urls:
                        results["articles"].insert(insert_pos, ea)
                        insert_pos += 1
                results["enhanced_providers"] = list(enhanced_providers)

            if grok_results:
                existing_urls = {a.get("url") for a in results.get("articles", [])}
                insert_pos = min(1, len(results.get("articles", [])))
                for gr in grok_results:
                    if gr.get("url") not in existing_urls:
                        results["articles"].insert(insert_pos, gr)
                        insert_pos += 1

            if web_results:
                web_results = boost_known_domains(web_results)
                web_fallback = True

                # Filter out blocked domains (admin-managed list)
                blocked_raw = SiteSetting.get("blocked_domains", "")
                if blocked_raw:
                    blocked_domains = [d.strip().lower() for d in blocked_raw.split("\n") if d.strip()]
                    web_results = [
                        wr for wr in web_results
                        if not any(bd in wr.get("url", "").lower() for bd in blocked_domains)
                    ]

                existing_urls = {a.get("url") for a in results.get("articles", [])}
                new_web = [wr for wr in web_results if wr.get("url") not in existing_urls]

                local_articles = results.get("articles", [])
                query_terms = set(query.lower().split())
                local_relevant = False
                for a in local_articles[:3]:
                    title_words = set(a.get("title", "").lower().split())
                    overlap = query_terms & title_words
                    if len(overlap) >= max(2, len(query_terms) - 1):
                        local_relevant = True
                        break

                if local_relevant:
                    blended = list(local_articles)
                    for i, wr in enumerate(new_web):
                        pos = min(3 + i * 4 + i, len(blended))
                        blended.insert(pos, wr)
                else:
                    blended = list(new_web) + list(local_articles)
                    web_promoted = True

                results["articles"] = blended

        # Log the search
        log = SearchLog(
            query=query,
            category=category,
            results_count=results.get("total", 0),
            ip_address=request.remote_addr,
        )
        db.session.add(log)
        db.session.commit()

        rendered_html = render_template("search.html", results=results, categories=CATEGORIES,
                               query=query, category=category, sort_by=sort_by,
                               enhanced_providers=enhanced_providers,
                               web_fallback=web_fallback,
                               web_promoted=web_promoted,
                               business_results=business_results if page == 1 else [],
                               user_location=_get_user_location())
        resp = make_response(rendered_html)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"

        # Cache rendered HTML for non-admin users (5 min TTL, persists to SQLite)
        if not is_admin:
            cache_set(cache_key, rendered_html)

        return resp

    @app.route("/api/ai-summary")
    def api_ai_summary():
        """Async endpoint for AI search summary. Cookie-limited to 5/day."""
        from profoundd.utils.cache import cache_get, cache_set, make_ai_key, AI_SUMMARY_TTL
        import json as _json

        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"answer": "", "error": "No query", "remaining": 0})

        # Cookie-based rate limit: 5 AI summaries per user per day (admins unlimited)
        is_admin = session.get("admin_logged_in", False)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cookie_val = request.cookies.get("ai_searches", "")
        ai_count = 0
        if cookie_val:
            parts = cookie_val.split(":", 1)
            if len(parts) == 2 and parts[0] == today:
                ai_count = int(parts[1])

        if not is_admin and ai_count >= 5:
            return jsonify({"answer": "", "error": "limit_reached", "remaining": 0})

        # Check cache for AI summary (24h TTL — saves API calls)
        ai_cache_key = make_ai_key(query)
        cached_ai = cache_get(ai_cache_key)
        if cached_ai:
            try:
                if isinstance(cached_ai, (bytes, memoryview)):
                    cached_ai = bytes(cached_ai).decode("utf-8")
                ai_result = _json.loads(cached_ai)
                ai_result["remaining"] = -1 if is_admin else (5 - ai_count)
                ai_result["cached"] = True
                return jsonify(ai_result)
            except Exception:
                pass  # Corrupted cache entry — regenerate

        # Get search results to summarize — prioritize web + Grokipedia over local index
        # so the AI sees the most relevant sources first
        articles = []

        # Grokipedia first (encyclopedic context)
        try:
            grok_articles = fetch_grokipedia(query, max_results=3)
            if grok_articles:
                articles.extend(grok_articles)
        except Exception:
            pass

        # Web results second (broad coverage)
        searxng_url = app.config.get("SEARXNG_URL", "")
        if searxng_url:
            try:
                web_articles = fetch_searxng(query, searxng_url, max_results=8)
                if web_articles:
                    articles.extend(web_articles)
            except Exception:
                pass

        # Local index last (may not be relevant to query)
        local_results = search_engine.search(query=query, category="all", page=1)
        local_articles = local_results.get("articles", [])
        if local_articles:
            articles.extend(local_articles[:5])

        if not articles:
            return jsonify({"answer": "", "error": "No results to summarize", "remaining": 5 - ai_count})

        ai_result = ai_generate_summary(query, articles)

        # Cache the AI result (24h TTL) — don't count cached hits against rate limit
        if ai_result.get("answer"):
            cache_set(ai_cache_key, _json.dumps(ai_result), ttl=AI_SUMMARY_TTL)

        # Increment counter and set cookie (skip for admins)
        if is_admin:
            ai_result["remaining"] = -1  # signals unlimited to frontend
            resp = make_response(jsonify(ai_result))
            resp.headers["Cache-Control"] = "no-cache, no-store"
            return resp

        ai_count += 1
        remaining = 5 - ai_count
        ai_result["remaining"] = remaining
        resp = make_response(jsonify(ai_result))
        resp.set_cookie("ai_searches", f"{today}:{ai_count}",
                        max_age=86400, samesite="Lax", httponly=False)
        return resp

    @app.route("/api/article-analysis", methods=["POST"])
    def api_article_analysis():
        """AI-powered article analysis endpoint."""
        from profoundd.search.ai_article_analysis import analyze_article
        from profoundd.utils.models import PublicUser, AIAnalysis

        data = request.get_json() or {}
        article_url = data.get("url", "")
        article_title = data.get("title", "")
        article_summary = data.get("summary", "")
        source_name = data.get("source", "")

        if not article_url:
            return jsonify({"error": "Missing article URL"}), 400

        # Determine if user is anonymous or logged in
        user_id = session.get("user_id")
        user = db.session.get(PublicUser, user_id) if user_id else None
        is_admin = session.get("admin_logged_in", False)

        # Anonymous rate limiting (5 free uses via cookie)
        anon_count = 0
        if not user and not is_admin:
            cookie = request.cookies.get("ai_analysis", "")
            try:
                anon_count = int(cookie) if cookie else 0
            except ValueError:
                anon_count = 0

            if anon_count >= 5:
                return jsonify({
                    "error": "login_required",
                    "message": "You've used your 5 free analyses. Log in and add your own API key to continue.",
                    "login_url": "/auth/login",
                }), 403

        # Determine which API key and provider to use
        if is_admin or (not user) or (user and not user.get_api_key()):
            # Use Profoundd's own key
            provider = "anthropic"
            api_key = ""
            try:
                from profoundd.utils.models import SiteSetting
                api_key = SiteSetting.get("ai_anthropic_key", "")
                if not api_key:
                    import os
                    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            except Exception:
                import os
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")

            if not api_key:
                return jsonify({"error": "AI service not configured"}), 500

            # If user is logged in but no key, still check if they've exceeded free tier
            if user and user.ai_uses_count >= 5 and not user.get_api_key():
                return jsonify({
                    "error": "api_key_required",
                    "message": "You've used your 5 free analyses. Add your own API key in settings to continue.",
                    "settings_url": "/auth/settings",
                }), 403

            model = "claude-haiku-4-5-20251001"
        else:
            # Use user's own key
            provider = user.api_provider
            api_key = user.get_api_key()
            model = None  # use provider default

        # Run analysis
        result = analyze_article(
            url=article_url,
            title=article_title,
            summary=article_summary,
            source_name=source_name,
            provider=provider,
            api_key=api_key,
            model=model,
        )

        if result.get("error"):
            return jsonify(result), 500

        # Save to DB
        try:
            analysis = AIAnalysis(
                user_id=user.id if user else None,
                article_url=article_url,
                article_title=article_title,
                query_text=article_summary[:500] if article_summary else "",
                analysis_text=result.get("raw_text", ""),
                provider_used=provider,
                model_used=model or "",
                sentiment=result.get("sentiment", ""),
                bias_notes=result.get("bias_notes", ""),
            )
            db.session.add(analysis)
            if user:
                user.ai_uses_count = (user.ai_uses_count or 0) + 1
            db.session.commit()
        except Exception as e:
            logger.warning("Failed to save AI analysis: %s", e)
            try:
                db.session.rollback()
            except Exception:
                pass

        # Update anonymous cookie counter
        resp = make_response(jsonify(result))
        if not user and not is_admin:
            resp.set_cookie("ai_analysis", str(anon_count + 1),
                            max_age=86400 * 365, samesite="Lax", httponly=False)

        return resp

    @app.route("/category/<category_name>")
    def category_page(category_name):
        """Browse a specific category with pagination."""
        if category_name not in CATEGORIES:
            return render_template("404.html", categories=CATEGORIES), 404

        page = request.args.get("page", 1, type=int)
        # Default to "elections" for prediction markets, "all" for everything else
        default_sub = "elections" if category_name == "polymarket" and "sub" not in request.args else "all"
        subcategory = request.args.get("sub", default_sub)
        per_page = 20

        articles = []
        total = 0
        pages = 0

        # Polls category — API-driven with interactive state map
        if category_name == "polls":
            from profoundd.search.polls_provider import get_polls_for_category, get_state_color, STATE_NAMES
            from profoundd.search.external_providers import fetch_polymarket
            selected_state = request.args.get("state", None)
            national_polls, state_map, national_races, pollster_ratings = get_polls_for_category(
                state=selected_state, max_results=30
            )
            # Build state colors for map
            state_colors = {}
            for st, st_polls in state_map.items():
                state_colors[st] = get_state_color(st_polls)

            # Fetch prediction market odds for comparison
            market_odds = fetch_polymarket(query="election", subcategory="elections", max_results=10)

            cat_info = CATEGORIES[category_name]
            return render_template("polls.html", category_name=category_name,
                                   category=cat_info,
                                   national_polls=national_polls,
                                   national_races=national_races,
                                   state_map=state_map,
                                   state_colors=state_colors,
                                   state_names=STATE_NAMES,
                                   selected_state=selected_state,
                                   pollster_ratings=pollster_ratings,
                                   market_odds=market_odds,
                                   categories=CATEGORIES)

        # Prediction Markets is API-only (no indexed articles) — aggregate multiple sources
        if category_name == "polymarket":
            from profoundd.search.external_providers import (
                fetch_polymarket, fetch_manifold, fetch_predictit,
                fetch_manifold_by_state, fetch_predictit_by_state,
                ABBREV_TO_STATE_NAME,
            )
            selected_state = request.args.get("state", None)
            sub = subcategory if subcategory != "all" else None

            if selected_state:
                # State-specific view: fetch markets for this state from all providers
                polymarket_articles = fetch_polymarket(query=ABBREV_TO_STATE_NAME.get(selected_state, ""), max_results=10)
                polymarket_articles = [a for a in polymarket_articles if a.get("_state") == selected_state or selected_state.lower() in a.get("title", "").lower()]
                manifold_articles = fetch_manifold_by_state(selected_state, max_results=10)
                predictit_articles = fetch_predictit_by_state(selected_state, max_results=15)
            else:
                polymarket_articles = fetch_polymarket(query="", subcategory=sub, max_results=15)
                manifold_articles = fetch_manifold(query="", max_results=10)
                predictit_articles = []
                if not sub or sub == "elections":
                    predictit_articles = fetch_predictit(query="", max_results=8)

            # Build state map for the SVG (which states have markets)
            all_for_states = fetch_predictit(query="", max_results=251)
            state_market_counts = {}
            for a in all_for_states:
                st = a.get("_state", "")
                if st:
                    state_market_counts[st] = state_market_counts.get(st, 0) + 1

            articles = polymarket_articles
            total = len(polymarket_articles) + len(manifold_articles) + len(predictit_articles)
            pages = 1

            cat_info = CATEGORIES[category_name]
            return render_template("category.html", category_name=category_name,
                                   category=cat_info, articles=articles,
                                   polymarket_articles=polymarket_articles,
                                   manifold_articles=manifold_articles,
                                   predictit_articles=predictit_articles,
                                   selected_state=selected_state,
                                   state_market_counts=state_market_counts,
                                   state_names=ABBREV_TO_STATE_NAME,
                                   page=page, pages=pages, total=total,
                                   subcategory=subcategory,
                                   categories=CATEGORIES)

        elif search_engine.is_available():
            results = search_engine.search(
                query="",
                category=category_name,
                subcategory=subcategory if subcategory != "all" else None,
                page=page,
                per_page=per_page,
                sort_by="date",
            )
            articles = results.get("articles", [])
            total = results.get("total", 0)
            pages = results.get("pages", 0)

        cat_info = CATEGORIES[category_name]
        return render_template("category.html", category_name=category_name,
                               category=cat_info, articles=articles,
                               page=page, pages=pages, total=total,
                               subcategory=subcategory,
                               categories=CATEGORIES)

    @app.route("/article/<doc_id>")
    def article_detail(doc_id):
        """Permalink page for Profoundd research articles."""
        article_url = f"profoundd://research/{doc_id}"
        article = search_engine.get_article(article_url)
        if not article:
            return render_template("404.html", categories=CATEGORIES), 404
        # Related articles (same category, recent)
        related = []
        if search_engine.is_available() and article.get("category"):
            try:
                related = search_engine.get_trending(category=article["category"], size=5, hours=720)
                related = [r for r in related if r.get("url") != article.get("url")][:4]
            except Exception:
                pass
        return render_template("article.html", article=article, doc_id=doc_id,
                               related=related, categories=CATEGORIES)

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

    # --- Feed API: topic-specific JSON feeds for external sites ---
    # Keyword lists that define each sub-topic within the markets/finance universe.
    FEED_TOPICS = {
        "stocks": {
            "label": "Stocks & Equities",
            "keywords": [
                "stock market", "stocks", "S&P 500", "Dow Jones", "Nasdaq",
                "NYSE", "equities", "equity market", "stock price",
                "earnings report", "IPO", "bull market", "bear market",
                "Wall Street", "stock rally", "stock crash", "shares",
                "dividend", "market cap", "trading", "stock exchange",
                "Russell 2000", "blue chip", "tech stocks", "growth stocks",
            ],
            "categories": ["markets", "finance"],
        },
        "crypto": {
            "label": "Cryptocurrency",
            "keywords": [
                "bitcoin", "ethereum", "crypto", "cryptocurrency",
                "blockchain", "altcoin", "defi", "NFT", "web3",
                "binance", "coinbase", "solana", "XRP", "ripple",
                "dogecoin", "stablecoin", "USDT", "USDC",
                "crypto exchange", "crypto regulation", "crypto market",
                "mining", "halving", "satoshi", "BTC", "ETH",
                "token", "smart contract", "decentralized",
            ],
            "categories": ["markets"],
        },
        "metals": {
            "label": "Precious Metals",
            "keywords": [
                "gold", "silver", "platinum", "palladium",
                "precious metals", "bullion", "gold price", "silver price",
                "gold spot", "silver spot", "comex", "gold mining",
                "silver mining", "gold reserve", "gold standard",
                "gold ETF", "silver ETF", "gold futures", "troy ounce",
                "numismatic", "gold bar", "silver bar", "gold coin",
                "central bank gold", "gold demand", "gold supply",
            ],
            "categories": ["markets", "finance"],
        },
        "markets": {
            "label": "All Markets",
            "keywords": [],  # empty = return all articles in these categories
            "categories": ["markets", "finance"],
        },
    }

    @app.route("/api/feed/<topic>")
    def api_feed(topic):
        """JSON feed for a specific topic (stocks, crypto, metals, markets).

        Designed for import by external sites like privacyfolio.com.

        Params:
            hours  - lookback window (default 72, max 720)
            limit  - max articles (default 30, max 100)
            format - 'json' (default) or 'rss'
        """
        topic_config = FEED_TOPICS.get(topic)

        # Also allow any existing category as a feed
        if not topic_config and topic in CATEGORIES:
            topic_config = {
                "label": CATEGORIES[topic]["label"],
                "keywords": [],
                "categories": [topic],
            }

        if not topic_config:
            available = list(FEED_TOPICS.keys()) + list(CATEGORIES.keys())
            return jsonify({
                "error": f"Unknown topic '{topic}'",
                "available_topics": list(FEED_TOPICS.keys()),
                "available_categories": list(CATEGORIES.keys()),
            }), 404

        hours = min(request.args.get("hours", 72, type=int), 720)
        limit = min(request.args.get("limit", 30, type=int), 100)
        fmt = request.args.get("format", "json")

        keywords = topic_config["keywords"]
        categories = topic_config["categories"]

        if keywords:
            articles = search_engine.get_topic_feed(
                keywords=keywords,
                categories=categories,
                hours=hours,
                size=limit,
            )
        else:
            # No keywords = just get latest from the category
            articles = []
            for cat in categories:
                articles.extend(search_engine.get_trending(category=cat, hours=hours, size=limit))
            # Sort by date descending and trim
            articles.sort(key=lambda a: a.get("published_at", ""), reverse=True)
            articles = articles[:limit]

        # Clean articles for export (strip internal fields)
        domain = app.config.get("DOMAIN", "profoundd.com")
        clean = []
        for a in articles:
            url = a.get("url", "")
            is_newsroom = url.startswith("profoundd://bob/")
            # Convert internal profoundd:// URLs to real HTTP links
            if is_newsroom:
                slug = url.replace("profoundd://bob/", "").split("/")[-1]
                url = f"https://{domain}/newsroom/{slug}"
            clean.append({
                "title": a.get("title", ""),
                "summary": a.get("summary", ""),
                "url": url,
                "source_name": "Profoundd NewsRoom" if is_newsroom else a.get("source_name", ""),
                "source_url": a.get("source_article_url", "") if is_newsroom else "",
                "source_credibility": a.get("source_credibility", 5),
                "category": a.get("category", ""),
                "published_at": a.get("published_at", ""),
                "image_url": a.get("image_url", ""),
                "is_newsroom": is_newsroom,
            })

        # NewsRoom stories lead the feed, then everything else by date
        clean.sort(key=lambda a: (not a["is_newsroom"], a.get("published_at", "")),
                   reverse=False)
        # Within each group (newsroom / non-newsroom), sort newest first
        newsroom = [a for a in clean if a["is_newsroom"]]
        others = [a for a in clean if not a["is_newsroom"]]
        newsroom.sort(key=lambda a: a.get("published_at", ""), reverse=True)
        others.sort(key=lambda a: a.get("published_at", ""), reverse=True)
        clean = newsroom + others

        if fmt == "rss":
            return _build_rss_feed(topic, topic_config["label"], clean)

        return jsonify({
            "topic": topic,
            "label": topic_config["label"],
            "count": len(clean),
            "hours": hours,
            "articles": clean,
        })

    def _build_rss_feed(topic, label, articles):
        """Build an RSS 2.0 XML feed from article list."""
        domain = app.config.get("DOMAIN", "profoundd.com")
        items = []
        for a in articles:
            pub_date = a.get("published_at", "")
            source_url = a.get("source_url", "")
            source_tag = f'<source url="{_xml_escape(source_url)}">{_xml_escape(a["source_name"])}</source>' if source_url else f'<source>{_xml_escape(a["source_name"])}</source>'
            newsroom_tag = "\n      <profoundd:newsroom>true</profoundd:newsroom>" if a.get("is_newsroom") else ""
            items.append(f"""    <item>
      <title>{_xml_escape(a['title'])}</title>
      <link>{_xml_escape(a['url'])}</link>
      <description>{_xml_escape(a['summary'][:500])}</description>
      {source_tag}
      <category>{_xml_escape(a['category'])}</category>
      {f'<pubDate>{pub_date}</pubDate>' if pub_date else ''}{newsroom_tag}
    </item>""")

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:profoundd="https://{domain}/ns/feed">
  <channel>
    <title>Profoundd - {_xml_escape(label)}</title>
    <link>https://{domain}</link>
    <description>{_xml_escape(label)} feed from Profoundd</description>
    <language>en-us</language>
{''.join(items)}
  </channel>
</rss>"""
        return Response(xml, mimetype="application/rss+xml")

    def _xml_escape(text):
        """Escape XML special characters."""
        if not text:
            return ""
        return (text.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

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
        # Related stories (same category, recent)
        related = []
        if search_engine.is_available() and story.category:
            try:
                related = search_engine.get_trending(category=story.category, size=5, hours=720)
                related = [r for r in related if r.get("title") != story.title][:4]
            except Exception:
                pass
        return render_template("bob_story.html", story=story, related=related, categories=CATEGORIES)

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
Sitemap: https://{domain}/news-sitemap.xml
"""
        return Response(content, mimetype="text/plain")

    @app.route("/news-sitemap.xml")
    def news_sitemap_xml():
        """Google News sitemap — articles from last 48 hours."""
        from html import escape
        domain = app.config.get("DOMAIN", "profoundd.com")
        base = f"https://{domain}"

        xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        xml_parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
                         ' xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">')

        # Bob stories from last 48h
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        stories = db.session.query(BobStory).filter(
            BobStory.status == "published",
            BobStory.published_at >= cutoff,
        ).order_by(BobStory.published_at.desc()).limit(100).all()

        for story in stories:
            pub_date = story.published_at.strftime("%Y-%m-%dT%H:%M:%S+00:00") if story.published_at else ""
            keywords = escape(story.seo_keywords or story.category or "")
            xml_parts.append("  <url>")
            xml_parts.append(f"    <loc>{base}/newsroom/{story.slug}</loc>")
            xml_parts.append("    <news:news>")
            xml_parts.append("      <news:publication>")
            xml_parts.append("        <news:name>Profoundd</news:name>")
            xml_parts.append("        <news:language>en</news:language>")
            xml_parts.append("      </news:publication>")
            xml_parts.append(f"      <news:publication_date>{pub_date}</news:publication_date>")
            xml_parts.append(f"      <news:title>{escape(story.title)}</news:title>")
            if keywords:
                xml_parts.append(f"      <news:keywords>{keywords}</news:keywords>")
            xml_parts.append("    </news:news>")
            xml_parts.append("  </url>")

        # Recent ES articles from last 48h
        if search_engine.is_available():
            recent = search_engine.get_latest(size=100, hours=48)
            for article in recent:
                pub = (article.get("published_at") or "")[:19]
                if pub:
                    pub += "+00:00"
                xml_parts.append("  <url>")
                xml_parts.append(f"    <loc>{escape(article.get('url', ''))}</loc>")
                xml_parts.append("    <news:news>")
                xml_parts.append("      <news:publication>")
                xml_parts.append(f"        <news:name>{escape(article.get('source_name', 'Profoundd'))}</news:name>")
                xml_parts.append("        <news:language>en</news:language>")
                xml_parts.append("      </news:publication>")
                if pub:
                    xml_parts.append(f"      <news:publication_date>{pub}</news:publication_date>")
                xml_parts.append(f"      <news:title>{escape(article.get('title', ''))}</news:title>")
                cat = article.get("category", "")
                if cat:
                    xml_parts.append(f"      <news:keywords>{escape(cat)}</news:keywords>")
                xml_parts.append("    </news:news>")
                xml_parts.append("  </url>")

        xml_parts.append("</urlset>")
        return Response("\n".join(xml_parts), mimetype="application/xml")

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
