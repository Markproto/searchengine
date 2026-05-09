"""
Database models for Profoundd search engine.
Stores sources, articles, rankings, and admin users.
"""
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

db = SQLAlchemy()


class AdminUser(UserMixin, db.Model):
    """Admin users who can manage sources and rankings."""
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    magic_token = db.Column(db.String(128), nullable=True)
    magic_token_expires = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Source(db.Model):
    """A feed source (RSS/Atom) that we crawl."""
    __tablename__ = "sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False, index=True)
    feed_type = db.Column(db.String(20), default="rss")
    is_active = db.Column(db.Boolean, default=True)

    # Ranking criteria (1-10 scale)
    credibility = db.Column(db.Integer, default=5)
    bias_score = db.Column(db.Integer, default=5)  # 1=far-left, 5=center, 10=far-right
    update_frequency = db.Column(db.Integer, default=5)  # how often it publishes

    # Custom weights for ranking algorithm
    credibility_weight = db.Column(db.Float, default=0.4)
    recency_weight = db.Column(db.Float, default=0.3)
    relevance_weight = db.Column(db.Float, default=0.3)

    # Subcategory for finer classification (e.g. "executive-orders", "house", "senate")
    subcategory = db.Column(db.String(100), default="")

    # Sponsor disclosure (comma-separated sponsor names, e.g. "Pfizer,Johnson & Johnson")
    sponsor_tags = db.Column(db.String(500), default="")

    # Metadata
    last_crawled = db.Column(db.DateTime)
    articles_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    articles = db.relationship("Article", backref="source", lazy="dynamic")

    def __repr__(self):
        return f"<Source {self.name} [{self.category}]>"

    def compute_rank(self):
        """Compute overall source rank from criteria."""
        return (
            self.credibility * self.credibility_weight +
            self.update_frequency * self.recency_weight +
            5 * self.relevance_weight  # base relevance
        )


class Article(db.Model):
    """An indexed article from a source."""
    __tablename__ = "articles"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(1000), unique=True, nullable=False)
    summary = db.Column(db.Text)
    content = db.Column(db.Text)
    author = db.Column(db.String(200))
    category = db.Column(db.String(50), index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), index=True)
    source_name = db.Column(db.String(200))

    # Timestamps
    published_at = db.Column(db.DateTime, index=True)
    crawled_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Search metadata
    elasticsearch_id = db.Column(db.String(100))
    is_indexed = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Article {self.title[:50]}>"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "author": self.author,
            "category": self.category,
            "source_name": self.source_name,
            "published_at": self.published_at.isoformat() if self.published_at else None,
        }


class SearchLog(db.Model):
    """Log of search queries for analytics."""
    __tablename__ = "search_logs"

    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(50))
    results_count = db.Column(db.Integer, default=0)
    ip_address = db.Column(db.String(45))
    searched_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class CrawlLog(db.Model):
    """Log of crawl runs for monitoring."""
    __tablename__ = "crawl_logs"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    articles_found = db.Column(db.Integer, default=0)
    articles_new = db.Column(db.Integer, default=0)
    articles_duplicate = db.Column(db.Integer, default=0)
    errors = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="running")  # running, success, failed, empty
    trigger = db.Column(db.String(20), default="manual")  # manual, scheduler
    category = db.Column(db.String(50))
    duration_seconds = db.Column(db.Float)


class SourceSubmission(db.Model):
    """Public source suggestion from users."""
    __tablename__ = "source_submissions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    feed_type = db.Column(db.String(20), default="rss")
    reason = db.Column(db.Text)  # why they want this source added
    submitted_by = db.Column(db.String(100))  # optional name/handle
    ip_address = db.Column(db.String(45))
    status = db.Column(db.String(20), default="pending")  # pending, approved, rejected
    admin_notes = db.Column(db.Text)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    reviewed_at = db.Column(db.DateTime)


class SiteSetting(db.Model):
    """Key-value store for admin-editable site content."""
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    @staticmethod
    def get(key, default=""):
        setting = db.session.query(SiteSetting).filter_by(key=key).first()
        return setting.value if setting else default

    @staticmethod
    def set(key, value):
        setting = db.session.query(SiteSetting).filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = SiteSetting(key=key, value=value)
            db.session.add(setting)
        db.session.commit()


