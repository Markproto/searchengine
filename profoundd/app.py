"""
Main Flask application for Profoundd search engine.
"""
import os
import json
import hashlib
import threading
import time
import uuid
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone
from urllib.parse import urlparse, quote

import requests as http_requests
from flask import Flask, render_template, request, jsonify, flash, redirect, Response, stream_with_context, url_for, make_response, session
from flask_cors import CORS
from flask_login import LoginManager

from profoundd.config.settings import get_config
from profoundd.config.sources import CATEGORIES
from profoundd.utils.models import db, AdminUser, SearchLog, Source, SourceSubmission, SiteSetting, BobStory, NewsroomNote, SourceNote, PageView, ArticleClick, DailyStats
from profoundd.search.engine import SearchEngine
from profoundd.search.ai_summary import generate_summary as ai_generate_summary, is_available as ai_is_available
from profoundd.admin.routes import admin_bp
from profoundd.utils.logging_config import setup_logging

logger = logging.getLogger(__name__)


def _hash_ip(ip):
    """Hash IP with daily salt — useful for vote dedup, not reversible."""
    salt = f"profoundd:{date.today().isoformat()}"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


def _clean_referrer(ref):
    """Strip query params from referrer to avoid leaking external search queries."""
    if not ref:
        return ""
    parsed = urlparse(ref)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:1000]


_download_rate_state = {}
_download_rate_lock = threading.Lock()


