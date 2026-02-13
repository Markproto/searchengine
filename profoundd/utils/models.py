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