class DomainCredibility(db.Model):
    """External-dataset domain credibility ratings (Iffy.news, CRED-1, Wikipedia RSP, MBFC).

    Bootstrapped from public datasets to give the Curator + ranker a 5k-domain head start.
    Domain is the apex (no www, no scheme). Score 0.0=spam/fake, 1.0=high credibility.
    Bias: 1=far-left, 5=center, 10=far-right (matches Source.bias_score).
    """
    __tablename__ = "domain_credibility"

    domain = db.Column(db.String(255), primary_key=True)
    credibility = db.Column(db.Float, nullable=False, index=True)  # 0.0-1.0
    bias_score = db.Column(db.Integer, nullable=True)              # 1-10 or null
    category = db.Column(db.String(50), nullable=True)             # "fake", "satire", "questionable", "pro", etc.
    sources = db.Column(db.String(120), nullable=False)            # comma-sep: "iffy,cred1,rsp,mbfc"
    notes = db.Column(db.Text, default="")
    last_updated = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    @staticmethod
    def lookup(domain):
        """Return DomainCredibility row for an apex domain, or None."""
        if not domain:
            return None
        return db.session.query(DomainCredibility).filter_by(domain=domain.lower()).first()


class ResearchDocument(db.Model):
    """Admin-curated research documents persisted in DB so they survive ES rebuilds."""
    __tablename__ = "research_documents"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    content = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text)
    category = db.Column(db.String(50), default="news")
    source_name = db.Column(db.String(200), default="Profoundd Research")
    doc_url = db.Column(db.String(500), unique=True, nullable=False)
    source_url = db.Column(db.String(1000))  # original source article link
    image_url = db.Column(db.String(1000))   # image from source (og:image or manual)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_es_doc(self):
        """Convert to Elasticsearch document format for indexing."""
        doc = {
            "title": self.title,
            "summary": self.summary or self.title,
            "content": self.content,
            "author": "Admin",
            "category": self.category,
            "source_name": self.source_name,
            "source_credibility": 9,
            "url": self.doc_url,
            "published_at": self.created_at.isoformat() if self.created_at else datetime.now(timezone.utc).isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.source_url:
            doc["source_url"] = self.source_url
        if self.image_url:
            doc["image_url"] = self.image_url
        return doc


class AdminRankingAction(db.Model):
    """Logs admin promote/demote actions on articles for AI training."""
    __tablename__ = "admin_ranking_actions"

    id = db.Column(db.Integer, primary_key=True)
    article_url = db.Column(db.String(1000), nullable=False, index=True)
    article_title = db.Column(db.String(500))
    source_name = db.Column(db.String(200))
    category = db.Column(db.String(50))
    action = db.Column(db.String(20), nullable=False)  # "promote" or "demote"
    old_boost = db.Column(db.Integer, default=0)
    new_boost = db.Column(db.Integer, default=0)
    search_query = db.Column(db.String(500))  # what the admin was searching when they acted
    acted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_training_dict(self):
        """Format for AI training: what the admin decided and why context."""
        return {
            "action": self.action,
            "article_title": self.article_title,
            "source_name": self.source_name,
            "category": self.category,
            "boost_change": f"{self.old_boost} -> {self.new_boost}",
            "search_context": self.search_query,
            "timestamp": self.acted_at.isoformat() if self.acted_at else None,
        }


class BobStory(db.Model):
    """AI-generated stories by NewsRoom Bob, based on original articles."""
    __tablename__ = "bob_stories"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    slug = db.Column(db.String(300), unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)
    summary = db.Column(db.Text)
    seo_keywords = db.Column(db.String(500))
    seo_description = db.Column(db.String(300))
    category = db.Column(db.String(50), index=True)
    extra_categories = db.Column(db.String(500), default="")  # comma-separated extra categories
    image_url = db.Column(db.String(1000))

    # Link back to the original source
    source_article_url = db.Column(db.String(1000), nullable=False)
    source_article_title = db.Column(db.String(500))
    source_name = db.Column(db.String(200))

    status = db.Column(db.String(20), default="published")  # draft, published
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    published_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_es_doc(self):
        """Convert to Elasticsearch document format for indexing."""
        return {
            "title": self.title,
            "summary": self.summary or self.title,
            "content": self.content,
            "author": "NewsRoom Bob",
            "category": self.category or "news",
            "source_name": "Profoundd NewsRoom",
            "source_credibility": 8,
            "url": f"profoundd://bob/{self.slug}",
            "tags": [t.strip() for t in (self.seo_keywords or "").split(",") if t.strip()],
            "published_at": self.published_at.isoformat() if self.published_at else datetime.now(timezone.utc).isoformat(),
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        }


class DailyStats(db.Model):
    """Privacy-friendly aggregate daily traffic counters.

    No PII stored — just date + page_type + counts. Incremented server-side
    on every request without requiring cookies or consent.
    """
    __tablename__ = "daily_stats"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)
    page_type = db.Column(db.String(30), nullable=False)  # home, search, category, article, epstein-doc, newsroom, other
    is_bot = db.Column(db.Boolean, default=False)
    requests = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint("date", "page_type", "is_bot", name="uq_daily_stats"),
    )

    @classmethod
    def increment(cls, date_val, page_type, is_bot=False):
        """Atomically increment the counter for this date/page_type/is_bot combo."""
        row = cls.query.filter_by(date=date_val, page_type=page_type, is_bot=is_bot).first()
        if row:
            row.requests = (row.requests or 0) + 1
        else:
            row = cls(date=date_val, page_type=page_type, is_bot=is_bot, requests=1)
            db.session.add(row)


