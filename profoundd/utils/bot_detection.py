"""Bot detection, user-agent parsing, referrer classification, and screen size utils."""

import re
from urllib.parse import urlparse, parse_qs

# Compiled bot patterns (case-insensitive).
#
# Structured so the common short patterns come first. The `indexer|webindexer`
# and explicit LLM-bot names below were added 2026-04-24 after discovering
# that meta-webindexer was only matching the regex via the URL embedded in
# its UA string (`/crawler`), which is fragile — same was true of ChatGPT-User
# which matched via `/gptbot` in its embedded URL. HTTP-client UAs (okhttp,
# Java/, Dalvik, Apache-HttpClient, axios) commonly used by non-browser
# scrapers are now explicit too, since previously they were sliding through
# detection and inflating the DailyStats "human" count.
BOT_PATTERNS = re.compile(
    r"bot|crawl|spider|slurp|indexer|webindexer|mediapartners|"
    r"googlebot|bingbot|yandexbot|baidubot|duckduckbot|duckassistbot|"
    r"facebookexternalhit|twitterbot|linkedinbot|applebot|"
    r"whatsapp|telegram|discord|slackbot|"
    # LLM / AI answer-engine crawlers (some don't contain "bot" substring)
    r"gptbot|chatgpt|claudebot|claude-web|anthropic-ai|perplexity|"
    r"google-extended|googleother|applebot-extended|meta-externalagent|"
    r"amazonbot|youbot|ccbot|searchgpt|copilot|gemini|"
    # SEO scrapers
    r"semrush|ahrefs|mj12bot|petalbot|bytespider|dotbot|rogerbot|"
    r"dataforseo|serpstat|majestic|screaming frog|"
    # HTTP clients / scripting (no-JS, almost always bots)
    r"curl|wget|httpx|python-requests|go-http-client|scrapy|"
    r"okhttp|apache-httpclient|java/|dalvik|node-fetch|axios|"
    # Headless browsers
    r"phantomjs|headlesschrome|lighthouse|puppeteer|playwright|"
    # Monitoring / health checks
    r"pingdom|uptimerobot|statuscake|monitor|healthcheck|"
    # Generic action-ish keywords often in non-browser fetchers
    r"preview|embed|fetch|archive",
    re.IGNORECASE,
)


def detect_bot(user_agent: str | None) -> bool:
    """Return True if the user agent looks like a bot."""
    if not user_agent or len(user_agent) < 10:
        return True
    return bool(BOT_PATTERNS.search(user_agent))


def parse_user_agent(ua: str | None) -> dict:
    """Parse user agent into browser, os, and device."""
    if not ua:
        return {"browser": "Unknown", "os": "Unknown", "device": "Unknown"}

    # Browser detection (order matters)
    if re.search(r"edg", ua, re.I):
        browser = "Edge"
    elif re.search(r"opr|opera", ua, re.I):
        browser = "Opera"
    elif re.search(r"chrome", ua, re.I) and not re.search(r"chromium", ua, re.I):
        browser = "Chrome"
    elif re.search(r"safari", ua, re.I) and not re.search(r"chrome", ua, re.I):
        browser = "Safari"
    elif re.search(r"firefox", ua, re.I):
        browser = "Firefox"
    elif re.search(r"msie|trident", ua, re.I):
        browser = "IE"
    else:
        browser = "Other"

    # OS detection
    if re.search(r"windows", ua, re.I):
        os_name = "Windows"
    elif re.search(r"macintosh|mac os", ua, re.I):
        os_name = "macOS"
    elif re.search(r"iphone|ipad|ipod", ua, re.I):
        os_name = "iOS"
    elif re.search(r"android", ua, re.I):
        os_name = "Android"
    elif re.search(r"linux", ua, re.I):
        os_name = "Linux"
    elif re.search(r"cros", ua, re.I):
        os_name = "ChromeOS"
    else:
        os_name = "Other"

    # Device detection
    if re.search(r"mobile|iphone|android.*mobile", ua, re.I):
        device = "Mobile"
    elif re.search(r"ipad|tablet|android(?!.*mobile)", ua, re.I):
        device = "Tablet"
    else:
        device = "Desktop"

    return {"browser": browser, "os": os_name, "device": device}


# Search engine patterns: (hostname_contains, display_name)
SEARCH_ENGINES = [
    ("google.", "Google"),
    ("bing.com", "Bing"),
    ("duckduckgo.com", "DuckDuckGo"),
    ("yahoo.", "Yahoo"),
    ("baidu.com", "Baidu"),
    ("yandex.", "Yandex"),
    ("ecosia.org", "Ecosia"),
    ("search.brave.com", "Brave"),
]

SOCIAL_PATTERNS = [
    "facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com",
    "reddit.com", "linkedin.com", "youtube.com", "t.co",
]

EMAIL_PATTERNS = ["mail", "outlook", "proton"]


def classify_referrer(referrer: str | None, site_domain: str = "profoundd.com") -> dict:
    """Classify referrer into traffic source category.

    Returns: {"source": str, "search_engine": str|None, "keyword": str|None}
    """
    if not referrer:
        return {"source": "Direct", "search_engine": None, "keyword": None}

    try:
        parsed = urlparse(referrer)
        host = (parsed.hostname or "").lower()
    except Exception:
        return {"source": "Direct", "search_engine": None, "keyword": None}

    if not host or site_domain in host:
        return {"source": "Direct", "search_engine": None, "keyword": None}

    # Search engines
    for pattern, name in SEARCH_ENGINES:
        if pattern in host:
            keyword = extract_search_keyword(referrer)
            return {"source": "Search", "search_engine": name, "keyword": keyword}

    # Social media
    for pattern in SOCIAL_PATTERNS:
        if pattern in host:
            return {"source": "Social", "search_engine": None, "keyword": None}

    # Email
    for pattern in EMAIL_PATTERNS:
        if pattern in host:
            return {"source": "Email", "search_engine": None, "keyword": None}

    return {"source": "Referral", "search_engine": None, "keyword": None}


def extract_search_keyword(referrer: str | None) -> str | None:
    """Extract search keyword from referrer URL query params."""
    if not referrer:
        return None
    try:
        parsed = urlparse(referrer)
        params = parse_qs(parsed.query)
        for key in ("q", "query", "p"):
            if key in params and params[key]:
                return params[key][0][:200]
    except Exception:
        pass
    return None


def classify_screen(width: int | None) -> str:
    """Classify screen width into a category."""
    if not width:
        return "Unknown"
    if width <= 480:
        return "Small Phone"
    if width <= 768:
        return "Phone/Tablet"
    if width <= 1024:
        return "Tablet/Laptop"
    if width <= 1440:
        return "Desktop"
    return "Large Desktop"
