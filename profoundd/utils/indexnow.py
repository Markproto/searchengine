"""
IndexNow protocol implementation.

IndexNow is an open protocol that lets a site push new/updated URLs to
Bing, Yandex, Yep, Seznam, and Naver in a single request. It's the modern
alternative to sitemap crawling for signaling new content.

Docs: https://www.indexnow.org/
"""
import hashlib
import logging
import secrets

import requests

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def get_or_create_key():
    """Return the IndexNow key from SiteSetting, creating one if missing.
    The key doubles as a verification token — we serve it at /{key}.txt.
    """
    from profoundd.utils.models import SiteSetting, db
    key = SiteSetting.get("indexnow_key", "")
    if not key:
        key = secrets.token_hex(16)  # 32 chars
        SiteSetting.set("indexnow_key", key)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return key


def ping_indexnow(urls, host="profoundd.com"):
    """Push a list of URLs to IndexNow.

    Returns (ok: bool, status: int, message: str).
    Accepts up to 10,000 URLs per request. Caller is responsible for batching.
    """
    if not urls:
        return True, 200, "no urls"
    if len(urls) > 10000:
        urls = urls[:10000]

    key = get_or_create_key()
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/indexnow-key.txt",
        "urlList": list(urls),
    }
    try:
        resp = requests.post(
            INDEXNOW_ENDPOINT,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15,
        )
        if resp.status_code in (200, 202):
            logger.info("IndexNow pinged %d URLs (status %d)", len(urls), resp.status_code)
            return True, resp.status_code, "ok"
        else:
            logger.warning("IndexNow returned %d: %s", resp.status_code, resp.text[:200])
            return False, resp.status_code, resp.text[:200]
    except Exception as e:
        logger.warning("IndexNow ping failed: %s", e)
        return False, 0, str(e)


def ping_async(urls, host="profoundd.com"):
    """Fire-and-forget IndexNow ping on a background thread."""
    import threading
    if not urls:
        return
    threading.Thread(
        target=ping_indexnow, args=(list(urls), host), daemon=True
    ).start()