def _get_client_ip():
    """Real client IP — honor Cloudflare/proxy headers before falling back to remote_addr."""
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def _stream_file(path, chunk_size=65536):
    """Generator that streams a local file in chunks."""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _check_download_rate_limit(ip_hash, max_per_hour=20):
    now = time.time()
    cutoff = now - 3600
    with _download_rate_lock:
        dq = _download_rate_state.setdefault(ip_hash, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_per_hour:
            return False
        dq.append(now)
        return True


# Domains we don't want to auto-index from Brave/SearXNG fallback results
# (forums, social, shopping — low value for re-finding)
BRAVE_INDEX_SKIP_DOMAINS = {
    "reddit.com", "old.reddit.com", "quora.com", "pinterest.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "linkedin.com", "threads.net", "mastodon.social",
    "amazon.com", "ebay.com", "walmart.com", "etsy.com",
    "wikipedia.org", "en.wikipedia.org",
    "youtube.com", "youtu.be",
}

_BRAVE_SKIP_URL_PATTERNS = [
    "/search?", "/search/", "/tag/", "/tags/", "/category/",
    "/author/", "/user/", "/profile/",
]

def _should_index_brave_result(wr):
    """Quality filter for auto-indexing Brave/SearXNG results."""
    url = wr.get("url", "")
    if not url:
        return False
    try:
        netloc = urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return False
    if netloc in BRAVE_INDEX_SKIP_DOMAINS:
        return False
    # Subdomain check (e.g. en.wikipedia.org)
    for skip in BRAVE_INDEX_SKIP_DOMAINS:
        if netloc.endswith("." + skip):
            return False
    # Too-short content
    if len(wr.get("title", "") or "") < 20:
        return False
    if len(wr.get("summary", "") or "") < 50:
        return False
    # URL patterns that aren't articles
    lower_url = url.lower()
    for pat in _BRAVE_SKIP_URL_PATTERNS:
        if pat in lower_url:
            return False
    return True


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
    # Allow audio uploads up to 30 MB (Groq hard-limits at 25 MB; extra headroom)
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024
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

        # Search engine verification tokens (rendered as <meta> in base.html)
        gsc_verification = SiteSetting.get("google_site_verification", "")
        bing_verification = SiteSetting.get("bing_site_verification", "")
        yandex_verification = SiteSetting.get("yandex_verification", "")

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
            "bing_verification": bing_verification,
            "yandex_verification": yandex_verification,
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

    # --- Block SEO leech bots that ignore robots.txt ---
    import re as _re
    _BLOCKED_BOTS_RE = _re.compile(
        r"SemrushBot|AhrefsBot|MJ12bot|DotBot|SERankingBacklinksBot|BLEXBot|PetalBot|Bytespider",
        _re.IGNORECASE,
    )

    @app.before_request
    def block_leech_bots():
        ua = request.user_agent.string or ""
        if _BLOCKED_BOTS_RE.search(ua):
            return Response("Blocked. See /robots.txt", status=403, mimetype="text/plain")

    # --- Analytics: record page views ---
    from profoundd.utils.bot_detection import detect_bot

    @app.after_request
    def track_page_view(response):
        """Record page views server-side. Two tracks:
        1. DailyStats: privacy-friendly aggregate counter for ALL requests (no PII)
        2. PageView: detailed bot-only log (bots get full tracking, humans opt-in via JS)
        """
        path = request.path
        if (
            request.method != "GET"
            or path.startswith(("/static", "/api/", "/admin", "/health", "/robots", "/sitemap", "/opensearch", "/indexnow"))
            or response.status_code >= 400
        ):
            return response

        ua = (request.user_agent.string or "")[:500]
        is_bot = detect_bot(ua)

        # Privacy-friendly aggregate counter — no PII, just daily counts by page type
        try:
            from profoundd.utils.models import DailyStats
            if path == "/" or path == "":
                page_type = "home"
            elif path.startswith("/search"):
                page_type = "search"
            elif path.startswith("/category/"):
                page_type = "category"
            elif path.startswith("/epstein-docs/"):
                page_type = "epstein-doc"
            elif path.startswith("/newsroom"):
                page_type = "newsroom"
            elif path.startswith("/article/"):
                page_type = "article"
            elif path.startswith("/auth/"):
                page_type = "auth"
            else:
                page_type = "other"
            DailyStats.increment(date.today(), page_type, is_bot=is_bot)
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Detailed tracking: bots only (humans opt-in via JS /api/analytics/track)
        if not is_bot:
            return response

        visitor_id = request.cookies.get("profoundd_vid") or uuid.uuid4().hex
        try:
            pv = PageView(
                path=path[:1000],
                visitor_id=visitor_id,
                ip_address=_hash_ip(request.remote_addr),
                user_agent=ua,
                referrer=_clean_referrer(request.referrer),
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
                ip_address=_hash_ip(request.remote_addr),
                user_agent=ua,
                referrer=_clean_referrer(data.get("referrer") or ""),
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

    # --- Trending topics builder (from our own sources, not Google) ---

    def _build_trending_topics(engine):
        """
        Build trending topic chips from our own data:
        1. Our users' top searches (past 48h)
        2. Hot topics from our indexed article headlines (last 12h)
        Filters out mainstream media noise. Returns list of 12-15 topic strings.
        """
        import re
        from collections import Counter

        topics = []

        # Source 1: Our users' actual searches
        try:
            from profoundd.utils.models import SearchLog
            from sqlalchemy import func
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
            user_queries = (
                db.session.query(SearchLog.query, func.count(SearchLog.id))
                .filter(SearchLog.searched_at >= cutoff)
                .group_by(SearchLog.query)
                .order_by(func.count(SearchLog.id).desc())
                .limit(10)
                .all()
            )
            for q, _ in user_queries:
                q = q.strip()
                # Skip junk: URLs, test queries, single short words, numbers-only
                if (3 < len(q) < 60
                    and not q.startswith("http")
                    and not q.startswith("-")
                    and q.lower() not in ("test", "hello", "asdf")
                    and not re.match(r'^[\d\s\'"]+$', q)
                    and len(q.split()) <= 8):
                    topics.append(q)
        except Exception:
            pass

        # Source 2: Hot phrases from our indexed headlines (last 12h)
        try:
            result = engine.es.search(
                index=engine.index_name,
                body={
                    "size": 150,
                    "sort": [{"published_at": {"order": "desc"}}],
                    "query": {"range": {"published_at": {"gte": "now-12h"}}},
                    "_source": ["title", "source_name"],
                },
            )
            # Extract 2-3 word phrases from titles
            stopwords = {
                "the", "a", "an", "is", "are", "was", "were", "be", "been",
                "have", "has", "had", "do", "does", "did", "will", "would",
                "could", "should", "may", "might", "can", "to", "of", "in",
                "for", "on", "with", "at", "by", "from", "as", "into", "about",
                "and", "but", "or", "not", "so", "this", "that", "it", "its",
                "he", "she", "they", "we", "you", "what", "which", "who", "how",
                "when", "where", "why", "new", "says", "said", "just", "now",
                "more", "than", "also", "after", "over", "up", "out", "get",
                "been", "being", "going", "back", "us", "our", "his", "her",
                "their", "my", "your", "report", "reports", "news", "update",
            }
            # Skip articles from mainstream sources
            msm = {"cnn", "msnbc", "cnbc", "abc news", "nbc news", "cbs news",
                    "new york times", "washington post", "associated press", "reuters"}

            phrase_counter = Counter()
            for hit in result["hits"]["hits"]:
                src = hit["_source"].get("source_name", "").lower()
                if src in msm:
                    continue
                title = hit["_source"].get("title", "")
                words = [w for w in re.findall(r"[a-zA-Z']+", title)
                         if w.lower() not in stopwords and len(w) > 2]
                # 2-word phrases
                for i in range(len(words) - 1):
                    phrase = f"{words[i]} {words[i+1]}"
                    if len(phrase) > 6:
                        phrase_counter[phrase] += 1

            # Take phrases appearing 2+ times (trending across multiple sources)
            for phrase, count in phrase_counter.most_common(20):
                if count >= 2 and phrase not in topics and len(phrase.split()) >= 2:
                    topics.append(phrase)

        except Exception:
            pass

        # Deduplicate (case-insensitive)
        seen = set()
        unique = []
        for t in topics:
            key = t.lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(t)

        return unique[:15]

    # --- Routes ---

    @app.route("/")
    def index():
        """Homepage with search bar, trending topics, and latest articles."""
        trending = []
        if search_engine.is_available():
            trending = search_engine.get_trending(size=6)
            if not trending:
                trending = search_engine.get_trending(size=6, hours=720)
            if not trending:
                trending = search_engine.get_latest(size=6)

        # Build trending topics from OUR sources — not Google/mainstream
        from profoundd.utils.cache import cache_get, cache_set
        brave_trending = cache_get("homepage:trending_topics")
        if brave_trending is None:
            brave_trending = _build_trending_topics(search_engine)
            cache_set("homepage:trending_topics", brave_trending, ttl=1800)

        # Cache may return bytes from SQLite L2 — deserialize
        if isinstance(brave_trending, (bytes, memoryview)):
            try:
                import ast
                brave_trending = ast.literal_eval(bytes(brave_trending).decode("utf-8"))
            except Exception:
                brave_trending = []
        if not isinstance(brave_trending, list):
            brave_trending = []

        # Epstein doc count for homepage banner (cached 1h)
        epstein_count = cache_get("homepage:epstein_count")
        if isinstance(epstein_count, (bytes, memoryview)):
            try:
                epstein_count = int(bytes(epstein_count).decode("utf-8"))
            except Exception:
                epstein_count = None
        if not isinstance(epstein_count, int) or epstein_count <= 0:
            epstein_count = search_engine.count_epstein_docs() if search_engine.is_available() else 0
            if epstein_count > 0:
                cache_set("homepage:epstein_count", epstein_count, ttl=3600)

        return render_template("index.html", categories=CATEGORIES,
                               trending=trending, brave_trending=brave_trending,
                               epstein_count=epstein_count)

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
            fetch_grokipedia, fetch_searxng_images, fetch_searxng_shopping,
        )
        from profoundd.utils.cache import cache_get, cache_set, make_search_key, make_ai_key, AI_SUMMARY_TTL

        query = request.args.get("q", "").strip()
        category = request.args.get("category", "all")
        page = request.args.get("page", 1, type=int)
        sort_by = request.args.get("sort", "relevance")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        tab = request.args.get("tab", "all")
        source_filter = request.args.get("source", "")
        epstein_custodian = request.args.get("custodian", "")
        epstein_dataset = request.args.get("dataset", "")
        exclude_sponsored = request.args.get("nopfizer", "") == "1"

        if not query:
            return render_template("search.html", results=None, categories=CATEGORIES,
                                   query="", category=category, enhanced_providers=set(),
                                   web_fallback=False, tab_images=None)

        # --- Tab-specific handlers (images, shopping, videos, maps, markets) ---
        if tab in ("images", "shopping") and query:
            searxng_url = SiteSetting.get("searxng_url", "")
            tab_images = []
            if tab == "images":
                tab_images = fetch_searxng_images(query, searxng_url, max_results=24)
            elif tab == "shopping":
                tab_images = fetch_searxng_shopping(query + " buy", searxng_url, max_results=24)
            return render_template("search.html", results={"articles": [], "total": len(tab_images), "pages": 1, "page": 1},
                                   categories=CATEGORIES, query=query, category=category,
                                   sort_by=sort_by, enhanced_providers=set(),
                                   web_fallback=False, web_promoted=False,
                                   business_results=[], user_location=None,
                                   tab_images=tab_images)

        if tab == "videos" and query:
            # Filter to YouTube/Rumble sources only
            results = search_engine.search(
                query=query, category="all", page=page, per_page=20,
                sort_by=sort_by, source_filter=None,
            )
            video_sources = {"youtube", "rumble", "bitchute", "odysee", "rumble.com"}
            results["articles"] = [
                a for a in results.get("articles", [])
                if any(vs in a.get("source_name", "").lower() or vs in a.get("url", "").lower()
                       for vs in video_sources)
            ]
            return render_template("search.html", results=results, categories=CATEGORIES,
                                   query=query, category=category, sort_by=sort_by,
                                   enhanced_providers=set(), web_fallback=False,
                                   web_promoted=False, business_results=[],
                                   user_location=None, tab_images=None)

        if tab == "news" and query:
            # Only show RSS-crawled articles (not web-indexed)
            results = search_engine.search(
                query=query, category=category if category != "all" else None,
                page=page, per_page=20, sort_by=sort_by,
            )
            results["articles"] = [
                a for a in results.get("articles", [])
                if "web-indexed" not in (a.get("tags") or []) and not a.get("_enhanced")
            ]
            return render_template("search.html", results=results, categories=CATEGORIES,
                                   query=query, category=category, sort_by=sort_by,
                                   enhanced_providers=set(), web_fallback=False,
                                   web_promoted=False, business_results=[],
                                   user_location=None, tab_images=None)

        if tab == "markets" and query:
            from profoundd.search.external_providers import fetch_polymarket
            poly_results = fetch_polymarket(query, max_results=15)
            local_results = search_engine.search(query=query, category="markets", page=1, per_page=10, sort_by="date")
            all_articles = poly_results + local_results.get("articles", [])
            return render_template("search.html", results={"articles": all_articles, "total": len(all_articles), "pages": 1, "page": 1},
                                   categories=CATEGORIES, query=query, category="markets",
                                   sort_by=sort_by, enhanced_providers={"polymarket"} if poly_results else set(),
                                   web_fallback=False, web_promoted=False,
                                   business_results=[], user_location=None, tab_images=None)

        if tab == "maps" and query:
            from profoundd.search.local_intent import detect_local_intent, build_event_web_query
            from profoundd.search.external_providers import (
                fetch_brave_web, fetch_searxng, boost_known_domains,
            )
            user_loc = _get_user_location()
            intent = detect_local_intent(query)
            biz_location = {"lat": user_loc["lat"], "lon": user_loc["lon"]} if user_loc else None
            biz_query = intent["clean_query"] if intent.get("clean_query") else query
            try:
                business_results = search_engine.search_businesses(
                    query=biz_query, location=biz_location, radius_km=80, per_page=20)
            except Exception:
                business_results = []

            # Always run a location-augmented web search on the maps tab:
            # - Event/class queries return event/class pages (OSM has no events).
            # - Plain business queries return reviews, hours, articles about local places.
            # SearXNG (real engines) is preferred over Brave's suggest API (autocomplete),
            # which returned unrelated results for queries like "escrima class Santos Community Center".
            web_articles = []
            web_q = build_event_web_query(query, intent, user_loc)
            searxng_url = SiteSetting.get("searxng_url", "")
            try:
                if searxng_url:
                    web_articles = fetch_searxng(web_q, searxng_url, max_results=15)
                if not web_articles:
                    web_articles = fetch_brave_web(web_q, max_results=15)
            except Exception as e:
                logger.debug("Maps tab web fetch failed: %s", e)
                web_articles = []
            if web_articles:
                web_articles = boost_known_domains(web_articles)
                blocked_raw = SiteSetting.get("blocked_domains", "")
                if blocked_raw:
                    blocked = [d.strip().lower() for d in blocked_raw.split("\n") if d.strip()]
                    web_articles = [
                        a for a in web_articles
                        if not any(bd in a.get("url", "").lower() for bd in blocked)
                    ]

            total = len(web_articles) + len(business_results)
            return render_template("search.html",
                                   results={"articles": web_articles, "total": total,
                                            "pages": 1, "page": 1},
                                   categories=CATEGORIES, query=query, category=category,
                                   sort_by=sort_by, enhanced_providers=set(),
                                   web_fallback=bool(web_articles),
                                   web_promoted=bool(web_articles),
                                   business_results=business_results, user_location=user_loc,
                                   tab_images=None, spelling_suggestion=None)

        if tab == "wef" and query:
            wef_year = request.args.get("year", "").strip() or None
            wef_doc = request.args.get("doc_id", "").strip() or None
            wef_results = search_engine.search_wef_docs(
                query, page=page, per_page=20,
                doc_id=wef_doc, year=wef_year, sort_by=sort_by,
            )
            all_articles = wef_results.get("articles", [])
            for a in all_articles:
                a["result_type"] = "wef-doc"
            total = wef_results.get("total", 0)
            return render_template("search.html",
                                   results={"articles": all_articles, "total": total,
                                            "pages": (total + 19) // 20, "page": page,
                                            "expansion": wef_results.get("expansion")},
                                   categories=CATEGORIES, query=query, category="wef",
                                   sort_by=sort_by, enhanced_providers=set(),
                                   web_fallback=False, web_promoted=False,
                                   business_results=[], user_location=None,
                                   tab_images=None, spelling_suggestion=None)

        if tab == "climate" and query:
            climate_city = request.args.get("city", "").strip() or None
            climate_doc = request.args.get("doc_id", "").strip() or None
            climate_results = search_engine.search_climate_docs(
                query, page=page, per_page=20,
                city=climate_city, doc_id=climate_doc, sort_by=sort_by,
            )
            all_articles = climate_results.get("articles", [])
            for a in all_articles:
                a["result_type"] = "climate-doc"
            total = climate_results.get("total", 0)
            return render_template("search.html",
                                   results={"articles": all_articles, "total": total,
                                            "pages": (total + 19) // 20, "page": page,
                                            "expansion": climate_results.get("expansion")},
                                   categories=CATEGORIES, query=query, category="climate",
                                   sort_by=sort_by, enhanced_providers=set(),
                                   web_fallback=False, web_promoted=False,
                                   business_results=[], user_location=None,
                                   tab_images=None, spelling_suggestion=None)

        if tab == "epstein" and query:
            # Search ONLY the Epstein court documents index
            doc_results = search_engine.search_epstein_docs(
                query, page=page, per_page=20,
                date_from=date_from, date_to=date_to,
                custodian=epstein_custodian or None,
                dataset=epstein_dataset or None,
                sort_by=sort_by,
            )
            all_articles = doc_results.get("articles", [])
            total = doc_results.get("total", 0)
            epstein_facets = search_engine.get_epstein_facets() if search_engine.is_available() else {}
            return render_template("search.html",
                                   results={
                                       "articles": all_articles,
                                       "total": total,
                                       "pages": (total + 19) // 20,
                                       "page": page,
                                       "alias_expansions": doc_results.get("alias_expansions", []),
                                   },
                                   categories=CATEGORIES, query=query, category="epstein-files",
                                   sort_by=sort_by, enhanced_providers=set(),
                                   web_fallback=False, web_promoted=False,
                                   business_results=[], user_location=None,
                                   tab_images=None, spelling_suggestion=None,
                                   epstein_facets=epstein_facets)

        # Check cache first (non-admin only — admins always get fresh results)
        is_admin = session.get("admin_logged_in", False)
        cache_key = make_search_key(query, category, page, sort_by, date_from, date_to, source_filter)
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
            source_filter=source_filter or None,
            exclude_sponsored=exclude_sponsored,
        )

        # Blend in Epstein court document results on "all" and "epstein-files" category searches
        if tab == "all" and category in ("all", "epstein-files") and query:
            try:
                epstein_results = search_engine.search_epstein_docs(query, page=1, per_page=5)
                epstein_docs = epstein_results.get("articles", [])
                if epstein_docs:
                    # Mark as document result type for proper card rendering
                    for doc in epstein_docs:
                        doc["result_type"] = "document"
                    # Insert after first 3 regular results
                    articles = results.get("articles", [])
                    insert_pos = min(3, len(articles))
                    for i, doc in enumerate(epstein_docs):
                        articles.insert(insert_pos + i, doc)
                    results["articles"] = articles
                    # Update total to reflect blended count
                    results["total"] = results.get("total", 0) + epstein_results.get("total", 0)
            except Exception as e:
                logger.debug("Epstein blend failed: %s", e)

        # Blend in WEF docs on "all" tab
        if tab == "all" and query:
            try:
                wef_blend = search_engine.search_wef_docs(query, page=1, per_page=3)
                wef_hits = wef_blend.get("articles", [])
                if wef_hits:
                    for doc in wef_hits:
                        doc["result_type"] = "wef-doc"
                    articles = results.get("articles", [])
                    insert_pos = min(8, len(articles))
                    for i, doc in enumerate(wef_hits):
                        articles.insert(insert_pos + i, doc)
                    results["articles"] = articles
                    results["total"] = results.get("total", 0) + wef_blend.get("total", 0)
            except Exception as e:
                logger.debug("WEF blend failed: %s", e)

        # Blend in climate doc results on "all" tab
        if tab == "all" and query:
            try:
                climate_blend = search_engine.search_climate_docs(query, page=1, per_page=3)
                climate_hits = climate_blend.get("articles", [])
                if climate_hits:
                    for doc in climate_hits:
                        doc["result_type"] = "climate-doc"
                    articles = results.get("articles", [])
                    insert_pos = min(5, len(articles))
                    for i, doc in enumerate(climate_hits):
                        articles.insert(insert_pos + i, doc)
                    results["articles"] = articles
                    results["total"] = results.get("total", 0) + climate_blend.get("total", 0)
            except Exception as e:
                logger.debug("Climate blend failed: %s", e)

        # Fetch enhanced results from external providers (page 1 only)
        enhanced_providers = set()
        web_fallback = False
        web_promoted = False
        if page == 1:
            # --- Fire all external fetches in parallel ---
            # Pre-read DB settings in main thread (Flask app context)
            searxng_url = SiteSetting.get("searxng_url", "")
            from profoundd.search.local_intent import detect_local_intent, build_event_web_query
            local_intent = detect_local_intent(query)
            user_loc_main = _get_user_location()

            with ThreadPoolExecutor(max_workers=4) as pool:
                fut_enhanced = pool.submit(fetch_all_enhanced, query, category)
                fut_grok = pool.submit(fetch_grokipedia, query, max_results=3)

                # Web results — use SearXNG (real search) for event/local queries since
                # Brave's suggest API only returns autocomplete, not page results.
                # For everything else, Brave is fine and faster.
                is_event_query = local_intent.get("is_event", False)
                web_query = (
                    build_event_web_query(query, local_intent, user_loc_main)
                    if is_event_query else query
                )

                def _fetch_web():
                    if is_event_query and searxng_url:
                        web = fetch_searxng(web_query, searxng_url, max_results=10)
                        if web:
                            return web
                        return fetch_brave_web(web_query, max_results=10)
                    web = fetch_brave_web(web_query, max_results=10)
                    if not web and searxng_url:
                        web = fetch_searxng(web_query, searxng_url, max_results=10)
                    return web
                fut_web = pool.submit(_fetch_web)

                # Local business search (fast ES query, fine in thread)
                business_results = []
                def _fetch_biz():
                    if not local_intent["is_local"]:
                        return []
                    biz_location = {"lat": user_loc_main["lat"], "lon": user_loc_main["lon"]} if user_loc_main else None
                    return search_engine.search_businesses(
                        query=local_intent["clean_query"],
                        location=biz_location,
                        radius_km=80, per_page=5,
                    )
                fut_biz = pool.submit(_fetch_biz)

                # Collect results as they complete
                try:
                    enhanced_articles, enhanced_providers = fut_enhanced.result(timeout=5)
                except Exception as e:
                    logger.warning("Enhanced providers failed: %s", e)
                    enhanced_articles, enhanced_providers = [], set()

                try:
                    grok_results = fut_grok.result(timeout=5)
                except Exception as e:
                    logger.warning("Grokipedia failed: %s", e)
                    grok_results = []

                try:
                    web_results = fut_web.result(timeout=5)
                except Exception as e:
                    logger.warning("Web results failed: %s", e)
                    web_results = []

                try:
                    business_results = fut_biz.result(timeout=5)
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

            # Fire-and-forget: index web results to ES so they become organic results
            # Apply quality filter — skip social media, forums, shopping, short content
            if web_results and search_engine.is_available():
                _now = datetime.now(timezone.utc).isoformat()
                _to_index = []
                _skipped = 0
                for wr in web_results:
                    if not _should_index_brave_result(wr):
                        _skipped += 1
                        continue
                    doc = {k: v for k, v in wr.items() if not k.startswith("_")}
                    doc["tags"] = ["web-indexed"]
                    doc["crawled_at"] = _now
                    doc.setdefault("result_type", "article")
                    _to_index.append(doc)

                def _bg_index(articles, skipped):
                    try:
                        count = search_engine.bulk_index(articles)
                        if count or skipped:
                            logger.info("Auto-indexed %d web results (skipped %d low-quality)", count, skipped)
                    except Exception as e:
                        logger.debug("Auto-index web results failed: %s", e)

                threading.Thread(target=_bg_index, args=(_to_index, _skipped), daemon=True).start()

        # Log the search (background — don't block response for DB write)
        _search_q = query
        _search_cat = category
        _search_count = results.get("total", 0)
        _search_ip = _hash_ip(request.remote_addr)

        def _bg_log_search():
            with app.app_context():
                try:
                    log = SearchLog(query=_search_q, category=_search_cat,
                                    results_count=_search_count, ip_address=_search_ip)
                    db.session.add(log)
                    db.session.commit()
                except Exception as e:
                    logger.debug("SearchLog write failed: %s", e)
                    try:
                        db.session.rollback()
                    except Exception:
                        pass

        threading.Thread(target=_bg_log_search, daemon=True).start()

        # Spelling correction now loads async via /api/spelling (Fix 5: don't block render)
        spelling_suggestion = None

        # Get source list for filter dropdown (cached in search_engine)
        source_list = search_engine.get_source_names() if search_engine.is_available() else []

        rendered_html = render_template("search.html", results=results, categories=CATEGORIES,
                               query=query, category=category, sort_by=sort_by,
                               enhanced_providers=enhanced_providers,
                               web_fallback=web_fallback,
                               web_promoted=web_promoted,
                               business_results=business_results if page == 1 else [],
                               user_location=_get_user_location(),
                               tab_images=None,
                               spelling_suggestion=spelling_suggestion,
                               source_list=source_list)
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

        # Web results second (broad coverage) — Brave primary, SearXNG fallback
        try:
            web_articles = fetch_brave_web(query, max_results=8)
            if not web_articles:
                searxng_url = app.config.get("SEARXNG_URL", "")
                if searxng_url:
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

    @app.route("/api/admin/save-article", methods=["POST"])
    def api_admin_save_article():
        """Admin: fetch full text from a web result URL and index to ES."""
        if not session.get("admin_logged_in"):
            return jsonify({"error": "Unauthorized"}), 403

        data = request.get_json() or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "URL required"}), 400

        # Fetch full text using the crawler's extraction logic
        from profoundd.crawler.feed_crawler import FeedCrawler
        crawler = FeedCrawler(search_engine=search_engine)
        full_text = crawler.fetch_full_text(url) or ""

        article = {
            "title": data.get("title", ""),
            "summary": data.get("summary", "") or (full_text[:500] + "..." if len(full_text) > 500 else full_text),
            "content": full_text,
            "source_name": data.get("source_name", ""),
            "source_credibility": 7,
            "category": "news",
            "url": url,
            "published_at": data.get("published_at", ""),
            "tags": ["web-indexed", "admin-saved"],
            "crawled_at": datetime.now(timezone.utc).isoformat(),
            "result_type": "article",
        }

        doc_id = search_engine.index_article(article)
        if doc_id:
            return jsonify({"ok": True, "doc_id": doc_id, "content_length": len(full_text)})
        return jsonify({"error": "Index failed"}), 500

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

            # For Epstein Files: merge court documents from the dedicated index
            if category_name == "epstein-files" and subcategory in ("all", "court-docs"):
                try:
                    doc_results = search_engine.search_epstein_docs(query="", page=page, per_page=per_page)
                    doc_articles = doc_results.get("articles", [])
                    if subcategory == "court-docs":
                        # Show only documents
                        articles = doc_articles
                        total = doc_results.get("total", 0)
                        pages = (total + per_page - 1) // per_page
                    else:
                        # Interleave: news first, then documents
                        articles.extend(doc_articles[:5])
                except Exception as e:
                    logger.warning("Epstein doc search failed: %s", e)

        cat_info = CATEGORIES[category_name]
        return render_template("category.html", category_name=category_name,
                               category=cat_info, articles=articles,
                               page=page, pages=pages, total=total,
                               subcategory=subcategory,
                               categories=CATEGORIES)

    @app.route("/epstein-docs/<bates_id>")
    def epstein_doc_viewer(bates_id):
        """View an Epstein court document by Bates number."""
        doc = search_engine.get_epstein_doc(bates_id)
        if not doc:
            return render_template("404.html"), 404
        from profoundd.search.related import find_related
        related = find_related(
            es=search_engine.es if search_engine.is_available() else None,
            source_type="epstein",
            source_id=bates_id,
            title=doc.get("title", ""),
            content=doc.get("content", ""),
        )
        return render_template("epstein_doc.html", doc=doc, related=related)

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

    @app.route("/api/epstein-explain", methods=["POST"])
    def api_epstein_explain():
        """AI-powered explanation of a person's connection to an Epstein document."""
        if not session.get("user_logged_in") and not session.get("admin_logged_in"):
            return jsonify(error="Log in to use AI analysis"), 401

        data = request.get_json(silent=True) or {}
        bates = data.get("bates_number", "").strip()
        query = data.get("query", "").strip()
        title = data.get("title", "").strip()
        if not bates or not query:
            return jsonify(error="Missing bates_number or query"), 400

        doc = search_engine.get_epstein_doc(bates)
        if not doc:
            return jsonify(error=f"Document {bates} not found"), 404

        from profoundd.search.ai_explain import explain, PROMPTS
        body, status = explain(
            doc_type="epstein",
            doc_id=bates,
            page_number=0,
            content=doc.get("content", ""),
            query=query,
            meta={
                "title": title or doc.get("title", ""),
                "bates": bates,
                "custodian": doc.get("custodian", "Unknown"),
            },
            prompt_template=PROMPTS["epstein"],
        )
        return jsonify(**body), status

    @app.route("/epstein-docs/<bates_id>/download")
    def epstein_doc_download(bates_id):
        """Stream the original Epstein PDF from the Azure7 file server."""
        ip_hash = _hash_ip(_get_client_ip())
        if not _check_download_rate_limit(ip_hash):
            return jsonify(error="Rate limit exceeded, try again later"), 429

        doc = search_engine.get_epstein_doc(bates_id)
        if not doc:
            return render_template("404.html"), 404

        file_path = doc.get("file_path", "")
        base = "/mnt/backup/epstein-files/extracted/"
        if not file_path.startswith(base):
            logger.warning("Epstein doc %s has unexpected file_path: %s", bates_id, file_path)
            return jsonify(error="File path invalid"), 500
        relpath = file_path[len(base):]

        server_url = os.environ.get("EPSTEIN_FILE_SERVER_URL", "").rstrip("/")
        token = os.environ.get("EPSTEIN_FILE_SERVER_TOKEN", "")
        if not server_url or not token:
            return jsonify(error="Download server not configured"), 503

        try:
            upstream = http_requests.get(
                f"{server_url}/files/{quote(relpath)}",
                headers={"Authorization": f"Bearer {token}"},
                stream=True,
                timeout=(5, 60),
            )
        except http_requests.RequestException as e:
            logger.warning("Epstein download upstream connect failed for %s: %s", bates_id, e)
            return jsonify(error="Upstream file server unreachable"), 502

        if upstream.status_code != 200:
            logger.warning("Epstein download upstream %s for %s", upstream.status_code, bates_id)
            return jsonify(error=f"Upstream returned {upstream.status_code}"), 502

        headers = {
            "Content-Disposition": f'attachment; filename="{bates_id}.pdf"',
        }
        content_length = upstream.headers.get("Content-Length")
        if content_length:
            headers["Content-Length"] = content_length

        return Response(
            stream_with_context(upstream.iter_content(chunk_size=65536)),
            mimetype="application/pdf",
            headers=headers,
        )

    # --- Archive collections (FBI Vault, SPLC Wayback, future CIA/JFK) ---

    ARCHIVE_LABELS = {
        "fbi-vault": "FBI Vault",
        "splc": "Southern Poverty Law Center (Wayback)",
        "phmpt": "Pfizer Documents (PHMPT/FDA)",
        "acip": "CDC ACIP — Vaccine Advisory Committee",
        "nih-grants": "NIH Pandemic-Era Grants",
        "cia-crest": "CIA CREST",
        "jfk-records": "JFK Records",
    }

    @app.route("/archive-docs")
    def archive_docs_index():
        """Browse / search across all archive collections."""
        return _archive_browse(collection=None)

    @app.route("/archive-docs/<collection>")
    def archive_docs_by_collection(collection):
        """Browse / search one archive collection."""
        if collection not in ARCHIVE_LABELS:
            return render_template("404.html"), 404
        return _archive_browse(collection=collection)

    def _archive_browse(collection):
        query = request.args.get("q", "").strip()
        page = request.args.get("page", 1, type=int)
        case = request.args.get("case", "").strip() or None
        date_from = request.args.get("date_from", "").strip() or None
        date_to = request.args.get("date_to", "").strip() or None
        sort_by = request.args.get("sort", "relevance")

        if not search_engine.is_available():
            return render_template(
                "archive_docs_index.html",
                results={"articles": [], "total": 0, "page": 1},
                facets={"collections": [], "cases": [], "sub_cases": []},
                query=query, case=case, date_from=date_from, date_to=date_to,
                sort_by=sort_by, collection=collection,
                collection_label=ARCHIVE_LABELS.get(collection) if collection else "All Archives",
                archive_labels=ARCHIVE_LABELS,
                categories=CATEGORIES,
            )

        results = search_engine.search_archive_docs(
            query=query, collection=collection, page=page, per_page=20,
            case=case, date_from=date_from, date_to=date_to, sort_by=sort_by,
        )
        facets = search_engine.get_archive_facets(collection=collection)
        return render_template(
            "archive_docs_index.html",
            results=results, facets=facets,
            query=query, case=case, date_from=date_from, date_to=date_to,
            sort_by=sort_by, collection=collection,
            collection_label=ARCHIVE_LABELS.get(collection) if collection else "All Archives",
            archive_labels=ARCHIVE_LABELS,
            categories=CATEGORIES,
        )

    @app.route("/archive-docs/<collection>/<doc_id>")
    def archive_doc_viewer(collection, doc_id):
        """View a single archive doc."""
        if collection not in ARCHIVE_LABELS:
            return render_template("404.html"), 404
        doc = search_engine.get_archive_doc(doc_id)
        if not doc or doc.get("collection") != collection:
            return render_template("404.html"), 404
        from profoundd.search.related import find_related
        try:
            related = find_related(
                es=search_engine.es if search_engine.is_available() else None,
                source_type="archive",
                source_id=doc_id,
                title=doc.get("title", ""),
                content=doc.get("content", ""),
            )
        except Exception:
            related = {}
        return render_template(
            "archive_doc.html", doc=doc, related=related,
            collection_label=ARCHIVE_LABELS.get(collection, collection),
            categories=CATEGORIES,
        )

    @app.route("/api/archive-explain", methods=["POST"])
    def api_archive_explain():
        """AI explain a query against an archive doc."""
        if not session.get("user_logged_in") and not session.get("admin_logged_in"):
            return jsonify(error="Log in to use AI analysis"), 401
        data = request.get_json(silent=True) or {}
        doc_id = data.get("doc_id", "").strip()
        query = data.get("query", "").strip()
        if not doc_id or not query:
            return jsonify(error="Missing doc_id or query"), 400
        doc = search_engine.get_archive_doc(doc_id)
        if not doc:
            return jsonify(error=f"Doc {doc_id} not found"), 404

        from profoundd.search.ai_explain import explain, PROMPTS
        body, status = explain(
            doc_type="archive",
            doc_id=doc_id,
            page_number=0,
            content=doc.get("content", ""),
            query=query,
            meta={
                "title": doc.get("title", ""),
                "collection": doc.get("collection", ""),
                "case": doc.get("case", "") or "",
                "sub_case": doc.get("sub_case", "") or "",
                "snapshot_date": doc.get("snapshot_date", "") or "",
            },
            prompt_template=PROMPTS.get("archive", PROMPTS.get("epstein", "")),
        )
        return jsonify(**body), status

    # --- Federal Writers' Project (FWP) ---

    @app.route("/fwp-docs")
    def fwp_docs_index():
        """Browse Federal Writers' Project items with filters + search."""
        query = request.args.get("q", "").strip()
        page = request.args.get("page", 1, type=int)
        contributor = request.args.get("contributor", "").strip() or None
        location = request.args.get("location", "").strip() or None
        date_from = request.args.get("date_from", "").strip() or None
        date_to = request.args.get("date_to", "").strip() or None
        sort_by = request.args.get("sort", "relevance")

        if not search_engine.is_available():
            return render_template(
                "fwp_docs_index.html",
                results={"articles": [], "total": 0, "page": 1},
                facets={"contributors": [], "locations": [], "subcollections": []},
                query=query, contributor=contributor, location=location,
                date_from=date_from, date_to=date_to, sort_by=sort_by,
                categories=CATEGORIES,
            )

        results = search_engine.search_fwp_docs(
            query=query, page=page, per_page=20,
            date_from=date_from, date_to=date_to,
            contributor=contributor, location=location,
            sort_by=sort_by,
        )
        facets = search_engine.get_fwp_facets()
        return render_template(
            "fwp_docs_index.html",
            results=results, facets=facets,
            query=query, contributor=contributor, location=location,
            date_from=date_from, date_to=date_to, sort_by=sort_by,
            categories=CATEGORIES,
        )

    @app.route("/fwp-docs/<item_id>")
    def fwp_doc_viewer(item_id):
        """View a single FWP item — metadata, OCR text, embedded LOC PDF."""
        doc = search_engine.get_fwp_doc(item_id)
        if not doc:
            return render_template("404.html"), 404
        from profoundd.search.related import find_related
        try:
            related = find_related(
                es=search_engine.es if search_engine.is_available() else None,
                source_type="fwp",
                source_id=item_id,
                title=doc.get("title", ""),
                content=doc.get("content", ""),
            )
        except Exception:
            related = []
        return render_template("fwp_doc.html", doc=doc, related=related,
                               categories=CATEGORIES)

    @app.route("/api/fwp-explain", methods=["POST"])
    def api_fwp_explain():
        """AI explain a topic in an FWP item."""
        if not session.get("user_logged_in") and not session.get("admin_logged_in"):
            return jsonify(error="Log in to use AI analysis"), 401

        data = request.get_json(silent=True) or {}
        item_id = data.get("item_id", "").strip()
        query = data.get("query", "").strip()
        if not item_id or not query:
            return jsonify(error="Missing item_id or query"), 400

        doc = search_engine.get_fwp_doc(item_id)
        if not doc:
            return jsonify(error=f"Item {item_id} not found"), 404

        from profoundd.search.ai_explain import explain, PROMPTS
        body, status = explain(
            doc_type="fwp",
            doc_id=item_id,
            page_number=0,
            content=doc.get("content", ""),
            query=query,
            meta={
                "title": doc.get("title", ""),
                "contributors": ", ".join(doc.get("contributors", []) or []),
                "location": ", ".join(doc.get("location", []) or []),
                "doc_date": doc.get("doc_date", ""),
            },
            prompt_template=PROMPTS.get("fwp", PROMPTS.get("epstein", "")),
        )
        return jsonify(**body), status

    @app.route("/climate-docs")
    def climate_docs_index():
        """Browse Oregon climate / planning docs grouped by city."""
        docs = search_engine.list_climate_docs() if search_engine.is_available() else []
        by_city = {}
        for d in docs:
            by_city.setdefault(d.get("city", "Other"), []).append(d)
        return render_template("climate_docs_index.html", by_city=by_city,
                               total_pages=sum(d.get("page_count", 0) for d in docs),
                               categories=CATEGORIES)

    @app.route("/climate-docs/<doc_id>/<int:page_number>")
    def climate_doc_viewer(doc_id, page_number):
        """View a single page of a climate document."""
        doc = search_engine.get_climate_page(doc_id, page_number)
        if not doc:
            return render_template("404.html"), 404
        # neighbor pages for prev/next
        total_pages_result = search_engine.search_climate_docs(
            query="", doc_id=doc_id, page=1, per_page=1,
        )
        total_pages = total_pages_result.get("total", 0)
        # Cross-collection related docs (cached 24h per page)
        from profoundd.search.related import find_related
        related = find_related(
            es=search_engine.es if search_engine.is_available() else None,
            source_type="climate",
            source_id=doc_id,
            title=doc.get("document_name", ""),
            content=doc.get("content", ""),
        )
        return render_template(
            "climate_doc_viewer.html",
            doc=doc,
            total_pages=total_pages,
            related=related,
            categories=CATEGORIES,
        )

    @app.route("/climate-docs/<doc_id>/download")
    def climate_doc_download(doc_id):
        """Download the full original PDF for a climate document."""
        ip_hash = _hash_ip(_get_client_ip())
        if not _check_download_rate_limit(ip_hash):
            return jsonify(error="Rate limit exceeded, try again later"), 429

        # Find one page to get file_path + document_name
        page = search_engine.get_climate_page(doc_id, 1)
        if not page:
            return render_template("404.html"), 404
        file_path = page.get("file_path", "")
        base = "/app/data/climate-docs/"
        if not file_path.startswith(base) or ".." in file_path:
            return jsonify(error="File path invalid"), 500
        if not os.path.isfile(file_path):
            logger.warning("climate pdf missing: %s", file_path)
            return jsonify(error="File not found on disk"), 404
        return Response(
            _stream_file(file_path),
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{doc_id}.pdf"'},
        )

    @app.route("/api/climate-explain", methods=["POST"])
    def api_climate_explain():
        """AI-powered explanation of a topic's treatment in a climate document page."""
        data = request.get_json(silent=True) or {}
        doc_id = data.get("doc_id", "").strip()
        page_number = data.get("page_number")
        query = data.get("query", "").strip()
        if not doc_id or page_number is None or not query:
            return jsonify(error="Missing doc_id, page_number, or query"), 400

        page = search_engine.get_climate_page(doc_id, page_number)
        if not page:
            return jsonify(error="Page not found"), 404

        from profoundd.search.ai_explain import explain, PROMPTS
        body, status = explain(
            doc_type="climate",
            doc_id=doc_id,
            page_number=page_number,
            content=page.get("content", ""),
            query=query,
            meta={
                "document_name": page.get("document_name", ""),
                "city": page.get("city", ""),
                "page_number": page_number,
                "adopted_date": page.get("adopted_date", "unknown"),
            },
            prompt_template=PROMPTS["climate"],
        )
        return jsonify(**body), status

    @app.route("/wef-docs")
    def wef_docs_index():
        """Browse WEF publications, optionally filtered by year or tag."""
        docs = search_engine.list_wef_docs() if search_engine.is_available() else []
        filter_tag = request.args.get("tag", "").strip() or None
        if filter_tag:
            docs = [d for d in docs if filter_tag in (d.get("tags") or [])]
        by_year = {}
        for d in docs:
            y = d.get("year") or "Undated"
            by_year.setdefault(y, []).append(d)
        # Year sort desc, "Undated" last
        sorted_years = sorted([k for k in by_year if k != "Undated"], reverse=True)
        if "Undated" in by_year:
            sorted_years.append("Undated")
        return render_template("wef_docs_index.html",
                               by_year=by_year, sorted_years=sorted_years,
                               total_pages=sum(d.get("page_count", 0) for d in docs),
                               total_docs=len(docs),
                               filter_tag=filter_tag,
                               categories=CATEGORIES)

    @app.route("/wef-docs/<doc_id>/<int:page_number>")
    def wef_doc_viewer(doc_id, page_number):
        doc = search_engine.get_wef_page(doc_id, page_number)
        if not doc:
            # Fall back to the first indexed page for this doc
            first = search_engine.search_wef_docs(query="", doc_id=doc_id, page=1, per_page=1, sort_by="relevance")
            hits = first.get("articles", [])
            if hits and hits[0].get("page_number") != page_number:
                return redirect(f"/wef-docs/{doc_id}/{hits[0]['page_number']}")
            return render_template("404.html"), 404
        total = search_engine.search_wef_docs(query="", doc_id=doc_id, page=1, per_page=1).get("total", 0)
        from profoundd.search.related import find_related
        related = find_related(
            es=search_engine.es if search_engine.is_available() else None,
            source_type="wef",
            source_id=doc_id,
            title=doc.get("document_name", ""),
            content=doc.get("content", ""),
        )
        return render_template("wef_doc_viewer.html", doc=doc, total_pages=total, related=related, categories=CATEGORIES)

    @app.route("/wef-docs/<doc_id>/download")
    def wef_doc_download(doc_id):
        ip_hash = _hash_ip(_get_client_ip())
        if not _check_download_rate_limit(ip_hash):
            return jsonify(error="Rate limit exceeded, try again later"), 429
        # Grab any page's metadata for this doc (file_path is the same)
        page = search_engine.get_wef_page(doc_id, 1)
        if not page:
            hits = search_engine.search_wef_docs(query="", doc_id=doc_id, page=1, per_page=1).get("articles", [])
            if hits:
                page = hits[0]
        if not page:
            return render_template("404.html"), 404
        file_path = page.get("file_path", "")
        base = "/app/data/wef-docs/"
        if not file_path.startswith(base) or ".." in file_path:
            return jsonify(error="File path invalid"), 500
        if not os.path.isfile(file_path):
            return jsonify(error="File not found on disk"), 404
        return Response(
            _stream_file(file_path),
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{page.get("filename", doc_id+".pdf")}"'},
        )

    @app.route("/api/wef-explain", methods=["POST"])
    def api_wef_explain():
        data = request.get_json(silent=True) or {}
        doc_id = data.get("doc_id", "").strip()
        page_number = data.get("page_number")
        query = data.get("query", "").strip()
        if not doc_id or page_number is None or not query:
            return jsonify(error="Missing doc_id, page_number, or query"), 400
        page = search_engine.get_wef_page(doc_id, page_number)
        if not page:
            return jsonify(error="Page not found"), 404

        from profoundd.search.ai_explain import explain, PROMPTS
        body, status = explain(
            doc_type="wef",
            doc_id=doc_id,
            page_number=page_number,
            content=page.get("content", ""),
            query=query,
            meta={
                "document_name": page.get("document_name", ""),
                "year": page.get("year", "unknown"),
                "page_number": page_number,
            },
            prompt_template=PROMPTS["wef"],
        )
        return jsonify(**body), status

    @app.route("/api/spelling")
    def api_spelling():
        """Async spelling suggestion — loaded via AJAX after page renders."""
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify(suggestion=None)
        try:
            suggestion = search_engine.get_spelling_suggestion(query)
            return jsonify(suggestion=suggestion)
        except Exception:
            return jsonify(suggestion=None)

    @app.route("/api/suggest")
    def api_suggest():
        """Search suggestions from popular queries + indexed titles."""
        query = request.args.get("q", "").strip()
        if not query or len(query) < 2:
            return jsonify({"suggestions": []})

        suggestions = []

        # Source 1: Popular past queries matching prefix (what people actually search)
        try:
            from sqlalchemy import func
            q_lower = query.lower()
            popular = (
                db.session.query(SearchLog.query, func.count(SearchLog.id).label("cnt"))
                .filter(SearchLog.query.ilike(f"{q_lower}%"))
                .group_by(SearchLog.query)
                .order_by(func.count(SearchLog.id).desc())
                .limit(3)
                .all()
            )
            for q, cnt in popular:
                if q.strip().lower() != q_lower:
                    suggestions.append({"title": q.strip(), "category": "", "source": f"searched {cnt}x"})
        except Exception:
            pass

        # Source 2: Article title matches (existing)
        title_suggestions = search_engine.get_suggestions(query, size=5)
        suggestions.extend(title_suggestions)

        # Deduplicate by title
        seen = set()
        unique = []
        for s in suggestions:
            key = s["title"].lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(s)

        return jsonify({"suggestions": unique[:7]})

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
                ip_address=_hash_ip(request.remote_addr),
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

    @app.route("/privacy")
    def privacy_policy():
        return render_template("privacy.html", categories=CATEGORIES)

    @app.route("/bot")
    def bot_info():
        return render_template("bot.html", categories=CATEGORIES)

    @app.route("/add-to-browser")
    def add_to_browser():
        return render_template("add_to_browser.html", categories=CATEGORIES)

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
Allow: /epstein-docs/
Allow: /climate-docs/
Allow: /wef-docs/
Allow: /about
Allow: /submit
Allow: /newsroom
Disallow: /admin/
Disallow: /api/
Disallow: /health

