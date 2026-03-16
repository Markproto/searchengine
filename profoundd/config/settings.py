"""
Central configuration for Profoundd search engine.
Loads from environment variables with sensible defaults.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Project root directory (parent of profoundd/ package)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    DOMAIN = os.getenv("DOMAIN", "profoundd.com")

    # Database - use absolute path
    _default_db = "sqlite:///" + os.path.join(BASE_DIR, "data", "profoundd.db")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", _default_db)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Elasticsearch
    ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")

    # Admin
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

    # Crawler
    CRAWL_INTERVAL_MINUTES = int(os.getenv("CRAWL_INTERVAL_MINUTES", "20"))
    MAX_ARTICLES_PER_FEED = int(os.getenv("MAX_ARTICLES_PER_FEED", "50"))
    RESPECT_ROBOTS_TXT = os.getenv("RESPECT_ROBOTS_TXT", "true").lower() == "true"
    USER_AGENT = os.getenv("USER_AGENT", "ProfounddBot/1.0 (+https://profoundd.com/bot)")
    CRAWL_DELAY_SECONDS = int(os.getenv("CRAWL_DELAY_SECONDS", "2"))
    CONCURRENT_REQUESTS = int(os.getenv("CONCURRENT_REQUESTS", "4"))

    # OSM / Local Business
    OSM_DATA_DIR = os.path.join(BASE_DIR, "data", "osm")
    OSM_REFRESH_HOURS = int(os.getenv("OSM_REFRESH_HOURS", "24"))


class DevelopmentConfig(Config):
    DEBUG = True
    FLASK_ENV = "development"


class ProductionConfig(Config):
    DEBUG = False
    FLASK_ENV = "production"


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    ELASTICSEARCH_URL = "http://localhost:9200"


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config():
    env = os.getenv("FLASK_ENV", "production")
    return config_map.get(env, ProductionConfig)