class SavedAlert(db.Model):
    """A user's saved search that emails them when new matches appear."""
    __tablename__ = "saved_alerts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("public_users.id"), nullable=False, index=True)
    query = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(50), default="all")
    # Frequency: "immediate" (every 15 min check), "daily", "weekly"
    frequency = db.Column(db.String(20), default="daily")
    enabled = db.Column(db.Boolean, default=True)
    # Unsubscribe token — lets users disable without logging in
    unsub_token = db.Column(db.String(64), unique=True, index=True)
    # Track last time we successfully ran + emailed for this alert
    last_checked_at = db.Column(db.DateTime)
    last_emailed_at = db.Column(db.DateTime)
    # Count of emails sent (for UI display + abuse detection)
    emails_sent = db.Column(db.Integer, default=0)
    # Hash of URLs already emailed (JSON array, last 500 URL hashes) — prevents
    # re-emailing the same article if it resurfaces on a later check
    seen_urls = db.Column(db.Text, default="[]")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class AudioTranscript(db.Model):
    """Audio files uploaded by admin, transcribed to text for Bob story seed."""
    __tablename__ = "audio_transcripts"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(300))
    duration_seconds = db.Column(db.Float)
    source_url = db.Column(db.String(1000))  # optional: podcast URL / YouTube
    source_description = db.Column(db.String(500))  # e.g. "Joe Rogan #2024 w/ RFK Jr"
    transcript = db.Column(db.Text)  # admin-editable
    detected_language = db.Column(db.String(20))
    # Status: transcribed | story_drafted | archived
    status = db.Column(db.String(20), default="transcribed")
    bob_story_id = db.Column(db.Integer, nullable=True)  # soft FK to BobStory
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    transcribed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ArticleVote(db.Model):
    """Public thumbs up/down votes on search results."""
    __tablename__ = "article_votes"

    id = db.Column(db.Integer, primary_key=True)
    article_url = db.Column(db.String(1000), nullable=False, index=True)
    vote = db.Column(db.Integer, nullable=False)  # +1 or -1
    ip_address = db.Column(db.String(45))
    voted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('article_url', 'ip_address', name='unique_vote_per_ip'),
    )


class PageView(db.Model):
    """Tracks individual page views for analytics."""
    __tablename__ = "page_views"

    id = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.String(1000), nullable=False, index=True)
    visitor_id = db.Column(db.String(64), index=True)  # cookie-based anonymous ID
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    referrer = db.Column(db.String(1000))
    country = db.Column(db.String(10))  # optional, from IP
    session_id = db.Column(db.String(64), index=True)  # tab-scoped session
    is_bot = db.Column(db.Boolean, default=False)
    duration = db.Column(db.Integer)  # seconds on page
    screen_width = db.Column(db.Integer)
    screen_height = db.Column(db.Integer)
    language = db.Column(db.String(20))
    viewed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ArticleClick(db.Model):
    """Tracks outbound clicks on search results for audience leaning analysis."""
    __tablename__ = "article_clicks"

    id = db.Column(db.Integer, primary_key=True)
    visitor_id = db.Column(db.String(64), index=True)
    session_id = db.Column(db.String(64))
    source_name = db.Column(db.String(200), index=True)
    bias_score = db.Column(db.Integer)  # 1=far-left, 5=center, 10=far-right
    article_url = db.Column(db.String(1000))
    search_query = db.Column(db.String(500))
    category = db.Column(db.String(50))
    clicked_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class NewsroomNote(db.Model):
    """Editorial notes added by admin to any article, visible to readers."""
    __tablename__ = "newsroom_notes"

    id = db.Column(db.Integer, primary_key=True)
    article_url = db.Column(db.String(1000), nullable=False, unique=True, index=True)
    article_title = db.Column(db.String(500))
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PublicUser(db.Model):
    """Public user accounts for AI analysis feature."""
    __tablename__ = "public_users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256))  # nullable — magic link first, password optional
    magic_token = db.Column(db.String(128), index=True)
    magic_token_expires = db.Column(db.DateTime)

    # AI provider settings
    api_provider = db.Column(db.String(20), default="")   # anthropic, openai, xai
    api_key_encrypted = db.Column(db.Text, default="")     # encrypted API key

    # Usage tracking
    ai_uses_count = db.Column(db.Integer, default=0)
    agreed_to_terms = db.Column(db.Boolean, default=False)
    agreed_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def set_api_key(self, key, provider):
        """Store API key with basic obfuscation (base64). For production, use Fernet."""
        import base64
        self.api_provider = provider
        self.api_key_encrypted = base64.b64encode(key.encode()).decode() if key else ""

    def get_api_key(self):
        """Retrieve stored API key."""
        import base64
        if not self.api_key_encrypted:
            return ""
        try:
            return base64.b64decode(self.api_key_encrypted.encode()).decode()
        except Exception:
            return ""


