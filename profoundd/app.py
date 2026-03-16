"""
Main Flask application for Profoundd search engine.
"""
import os
import uuid
import logging
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, flash, redirect, Response, url_for, make_response
from flask_cors import CORS
from flask_login import LoginManager

from profoundd.config.settings import get_config
from profoundd.config.sources import CATEGORIES
from profoundd.utils.models import db, AdminUser, SearchLog, Source, SourceSubmission, SiteSetting, BobStory, NewsroomNote, SourceNote, PageView
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

        def get_newsroom_note(url):
            """Look up an editorial note for an article URL."""
            if not url:
                return None
            return db.session.query(NewsroomNote).filter_by(article_url=url).first()

        def get_source_note(source_name):
            """Look up a credibility note for a content source."""
            if not source_name:
                return None
            return db.session.query(SourceNote).filter_by(source_name=source_name).first()

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
        }

    # --- Analytics: record page views when cookies are accepted ---
    @app.after_request
    def track_page_view(response):
        # Only track HTML pages, skip static/API/admin/health
        path = request.path
        if (
            request.method != "GET"
            or path.startswith(("/static", "/api/", "/admin", "/health", "/robots", "/sitemap"))
            or response.status_code >= 400
        ):
            return response

        # Check if the visitor has accepted analytics cookies
        consent = request.cookies.get("cookie_consent")
        if consent != "accepted":
            return response

        # Get or assign a visitor ID cookie
        visitor_id = request.cookies.get("profoundd_vid")
        if not visitor_id:
            visitor_id = uuid.uuid4().hex
            response.set_cookie(
                "profoundd_vid", visitor_id,
                max_age=365 * 24 * 3600,  # 1 year
                httponly=True,
                samesite="Lax",
                secure=request.is_secure,
            )

        try:
            pv = PageView(
                path=path[:1000],
                visitor_id=visitor_id,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
                referrer=(request.referrer or "")[:1000],
            )
            db.session.add(pv)
            db.session.commit()
        except Exception:
            logger.exception("Failed to record page view for %s", path)
            db.session.rollback()

        return response

    # --- Beacon endpoint: record the page view when cookies are accepted ---
    @app.route("/api/analytics/beacon", methods=["POST"])
    def analytics_beacon():
        """Record a page view via JS beacon (fires on cookie accept)."""
        data = request.get_json(silent=True) or {}
        page_path = (data.get("path") or "/")[:1000]
        referrer = (data.get("referrer") or "")[:1000]

        visitor_id = request.cookies.get("profoundd_vid")
        if not visitor_id:
            visitor_id = uuid.uuid4().hex

        try:
            pv = PageView(
                path=page_path,
                visitor_id=visitor_id,
                ip_address=request.remote_addr,
                user_agent=(request.user_agent.string or "")[:500],
                referrer=referrer,
            )
            db.session.add(pv)
            db.session.commit()
        except Exception:
            logger.exception("Failed to record beacon page view")
            db.session.rollback()
            return jsonify(ok=False), 500

        resp = jsonify(ok=True)
        if not request.cookies.get("profoundd_vid"):
            resp.set_cookie(
                "profoundd_vid", visitor_id,
                max_age=365 * 24 * 3600,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure,
            )
        return resp

    # Initialize search engine
    search_engine = SearchEngine(app.config.get("ELASTICSEARCH_URL", "http://localhost:9200"))

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
        ]:
            try:
                db.session.execute(db.text(col_sql))
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

    @app.route("/search")
    def search():
        """Main search endpoint."""
        from profoundd.search.external_providers import (
            fetch_all_enhanced, fetch_searxng, fetch_brave_web, boost_known_domains,
            fetch_grokipedia,
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
        web_promoted = False
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

            # Fetch Grokipedia results and insert near the top
            grok_results = fetch_grokipedia(query, max_results=3)
            if grok_results:
                existing_urls = {a.get("url") for a in results.get("articles", [])}
                insert_pos = min(1, len(results.get("articles", [])))
                for gr in grok_results:
                    if gr.get("url") not in existing_urls:
                        results["articles"].insert(insert_pos, gr)
                        insert_pos += 1

            # Always fetch external web results so every search taps sources
            # beyond Profoundd's curated index — especially important for
            # controversial topics not covered by mainstream media.
            web_results = []
            searxng_url = SiteSetting.get("searxng_url", "")
            if searxng_url:
                web_results = fetch_searxng(query, searxng_url, max_results=10)
            if not web_results:
                # Brave backup when SearXNG is down or returns nothing
                web_results = fetch_brave_web(query, max_results=10)
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

                # Check if local results are actually relevant to the query.
                # If top local results don't contain most query terms in
                # their title, the local index doesn't cover this topic —
                # put web results first so the user finds what they need.
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
                    # Local results match — interleave web after every 3
                    blended = list(local_articles)
                    for i, wr in enumerate(new_web):
                        pos = min(3 + i * 4 + i, len(blended))
                        blended.insert(pos, wr)
                else:
                    # Local results are weak matches — web results go first
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

        return render_template("search.html", results=results, categories=CATEGORIES,
                               query=query, category=category, sort_by=sort_by,
                               enhanced_providers=enhanced_providers,
                               web_fallback=web_fallback,
                               web_promoted=web_promoted)

    @app.route("/api/ai-summary")
    def api_ai_summary():
        """Async endpoint for AI search summary. Cookie-limited to 5/day."""
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"answer": "", "error": "No query", "remaining": 0})

        # Cookie-based rate limit: 5 AI summaries per user per day
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cookie_val = request.cookies.get("ai_searches", "")
        ai_count = 0
        if cookie_val:
            parts = cookie_val.split(":", 1)
            if len(parts) == 2 and parts[0] == today:
                ai_count = int(parts[1])

        if ai_count >= 5:
            return jsonify({"answer": "", "error": "limit_reached", "remaining": 0})

        # Get search results to summarize (local index + Grokipedia + web)
        results = search_engine.search(query=query, category="all", page=1)
        articles = results.get("articles", [])

        # Include Grokipedia articles so AI can cite them
        try:
            grok_articles = fetch_grokipedia(query, max_results=3)
            if grok_articles:
                articles = grok_articles + articles
        except Exception:
            pass

        # Include web results so AI has broader coverage
        searxng_url = app.config.get("SEARXNG_URL", "")
        if searxng_url:
            try:
                web_articles = fetch_searxng(query, searxng_url, max_results=5)
                if web_articles:
                    articles.extend(web_articles)
            except Exception:
                pass

        if not articles:
            return jsonify({"answer": "", "error": "No results to summarize", "remaining": 5 - ai_count})

        ai_result = ai_generate_summary(query, articles)

        # Increment counter and set cookie
        ai_count += 1
        remaining = 5 - ai_count
        ai_result["remaining"] = remaining
        resp = make_response(jsonify(ai_result))
        resp.set_cookie("ai_searches", f"{today}:{ai_count}",
                        max_age=86400, samesite="Lax", httponly=False)
        return resp

    @app.route("/category/<category_name>")
    def category_page(category_name):
        """Browse a specific category with pagination."""
        if category_name not in CATEGORIES:
            return render_template("404.html", categories=CATEGORIES), 404

        page = request.args.get("page", 1, type=int)
        subcategory = request.args.get("sub", "all")
        per_page = 20

        articles = []
        total = 0
        pages = 0
        if search_engine.is_available():
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
