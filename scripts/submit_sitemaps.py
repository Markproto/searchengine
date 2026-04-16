"""
Submit Profoundd's sitemap to independent search engines.

Usage:
    python scripts/submit_sitemaps.py

Runs submissions to:
- IndexNow (Bing, Yandex, Yep, Seznam, Naver — all at once)
- Mojeek (direct form submission)
- Marginalia (direct form submission)

Prints instructions for Brave Search (no public API).
"""
import sys
import time

import requests

DOMAIN = "profoundd.com"
BASE = f"https://{DOMAIN}"
SITEMAP_INDEX = f"{BASE}/sitemap-index.xml"
UA = "ProfounddBot/1.0 (+https://profoundd.com/bot)"


def submit_indexnow(sample_urls=None):
    """Push sitemap URLs via IndexNow (hits Bing/Yandex/Yep/Seznam at once)."""
    print("\n=== IndexNow (Bing, Yandex, Yep, Seznam, Naver) ===")
    # We need the key from the running app. Fetch it via our own endpoint.
    try:
        resp = requests.get(f"{BASE}/indexnow-key.txt", timeout=10)
        if resp.status_code != 200:
            print(f"  Could not fetch key: {resp.status_code}")
            return False
        key = resp.text.strip()
    except Exception as e:
        print(f"  Error fetching key: {e}")
        return False

    urls = sample_urls or [
        f"{BASE}/",
        f"{BASE}/category/epstein-files",
        f"{BASE}/category/news",
        f"{BASE}/category/medical",
        f"{BASE}/category/legal",
        f"{BASE}/sitemap-index.xml",
        f"{BASE}/sitemap.xml",
        f"{BASE}/sitemap-epstein-0.xml",
    ]

    payload = {
        "host": DOMAIN,
        "key": key,
        "keyLocation": f"{BASE}/indexnow-key.txt",
        "urlList": urls,
    }
    try:
        r = requests.post(
            "https://api.indexnow.org/indexnow",
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15,
        )
        print(f"  Status: {r.status_code}")
        if r.status_code in (200, 202):
            print(f"  OK - submitted {len(urls)} URLs")
            return True
        print(f"  Response: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  Error: {e}")
        return False


def submit_mojeek():
    """Submit sitemap to Mojeek."""
    print("\n=== Mojeek ===")
    print(f"  Submit form at: https://www.mojeek.com/submit")
    print(f"  URL to submit: {SITEMAP_INDEX}")
    print(f"  (Mojeek requires human/captcha so this is manual; visit the URL above)")
    return True


def submit_marginalia():
    """Submit to Marginalia Search."""
    print("\n=== Marginalia Search ===")
    print(f"  Submit at: https://search.marginalia.nu/submit")
    print(f"  URL to submit: {BASE}")
    print(f"  (Marginalia focuses on small indie sites — fits perfectly)")
    return True


def submit_brave():
    """Instructions for Brave Search."""
    print("\n=== Brave Search ===")
    print(f"  Brave Search does not yet have a public webmaster submission API.")
    print(f"  They use their own crawler + Bing fallback. Submitting via IndexNow")
    print(f"  above will reach Bing's index which Brave also queries.")
    print(f"  Monitor at: https://search.brave.com/search?q=site:{DOMAIN}")
    return True


def submit_bing_webmaster():
    """Reminder to register Bing Webmaster Tools (for richer integration)."""
    print("\n=== Bing Webmaster Tools (optional, richer features) ===")
    print(f"  Register at: https://www.bing.com/webmasters")
    print(f"  Submit sitemap: {SITEMAP_INDEX}")
    print(f"  (IndexNow above already notifies Bing; this adds analytics + crawl stats)")


def check_sitemap_reachable():
    """Pre-flight: verify sitemap-index.xml is accessible."""
    print(f"\n=== Pre-flight: verify {SITEMAP_INDEX} ===")
    try:
        r = requests.get(SITEMAP_INDEX, headers={"User-Agent": UA}, timeout=15)
        if r.status_code == 200 and "<sitemapindex" in r.text:
            # Count sitemaps listed
            count = r.text.count("<sitemap>")
            print(f"  OK - sitemap-index reachable, lists {count} child sitemaps")
            return True
        print(f"  Unexpected: status {r.status_code}")
        return False
    except Exception as e:
        print(f"  ERROR - could not fetch sitemap: {e}")
        return False


def main():
    print(f"Profoundd sitemap submission tool")
    print(f"Base: {BASE}")

    if not check_sitemap_reachable():
        print("\nAbort: sitemap not reachable. Is the site deployed?")
        sys.exit(1)

    submit_indexnow()
    submit_mojeek()
    submit_marginalia()
    submit_brave()
    submit_bing_webmaster()

    print("\nDone. IndexNow push covers Bing/Yandex/Yep/Seznam automatically.")
    print("Visit the URLs above for manual submissions to Mojeek & Marginalia.")


if __name__ == "__main__":
    main()
