"""
Tests for Profoundd search engine.
"""
import pytest
from profoundd.app import create_app
from profoundd.utils.models import db as _db, AdminUser, Source
from profoundd.config.settings import TestingConfig


@pytest.fixture
def app():
    app = create_app(config_override=TestingConfig)
    with app.app_context():
        _db.create_all()
        # Create test admin
        admin = AdminUser(username="testadmin")
        admin.set_password("testpass")
        _db.session.add(admin)
        _db.session.commit()
        yield app
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


class TestHomepage:
    def test_homepage_loads(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Profoundd" in response.data
        assert b"Search What Matters" in response.data

    def test_homepage_has_categories(self, client):
        response = client.get("/")
        assert b"News" in response.data
        assert b"Medical" in response.data
        assert b"Legal" in response.data
        assert b"Technology" in response.data
        assert b"Finance" in response.data
        assert b"Science" in response.data


class TestSearchPage:
    def test_search_page_loads(self, client):
        response = client.get("/search")
        assert response.status_code == 200

    def test_search_with_query(self, client):
        response = client.get("/search?q=test")
        assert response.status_code == 200

    def test_search_with_category(self, client):
        response = client.get("/search?q=health&category=medical")
        assert response.status_code == 200


class TestAPI:
    def test_api_search_requires_query(self, client):
        response = client.get("/api/search")
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_api_search_with_query(self, client):
        response = client.get("/api/search?q=test")
        assert response.status_code == 200
        data = response.get_json()
        assert "articles" in data or "error" in data

    def test_api_trending(self, client):
        response = client.get("/api/trending")
        assert response.status_code == 200


class TestAboutPage:
    def test_about_loads(self, client):
        response = client.get("/about")
        assert response.status_code == 200
        assert b"About Profoundd" in response.data


class TestAdminLogin:
    def test_admin_redirects_to_login(self, client):
        response = client.get("/admin/", follow_redirects=False)
        assert response.status_code == 302

    def test_login_page_loads(self, client):
        response = client.get("/admin/login")
        assert response.status_code == 200
        assert b"Admin Login" in response.data

    def test_login_with_valid_credentials(self, client):
        response = client.post("/admin/login", data={
            "username": "testadmin",
            "password": "testpass",
        }, follow_redirects=True)
        assert response.status_code == 200

    def test_login_with_invalid_credentials(self, client):
        response = client.post("/admin/login", data={
            "username": "wrong",
            "password": "wrong",
        }, follow_redirects=True)
        assert b"Invalid credentials" in response.data


class TestAdminDashboard:
    def _login(self, client):
        client.post("/admin/login", data={
            "username": "testadmin",
            "password": "testpass",
        })

    def test_dashboard_loads(self, client):
        self._login(client)
        response = client.get("/admin/")
        assert response.status_code == 200

    def test_sources_list(self, client):
        self._login(client)
        response = client.get("/admin/sources")
        assert response.status_code == 200

    def test_add_source_form(self, client):
        self._login(client)
        response = client.get("/admin/sources/add")
        assert response.status_code == 200


class TestModels:
    def test_admin_password_hashing(self, app):
        with app.app_context():
            user = AdminUser(username="test")
            user.set_password("secret")
            assert user.check_password("secret")
            assert not user.check_password("wrong")

    def test_source_compute_rank(self, app):
        with app.app_context():
            source = Source(
                name="Test", url="http://test.com/rss", category="news",
                credibility=8, update_frequency=6,
                credibility_weight=0.4, recency_weight=0.3, relevance_weight=0.3,
            )
            rank = source.compute_rank()
            expected = 8 * 0.4 + 6 * 0.3 + 5 * 0.3
            assert abs(rank - expected) < 0.01


class TestSources:
    def test_all_sources_have_required_fields(self):
        from profoundd.config.sources import ALL_SOURCES
        for src in ALL_SOURCES:
            assert "name" in src, f"Source missing name"
            assert "url" in src, f"Source {src.get('name', '?')} missing url"
            assert "category" in src, f"Source {src.get('name', '?')} missing category"
            assert "credibility" in src, f"Source {src.get('name', '?')} missing credibility"

    def test_categories_defined(self):
        from profoundd.config.sources import CATEGORIES
        assert "news" in CATEGORIES
        assert "medical" in CATEGORIES
        assert "legal" in CATEGORIES
        assert "tech" in CATEGORIES
        assert "finance" in CATEGORIES
        assert "science" in CATEGORIES
        assert "education" in CATEGORIES
        assert "environment" in CATEGORIES
        assert "politics" in CATEGORIES

    def test_source_count(self):
        from profoundd.config.sources import ALL_SOURCES
        assert len(ALL_SOURCES) >= 70  # We have 80+ sources


class TestNewEndpoints:
    def test_health_check(self, client):
        response = client.get("/health")
        assert response.status_code in (200, 503)
        data = response.get_json()
        assert "status" in data
        assert "version" in data

    def test_api_suggest_requires_query(self, client):
        response = client.get("/api/suggest")
        data = response.get_json()
        assert data["suggestions"] == []

    def test_api_suggest_short_query(self, client):
        response = client.get("/api/suggest?q=a")
        data = response.get_json()
        assert data["suggestions"] == []

    def test_api_sources(self, client):
        response = client.get("/api/sources")
        assert response.status_code == 200
        data = response.get_json()
        assert "categories" in data

    def test_category_page_valid(self, client):
        response = client.get("/category/news")
        assert response.status_code == 200

    def test_category_page_invalid(self, client):
        response = client.get("/category/nonexistent")
        assert response.status_code == 404

    def test_crawl_history_requires_auth(self, client):
        response = client.get("/admin/crawl-history", follow_redirects=False)
        assert response.status_code == 302


class TestCrawler:
    def test_crawler_deduplication(self):
        from profoundd.crawler.feed_crawler import FeedCrawler
        crawler = FeedCrawler.__new__(FeedCrawler)
        crawler._seen_urls = set()
        assert not crawler._is_duplicate("http://example.com/1")
        assert crawler._is_duplicate("http://example.com/1")
        assert not crawler._is_duplicate("http://example.com/2")