Sitemap: https://{domain}/sitemap-index.xml
Sitemap: https://{domain}/sitemap.xml
Sitemap: https://{domain}/news-sitemap.xml

# =========================================================================
# Answer engines and AI assistants — explicitly welcome.
# Profoundd exists to be cited in AI answers about Epstein files, WEF docs,
# Oregon climate policy, etc. These crawlers drive referrals when users ask
# chatbots questions we can answer.
# =========================================================================

# OpenAI (ChatGPT / SearchGPT)
User-agent: GPTBot
Allow: /
Crawl-delay: 2

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

# Anthropic (Claude)
User-agent: ClaudeBot
Allow: /
Crawl-delay: 2

User-agent: claude-web
Allow: /

User-agent: anthropic-ai
Allow: /

# Perplexity
User-agent: PerplexityBot
Allow: /

User-agent: Perplexity-User
Allow: /

# Google (AI Overviews, Gemini, Search)
User-agent: Google-Extended
Allow: /
Crawl-delay: 2

User-agent: GoogleOther
Allow: /

# Apple (Siri, Apple Intelligence)
User-agent: Applebot
Allow: /

User-agent: Applebot-Extended
Allow: /
Crawl-delay: 2

# DuckDuckGo (DuckAssist)
User-agent: DuckAssistBot
Allow: /

