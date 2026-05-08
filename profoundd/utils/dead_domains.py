"""Rewrite article URLs on dead domains to a Profoundd-hosted snapshot route.

We mirror the article body in our own ES index, so when the origin is gone we
serve our copy at /snapshot/<sha1(url)> rather than handing the user off to
web.archive.org. We're archiving the internet here, not just linking to it.
"""
import hashlib
from urllib.parse import urlparse

# Apex domains whose canonical site is dead (NXDOMAIN, redirected to a parking
# page, or otherwise unreachable). Any stored article URL on one of these gets
# rewritten to a local /snapshot/<id> route at render time.
DEAD_DOMAINS = {
    "mailtribune.com",
}


def url_hash(url: str) -> str:
    return hashlib.md5((url or "").encode("utf-8")).hexdigest()


def to_snapshot_path(url: str) -> str:
    return f"/snapshot/{url_hash(url)}"


def _apex(host: str) -> str:
    parts = (host or "").lower().split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host or ""


def is_dead(url: str) -> bool:
    if not url or url.startswith("profoundd://") or url.startswith("/"):
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _apex(host) in DEAD_DOMAINS


def rewrite_article(article: dict) -> dict:
    """Mutate article in place: if URL is on a dead domain, point it at our local snapshot."""
    url = article.get("url") or ""
    if not is_dead(url):
        return article
    article["original_url"] = url
    article["url"] = to_snapshot_path(url)
    article["via_local_snapshot"] = True
    return article


def rewrite_articles(articles):
    for a in articles or []:
        rewrite_article(a)
    return articles