class AIAnalysis(db.Model):
    """Stored AI analysis results for articles."""
    __tablename__ = "ai_analyses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("public_users.id"), nullable=True, index=True)
    article_url = db.Column(db.String(1000), nullable=False, index=True)
    article_title = db.Column(db.String(500))
    query_text = db.Column(db.Text)           # what user asked or article context
    analysis_text = db.Column(db.Text)         # full AI response
    provider_used = db.Column(db.String(20))   # anthropic, openai, xai
    model_used = db.Column(db.String(100))
    sentiment = db.Column(db.String(20))       # positive, negative, neutral, mixed
    bias_notes = db.Column(db.Text)
    market_odds_json = db.Column(db.Text)      # JSON snapshot of related prediction market odds
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship("PublicUser", backref="analyses", lazy=True)


class PollSnapshot(db.Model):
    """Cached polling data for accuracy tracking and display."""
    __tablename__ = "poll_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.String(100), index=True)        # unique ID from source
    source = db.Column(db.String(50), nullable=False)       # "538", "predictit", etc.
    pollster = db.Column(db.String(200), nullable=False)
    pollster_rating = db.Column(db.Float)                   # 538 numeric grade
    race = db.Column(db.String(100))                        # "president", "senate", "governor"
    state = db.Column(db.String(5), index=True)             # "US" for national, or state abbrev
    question = db.Column(db.String(500))
    candidate_1 = db.Column(db.String(200))
    candidate_1_pct = db.Column(db.Float)
    candidate_1_party = db.Column(db.String(10))
    candidate_2 = db.Column(db.String(200))
    candidate_2_pct = db.Column(db.Float)
    candidate_2_party = db.Column(db.String(10))
    margin = db.Column(db.Float)                            # candidate_1_pct - candidate_2_pct
    sample_size = db.Column(db.Integer)
    methodology = db.Column(db.String(100))                 # "Live Phone", "Online Panel", etc.
    poll_date = db.Column(db.String(20))                    # date poll was conducted
    cycle = db.Column(db.String(10))                        # "2026", "2028"
    url = db.Column(db.String(1000))
    snapshot_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        db.Index('ix_poll_state_race', 'state', 'race'),
    )


class PolymarketSnapshot(db.Model):
    """Cached Polymarket event data for AI sentiment analysis."""
    __tablename__ = "polymarket_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(50), nullable=False, index=True)
    event_slug = db.Column(db.String(300), nullable=False)
    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(50))           # our subcategory mapping
    outcomes_json = db.Column(db.Text)             # JSON: [{"label":"Yes","pct":62.0,"question":"..."},...]
    volume_total = db.Column(db.Float, default=0)  # lifetime USD volume
    volume_24hr = db.Column(db.Float, default=0)   # 24h USD volume
    liquidity = db.Column(db.Float, default=0)
    end_date = db.Column(db.String(30))            # ISO date string
    image_url = db.Column(db.String(1000))
    market_slugs_json = db.Column(db.Text)         # JSON list of individual market slugs for embeds
    tags_json = db.Column(db.Text)                 # JSON list of tag labels
    snapshot_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        db.Index('ix_poly_event_time', 'event_id', 'snapshot_at'),
    )


class SourceNote(db.Model):
    """Editorial notes on content sources — why they are or aren't trustworthy."""
    __tablename__ = "source_notes"

    id = db.Column(db.Integer, primary_key=True)
    source_name = db.Column(db.String(200), nullable=False, unique=True, index=True)
    note_text = db.Column(db.Text, nullable=False)
    stance = db.Column(db.String(20), default="neutral")  # "trustworthy", "caution", "neutral"
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