# Microsoft (Bing, Copilot)
User-agent: Bingbot
Allow: /

# Meta's AI crawler (NOT the link-preview webindexer below)
User-agent: Meta-ExternalAgent
Allow: /
Crawl-delay: 2

# You.com
User-agent: YouBot
Allow: /

# Amazon (Alexa, product search)
User-agent: Amazonbot
Allow: /

# Throttle Facebook/Meta link-preview crawler (was 250K hits/day, aggressive)
# Still allowed — just delayed. Users sharing profoundd.com links on FB still get previews.
User-agent: meta-webindexer
Crawl-delay: 10
Disallow: /search
Disallow: /api/

# =========================================================================
# Block SEO analysis bots — zero value, just scrape for their paid tools.
# =========================================================================
User-agent: SemrushBot
Disallow: /

User-agent: SemrushBot-BA
Disallow: /

User-agent: AhrefsBot
Disallow: /

User-agent: MJ12bot
Disallow: /

User-agent: DotBot
Disallow: /

User-agent: SERankingBacklinksBot
Disallow: /

User-agent: BLEXBot
Disallow: /

User-agent: PetalBot
Disallow: /

User-agent: Bytespider
Disallow: /
"""
        return Response(content, mimetype="text/plain")

    @app.route("/sitemap-index.xml")
    def sitemap_index():
        """Master sitemap index listing all sitemaps (main + news + epstein chunks)."""
        domain = app.config.get("DOMAIN", "profoundd.com")
        base = f"https://{domain}"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Count Epstein docs to determine how many sitemap chunks we need
        epstein_count = 0
        if search_engine.is_available():
            try:
                epstein_count = search_engine.count_epstein_docs()
            except Exception:
                pass
        chunk_size = 50000
        num_chunks = (epstein_count + chunk_size - 1) // chunk_size

        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        parts.append(f"  <sitemap><loc>{base}/sitemap.xml</loc><lastmod>{today}</lastmod></sitemap>")
        parts.append(f"  <sitemap><loc>{base}/news-sitemap.xml</loc><lastmod>{today}</lastmod></sitemap>")
        for i in range(num_chunks):
            parts.append(f"  <sitemap><loc>{base}/sitemap-epstein-{i}.xml</loc><lastmod>{today}</lastmod></sitemap>")
        parts.append("</sitemapindex>")
        return Response("\n".join(parts), mimetype="application/xml")

    @app.route("/sitemap-epstein-<int:chunk>.xml")
    def sitemap_epstein_chunk(chunk):
        """Generate one 50K-URL chunk of the Epstein doc sitemap."""
        from profoundd.utils.cache import cache_get, cache_set
        cache_key = f"sitemap:epstein:{chunk}"

        cached = cache_get(cache_key)
        if cached:
            if isinstance(cached, (bytes, memoryview)):
                cached = bytes(cached).decode("utf-8")
            return Response(cached, mimetype="application/xml")

        if not search_engine.is_available():
            return Response("<urlset/>", mimetype="application/xml", status=503)

        domain = app.config.get("DOMAIN", "profoundd.com")
        base = f"https://{domain}"

        try:
            items = search_engine.scroll_epstein_bates(chunk_size=50000, chunk_index=chunk)
        except Exception as e:
            logger.error("Epstein sitemap chunk %d failed: %s", chunk, e)
            items = []

        if not items:
            return Response("<urlset/>", mimetype="application/xml", status=404)

        parts = ['<?xml version="1.0" encoding="UTF-8"?>']
        parts.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for bates, indexed_at in items:
            lastmod = (indexed_at or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            parts.append("  <url>")
            parts.append(f"    <loc>{base}/epstein-docs/{bates}</loc>")
            parts.append(f"    <lastmod>{lastmod}</lastmod>")
            parts.append("    <changefreq>yearly</changefreq>")
            parts.append("    <priority>0.5</priority>")
            parts.append("  </url>")
        parts.append("</urlset>")

        xml = "\n".join(parts)
        # Cache for 24h
        try:
            cache_set(cache_key, xml, ttl=86400)
        except Exception:
            pass
        return Response(xml, mimetype="application/xml")

    @app.route("/opensearch.xml")
    def opensearch_descriptor():
        """OpenSearch descriptor — lets browsers add Profoundd as a search engine."""
        domain = app.config.get("DOMAIN", "profoundd.com")
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>Profoundd</ShortName>
  <Description>Independent search: news, Epstein files, government docs, source credibility</Description>
  <InputEncoding>UTF-8</InputEncoding>
  <Image width="32" height="32" type="image/png">https://{domain}/static/images/favicon-32.png</Image>
  <Url type="text/html" template="https://{domain}/search?q={{searchTerms}}"/>
  <Url type="application/opensearchdescription+xml" rel="self" template="https://{domain}/opensearch.xml"/>
  <moz:SearchForm xmlns:moz="http://www.mozilla.org/2006/browser/search/">https://{domain}/search</moz:SearchForm>
</OpenSearchDescription>"""
        return Response(xml, mimetype="application/opensearchdescription+xml")

    @app.route("/indexnow-key.txt")
    def indexnow_key_file():
        """Serve the IndexNow verification key at a fixed path.

        IndexNow allows the keyLocation to point to any URL on the host, as
        long as the file content matches the key. We use a stable path so we
        don't need a dynamic URL and can't collide with other routes.
        """
        from profoundd.utils.indexnow import get_or_create_key
        try:
            stored_key = get_or_create_key()
        except Exception:
            return Response("", status=503, mimetype="text/plain")
        return Response(stored_key, mimetype="text/plain")

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
