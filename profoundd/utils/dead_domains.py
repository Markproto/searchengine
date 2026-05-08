"""Rewrite article URLs whose origin domains no longer resolve to Wayback snapshots."""
from urllib.parse import urlparse

WAYBACK_PREFIX = "https://web.archive.org/web/"

# Apex domains whose canonical site is dead (NXDOMAIN, redirected to a parking
# page, or otherwise unreachable). Any stored article URL on one of these gets
# rewritten to a Wayback snapshot at render time.
DEAD_DOMAINS = {
    "mailtribune.com",
    "dailytidings.com",
}


def _apex(host: str) -> str:
    parts = (host or "").lower().split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host or ""


def is_dead(url: str) -> bool:
    if not url or url.startswith("profoundd://") or "web.archive.org" in url:
        return False
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return _apex(host) in DEAD_DOMAINS


def to_wayback(url: str, timestamp: str | None = None) -> str:
    """Wrap a URL with the Wayback resolver. Empty timestamp lets web.archive.org pick the closest snapshot."""
    ts = (timestamp or "").strip() or "*"
    return f"{WAYBACK_PREFIX}{ts}/{url}"


def rewrite_article(article: dict) -> dict:
    """Mutate article in place: if URL is on a dead domain, point it at Wayback."""
    url = article.get("url") or ""
    if not is_dead(url):
        return article
    ts = article.get("wayback_timestamp") or article.get("archived_at") or ""
    article["original_url"] = url
    article["url"] = to_wayback(url, ts)
    article["via_wayback"] = True
    return article


def rewrite_articles(articles):
    for a in articles or []:
        rewrite_article(a)
    return articles
