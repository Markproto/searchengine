"""
External search providers for enhanced results.
Queries CourtListener (legal), PubMed/arXiv (medical/science), and
Congress.gov (legislative) APIs in real-time when the user's search
matches those domains.
"""
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# Timeout for all external API calls (seconds)
API_TIMEOUT = 5

# Categories that trigger each provider
LEGAL_CATEGORIES = {"legal"}
SCIENCE_CATEGORIES = {"medical", "science"}
LEGISLATIVE_CATEGORIES = {"legislative"}
POLYMARKET_CATEGORIES = {"polymarket"}

# Keywords that trigger providers when category is "all"
POLYMARKET_KEYWORDS = [
    "polymarket", "prediction market", "betting odds", "market odds",
    "forecast odds", "what are the odds", "probability of", "chances of",
    "election odds", "prediction contract",
]
LEGAL_KEYWORDS = [
    "court", "lawsuit", "ruling", "judge", "plaintiff", "defendant",
    "amendment", "constitutional", "supreme court", "appeal", "verdict",
    "statute", "litigation", "indictment", "prosecution", "habeas",
    "injunction", "subpoena", "testimony", "case law",
]
SCIENCE_KEYWORDS = [
    "study", "clinical trial", "peer review", "pubmed", "research paper",
    "journal", "placebo", "randomized", "efficacy", "vaccine", "mrna",
    "arxiv", "preprint", "meta-analysis", "double-blind", "cohort",
    "epidemiology", "pathogen", "therapeutic", "biomarker",
]
LEGISLATIVE_KEYWORDS = [
    "bill", "h.r.", "s.", "senate bill", "house bill", "farm bill",
    "act", "congress", "committee", "legislation", "legislative",
    "appropriation", "authorization", "executive order", "regulation",
    "federal register", "subcommittee", "hearing", "markup",
    "filibuster", "cloture", "reconciliation", "omnibus",
    "continuing resolution", "debt ceiling", "government shutdown",
    "preemption", "pesticide", "epa", "usda", "fifra",
    "signed into law", "passed the house", "passed the senate",
    "sent to president", "veto", "enrolled",
]


def should_enhance(query, category):
    """
    Determine which providers to activate.
    Returns a set of provider names: {'courtlistener', 'pubmed', 'congress'}
    """
    providers = set()
    q_lower = (query or "").lower()

    if category in LEGAL_CATEGORIES:
        providers.add("courtlistener")
    elif category in SCIENCE_CATEGORIES:
        providers.add("pubmed")
    elif category in LEGISLATIVE_CATEGORIES:
        providers.add("congress")
    elif category in POLYMARKET_CATEGORIES:
        providers.add("polymarket")
    elif category == "all" or not category:
        # Auto-detect from query keywords
        if any(kw in q_lower for kw in LEGAL_KEYWORDS):
            providers.add("courtlistener")
        if any(kw in q_lower for kw in SCIENCE_KEYWORDS):
            providers.add("pubmed")
        if any(kw in q_lower for kw in LEGISLATIVE_KEYWORDS):
            providers.add("congress")
        if any(kw in q_lower for kw in POLYMARKET_KEYWORDS):
            providers.add("polymarket")

    return providers


def fetch_courtlistener(query, max_results=5):
    """
    Query CourtListener's free API for court opinions.
    Returns list of article-like dicts compatible with our search results.
    """
    try:
        resp = requests.get(
            "https://www.courtlistener.com/api/rest/v4/search/",
            params={
                "q": query,
                "type": "o",  # opinions
                "order_by": "score desc",
                "page_size": max_results,
            },
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for result in data.get("results", [])[:max_results]:
            case_name = result.get("caseName") or result.get("case_name", "")
            court = result.get("court", "")
            date_filed = result.get("dateFiled") or result.get("date_filed", "")
            snippet = result.get("snippet", "").replace("<em>", "<mark>").replace("</em>", "</mark>")
            absolute_url = result.get("absolute_url", "")

            if absolute_url and not absolute_url.startswith("http"):
                url = f"https://www.courtlistener.com{absolute_url}"
            else:
                url = absolute_url or ""

            articles.append({
                "title": case_name,
                "summary": snippet,
                "content": "",
                "source_name": f"CourtListener ({court})" if court else "CourtListener",
                "source_credibility": 9,
                "category": "legal",
                "url": url,
                "published_at": date_filed,
                "tags": ["court-opinion", "enhanced"],
                "_enhanced": True,
                "_provider": "courtlistener",
                "_score": 0,
                "_highlights": {"summary": [snippet]} if snippet else {},
            })

        logger.info("CourtListener returned %d results for '%s'", len(articles), query)
        return articles

    except requests.Timeout:
        logger.warning("CourtListener API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("CourtListener API error: %s", e)
        return []


def fetch_pubmed(query, max_results=5):
    """
    Query PubMed E-utilities for research papers.
    Two-step: esearch to get IDs, then esummary for details.
    """
    try:
        # Step 1: Search for paper IDs
        search_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "relevance",
            },
            timeout=API_TIMEOUT,
        )
        search_resp.raise_for_status()
        search_data = search_resp.json()

        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        # Step 2: Get paper details
        summary_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(id_list),
                "retmode": "json",
            },
            timeout=API_TIMEOUT,
        )
        summary_resp.raise_for_status()
        summary_data = summary_resp.json()

        articles = []
        results = summary_data.get("result", {})
        for pmid in id_list:
            paper = results.get(pmid, {})
            if not paper or "error" in paper:
                continue

            title = paper.get("title", "")
            authors = paper.get("authors", [])
            author_str = ", ".join(a.get("name", "") for a in authors[:3])
            if len(authors) > 3:
                author_str += " et al."
            journal = paper.get("fulljournalname") or paper.get("source", "")
            pub_date = paper.get("pubdate", "")
            # Normalize date to ISO format
            date_iso = _normalize_pubmed_date(pub_date)

            summary = f"{author_str}. {journal} ({pub_date})." if author_str else f"{journal} ({pub_date})."

            articles.append({
                "title": title,
                "summary": summary,
                "content": "",
                "source_name": f"PubMed ({journal})" if journal else "PubMed",
                "source_credibility": 7,
                "category": "medical",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                "published_at": date_iso,
                "tags": ["research-paper", "enhanced"],
                "_enhanced": True,
                "_provider": "pubmed",
                "_score": 0,
                "_highlights": {},
            })

        logger.info("PubMed returned %d results for '%s'", len(articles), query)
        return articles

    except requests.Timeout:
        logger.warning("PubMed API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("PubMed API error: %s", e)
        return []


def fetch_congress_gov(query, api_key=None, max_results=5):
    """
    Query Congress.gov API v3 for bills, resolutions, and amendments.
    Free API key required (5000 req/hour).
    Returns list of article-like dicts compatible with our search results.
    """
    if not api_key:
        logger.debug("Congress.gov API key not configured, skipping")
        return []

    try:
        resp = requests.get(
            "https://api.congress.gov/v3/bill",
            params={
                "query": query,
                "limit": max_results,
                "sort": "updateDate+desc",
                "api_key": api_key,
            },
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 2,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for bill in data.get("bills", [])[:max_results]:
            bill_type = bill.get("type", "")
            bill_number = bill.get("number", "")
            congress = bill.get("congress", "")
            title = bill.get("title", "")
            latest_action = bill.get("latestAction", {})
            action_text = latest_action.get("text", "")
            action_date = latest_action.get("actionDate", "")

            # Build a readable label like "H.R. 1234 (119th Congress)"
            bill_label = f"{bill_type}.{bill_number}" if bill_type and bill_number else ""
            congress_label = f"{congress}th Congress" if congress else ""

            summary = action_text
            if bill_label and congress_label:
                summary = f"{bill_label} ({congress_label}). Latest action: {action_text}"

            # Congress.gov URL
            url = bill.get("url", "")
            # The API returns an API URL; build the public-facing URL instead
            if bill_type and bill_number and congress:
                type_slug = bill_type.lower().replace(".", "")
                public_url = f"https://www.congress.gov/bill/{congress}th-congress/{_bill_type_path(bill_type)}/{bill_number}"
            else:
                public_url = url

            articles.append({
                "title": title,
                "summary": summary,
                "content": "",
                "source_name": f"Congress.gov ({bill_label})" if bill_label else "Congress.gov",
                "source_credibility": 9,
                "category": "legislative",
                "url": public_url,
                "published_at": action_date,
                "tags": ["legislation", "enhanced", "primary-source"],
                "_enhanced": True,
                "_provider": "congress",
                "_score": 0,
                "_highlights": {},
                "_bill_type": bill_type,
                "_bill_number": bill_number,
                "_congress": str(congress),
            })

        logger.info("Congress.gov returned %d results for '%s'", len(articles), query)
        return articles

    except requests.Timeout:
        logger.warning("Congress.gov API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("Congress.gov API error: %s", e)
        return []


def fetch_federal_register(query, max_results=5):
    """
    Query the Federal Register API for regulations and rules.
    No API key required.
    """
    try:
        resp = requests.get(
            "https://www.federalregister.gov/api/v1/documents.json",
            params={
                "conditions[term]": query,
                "per_page": max_results,
                "order": "relevance",
                "fields[]": ["title", "abstract", "html_url", "publication_date",
                             "agencies", "type", "document_number"],
            },
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for doc in data.get("results", [])[:max_results]:
            title = doc.get("title", "")
            abstract = doc.get("abstract", "") or ""
            url = doc.get("html_url", "")
            pub_date = doc.get("publication_date", "")
            doc_type = doc.get("type", "Document")
            agencies = doc.get("agencies", [])
            agency_names = ", ".join(a.get("name", "") for a in agencies if a.get("name"))

            source_label = f"Federal Register ({agency_names})" if agency_names else "Federal Register"

            articles.append({
                "title": title,
                "summary": abstract[:500] if abstract else f"{doc_type} published {pub_date}",
                "content": "",
                "source_name": source_label,
                "source_credibility": 9,
                "category": "legislative",
                "url": url,
                "published_at": pub_date,
                "tags": ["regulation", "enhanced", "primary-source"],
                "_enhanced": True,
                "_provider": "congress",
                "_score": 0,
                "_highlights": {},
            })

        logger.info("Federal Register returned %d results for '%s'", len(articles), query)
        return articles

    except requests.Timeout:
        logger.warning("Federal Register API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("Federal Register API error: %s", e)
        return []


def fetch_bill_text(congress, bill_type, bill_number, api_key, max_chars=15000):
    """
    Fetch the actual text of a bill from Congress.gov.
    Two-step: (1) get text version URLs from API, (2) fetch the HTML/XML content.
    Returns the bill text as plain text, or empty string on failure.
    """
    if not api_key:
        return ""

    type_slug = bill_type.lower().replace(".", "")
    try:
        # Step 1: Get text versions from the API
        resp = requests.get(
            f"https://api.congress.gov/v3/bill/{congress}/{type_slug}/{bill_number}/text",
            params={"api_key": api_key, "format": "json"},
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,
        )
        resp.raise_for_status()
        data = resp.json()

        text_versions = data.get("textVersions", [])
        if not text_versions:
            logger.info("No text versions available for %s %s (%sth Congress)", bill_type, bill_number, congress)
            return ""

        # Use the latest text version (first in list)
        latest = text_versions[0]
        formats = latest.get("formats", [])

        # Prefer HTML, then XML, then any other format
        text_url = ""
        for fmt in formats:
            url = fmt.get("url", "")
            if url.endswith(".htm") or url.endswith(".html"):
                text_url = url
                break
            elif url.endswith(".xml") and not text_url:
                text_url = url

        if not text_url:
            # Fall back to first available format
            text_url = formats[0].get("url", "") if formats else ""

        if not text_url:
            return ""

        logger.info("Fetching bill text from: %s", text_url)

        # Step 2: Fetch the actual text content
        text_resp = requests.get(
            text_url,
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=10,
        )
        text_resp.raise_for_status()

        # Parse HTML/XML to plain text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text_resp.text, "lxml")

        # Remove script/style elements
        for tag in soup(["script", "style", "meta", "link"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Truncate to max_chars to keep evidence manageable
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated — full text available at Congress.gov]"

        logger.info("Fetched %d chars of bill text for %s %s", len(text), bill_type, bill_number)
        return text

    except requests.Timeout:
        logger.warning("Bill text fetch timed out for %s %s", bill_type, bill_number)
        return ""
    except Exception as e:
        logger.warning("Bill text fetch failed for %s %s: %s", bill_type, bill_number, e)
        return ""


def fetch_bill_summary(congress, bill_type, bill_number, api_key):
    """
    Fetch CRS summary of a bill from Congress.gov API.
    Returns summary text, or empty string on failure.
    """
    if not api_key:
        return ""

    type_slug = bill_type.lower().replace(".", "")
    try:
        resp = requests.get(
            f"https://api.congress.gov/v3/bill/{congress}/{type_slug}/{bill_number}/summaries",
            params={"api_key": api_key, "format": "json"},
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,
        )
        resp.raise_for_status()
        data = resp.json()

        summaries = data.get("summaries", [])
        if not summaries:
            return ""

        # Use the most recent summary (last in list = most detailed)
        best = summaries[-1]
        text = best.get("text", "")

        # Strip HTML tags from CRS summaries
        if "<" in text:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "lxml")
            text = soup.get_text(separator="\n", strip=True)

        return text

    except Exception as e:
        logger.warning("Bill summary fetch failed for %s %s: %s", bill_type, bill_number, e)
        return ""


def _bill_type_path(bill_type):
    """Convert API bill type code to Congress.gov URL path segment."""
    mapping = {
        "HR": "house-bill",
        "S": "senate-bill",
        "HJRES": "house-joint-resolution",
        "SJRES": "senate-joint-resolution",
        "HCONRES": "house-concurrent-resolution",
        "SCONRES": "senate-concurrent-resolution",
        "HRES": "house-resolution",
        "SRES": "senate-resolution",
    }
    return mapping.get(bill_type.upper().replace(".", ""), "house-bill")


def fetch_all_enhanced(query, category, max_per_provider=5):
    """
    Fetch results from all applicable external providers IN PARALLEL.
    Returns (articles_list, active_providers_set).
    """
    providers = should_enhance(query, category)
    if not providers:
        return [], set()

    all_articles = []
    active = set()

    # Pre-fetch congress API key if needed (DB access must happen in main thread
    # for Flask app-context safety, so grab it before spawning threads)
    congress_api_key = ""
    if "congress" in providers:
        try:
            from profoundd.utils.models import SiteSetting
            congress_api_key = SiteSetting.get("congress_gov_api_key", "")
        except Exception:
            pass

    # Build list of (provider_name, callable) pairs to run concurrently
    tasks = []
    if "courtlistener" in providers:
        tasks.append(("courtlistener", lambda: fetch_courtlistener(query, max_results=max_per_provider)))
    if "pubmed" in providers:
        tasks.append(("pubmed", lambda: fetch_pubmed(query, max_results=max_per_provider)))
    if "congress" in providers:
        if congress_api_key:
            # Capture api_key in closure explicitly
            _key = congress_api_key
            tasks.append(("congress", lambda: fetch_congress_gov(query, api_key=_key, max_results=max_per_provider)))
        tasks.append(("federal_register", lambda: fetch_federal_register(query, max_results=3)))
    if "polymarket" in providers:
        tasks.append(("polymarket", lambda: fetch_polymarket(query, max_results=max_per_provider)))

    # Fire all providers concurrently
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {executor.submit(fn): name for name, fn in tasks}
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                results = future.result(timeout=API_TIMEOUT + 5)
                if results:
                    all_articles.extend(results)
                    # federal_register rolls up under "congress"
                    active.add("congress" if name == "federal_register" else name)
            except Exception as e:
                logger.warning("Enhanced provider %s failed: %s", name, e)

    return all_articles, active


# Keywords that indicate a product/business/shopping query → use Google
PRODUCT_KEYWORDS = [
    "buy", "price", "for sale", "cheap", "best", "review", "reviews",
    "amazon", "walmart", "store", "shop", "deal", "deals", "discount",
    "coupon", "order", "shipping", "delivery", "watt", "watts", "inch",
    "model", "brand", "product", "compare", "vs", "refurbished", "used",
    "new", "how much", "where to buy", "cost", "affordable",
]


def _is_product_query(query):
    """Detect if a query is about products/shopping vs news/politics/medical."""
    q_lower = query.lower()
    return any(kw in q_lower for kw in PRODUCT_KEYWORDS)


def fetch_searxng(query, searxng_url, max_results=10):
    """
    Query a SearXNG instance for web search results.
    Uses Brave+DDG for political/medical/news queries (less filtered).
    Uses Google for product/business queries (better shopping results).
    """
    if not searxng_url:
        return []

    # Strip trailing slash
    base_url = searxng_url.rstrip("/")

    # Route to different engines based on query type
    if _is_product_query(query):
        engines = "google,duckduckgo"
    else:
        engines = "brave,duckduckgo"

    try:
        resp = requests.get(
            f"{base_url}/search",
            params={
                "q": query,
                "format": "json",
                "categories": "general",
                "engines": engines,
                "language": "en",
                "pageno": 1,
            },
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,  # SearXNG aggregates, give it extra time
        )
        resp.raise_for_status()
        data = resp.json()

        articles = []
        for result in data.get("results", [])[:max_results]:
            title = result.get("title", "")
            url = result.get("url", "")
            snippet = result.get("content", "")
            engine = result.get("engine", "web")
            pub_date = result.get("publishedDate", "")

            # Normalize date if present
            date_str = ""
            if pub_date:
                try:
                    dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                    date_str = dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    date_str = pub_date[:10] if len(pub_date) >= 10 else ""

            # Extract domain as source name
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")

            articles.append({
                "title": title,
                "summary": snippet,
                "content": "",
                "source_name": domain,
                "source_credibility": 5,  # Unknown credibility for web results
                "category": "news",
                "url": url,
                "published_at": date_str,
                "tags": ["web-search"],
                "_enhanced": True,
                "_provider": "searxng",
                "_score": 0,
                "_highlights": {"summary": [snippet]} if snippet else {},
            })

        logger.info("SearXNG returned %d results for '%s'", len(articles), query)
        return articles

    except requests.Timeout:
        logger.warning("SearXNG timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("SearXNG error: %s", e)
        return []


def fetch_searxng_images(query, searxng_url, max_results=20):
    """Fetch image results from SearXNG."""
    if not searxng_url:
        return []
    try:
        resp = requests.get(
            f"{searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "categories": "images", "language": "en", "pageno": 1},
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,
        )
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "img_src": r.get("img_src", r.get("thumbnail_src", "")),
                "thumbnail": r.get("thumbnail_src", r.get("img_src", "")),
                "source_name": r.get("engine", ""),
                "source_url": r.get("source", r.get("url", "")),
                "width": r.get("img_format", "").split("x")[0] if "x" in r.get("img_format", "") else "",
                "height": r.get("img_format", "").split("x")[-1] if "x" in r.get("img_format", "") else "",
            })
        logger.info("SearXNG images returned %d results for '%s'", len(results), query)
        return results
    except Exception as e:
        logger.warning("SearXNG images error: %s", e)
        return []


def fetch_searxng_shopping(query, searxng_url, max_results=20):
    """Fetch shopping/product results from SearXNG with images."""
    if not searxng_url:
        return []
    try:
        # Use Google for shopping (best product results)
        resp = requests.get(
            f"{searxng_url.rstrip('/')}/search",
            params={
                "q": query, "format": "json",
                "categories": "general",
                "engines": "google,duckduckgo",
                "language": "en", "pageno": 1,
            },
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,
        )
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": r.get("content", ""),
                "img_src": r.get("img_src", r.get("thumbnail_src", "")),
                "thumbnail": r.get("thumbnail_src", r.get("img_src", "")),
                "source_name": r.get("engine", ""),
                "price": r.get("price", ""),
            })
        logger.info("SearXNG shopping returned %d results for '%s'", len(results), query)
        return results
    except Exception as e:
        logger.warning("SearXNG shopping error: %s", e)
        return []


def fetch_brave_web(query, max_results=10):
    """
    Query Brave Search directly (no API key required).
    Used as a backup when SearXNG is unavailable.
    Parses Brave's JSON-LD response from their web search.
    """
    try:
        resp = requests.get(
            "https://search.brave.com/api/suggest",
            params={"q": query, "rich": "true"},
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
                "Accept": "application/json",
            },
            timeout=API_TIMEOUT + 2,
        )
        resp.raise_for_status()
        data = resp.json()

        # Brave suggest API returns [query, [suggestions], [urls], [descriptions]]
        articles = []
        if len(data) >= 4:
            titles = data[1] or []
            urls = data[2] or []
            descriptions = data[3] if len(data) > 3 else []

            for i in range(min(len(titles), len(urls), max_results)):
                url = urls[i] if i < len(urls) else ""
                if not url:
                    continue

                from urllib.parse import urlparse
                parsed = urlparse(url)
                domain = parsed.netloc.replace("www.", "")

                articles.append({
                    "title": titles[i] if i < len(titles) else "",
                    "summary": descriptions[i] if i < len(descriptions) else "",
                    "content": "",
                    "source_name": domain,
                    "source_credibility": 5,
                    "category": "news",
                    "url": url,
                    "published_at": "",
                    "tags": ["web-search"],
                    "_enhanced": True,
                    "_provider": "searxng",  # Same provider tag — same display treatment
                    "_score": 0,
                    "_highlights": {},
                })

        logger.info("Brave web returned %d results for '%s'", len(articles), query)
        return articles

    except Exception as e:
        logger.warning("Brave web search error: %s", e)
        return []


def boost_known_domains(articles):
    """
    Re-rank web results to prioritize domains from Profoundd's source list.
    Known domains get boosted to the top, maintaining their relative order.
    """
    from profoundd.config.sources import ALL_SOURCES
    from urllib.parse import urlparse

    # Build set of known domains from source URLs
    known_domains = set()
    for src in ALL_SOURCES:
        try:
            parsed = urlparse(src["url"])
            domain = parsed.netloc.replace("www.", "").replace("feeds.", "").replace("rss.", "")
            # Also extract the base domain (e.g. "zerohedge.com" from "feeds.feedburner.com")
            if "feedburner" not in domain and "megaphone" not in domain and "libsyn" not in domain:
                known_domains.add(domain)
            # Use source name to match domains loosely
            name_slug = src["name"].lower().replace(" ", "").replace("-", "")
            known_domains.add(name_slug)
        except Exception:
            continue

    # Also add common domain forms from source names
    for src in ALL_SOURCES:
        name = src["name"].lower()
        for variant in [
            name.replace(" ", "").replace("(", "").replace(")", ""),
            name.split("(")[0].strip().replace(" ", ""),
        ]:
            known_domains.add(variant)

    # Split articles into known and unknown
    known = []
    unknown = []
    for article in articles:
        domain = article.get("source_name", "").lower()
        domain_base = domain.split(".")[0] if "." in domain else domain
        if domain in known_domains or domain_base in known_domains:
            known.append(article)
        else:
            unknown.append(article)

    return known + unknown


def _normalize_pubmed_date(date_str):
    """Convert PubMed date format (e.g., '2024 Jan 15') to ISO format."""
    if not date_str:
        return ""
    try:
        # Try common PubMed formats
        for fmt in ("%Y %b %d", "%Y %b", "%Y"):
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return date_str[:10]
    except Exception:
        return ""


# Polymarket Gamma API — subcategory → tag ID mapping
POLYMARKET_TAG_MAP = {
    "elections": 2,          # Politics
    "sports": 100639,
    "global-conflicts": 100265,  # Geopolitics
    "crypto": 21,
    "finance": 120,
    "tech": 1401,
    "culture": 596,
}

# Reverse map: tag ID → subcategory slug
_TAG_ID_TO_SUBCATEGORY = {v: k for k, v in POLYMARKET_TAG_MAP.items()}


def _save_polymarket_snapshots(events_data):
    """
    Save Polymarket event data to SQLite for future AI sentiment analysis.
    Runs in a best-effort manner — failures are logged but don't break the fetch.
    """
    import json as _json
    try:
        from profoundd.utils.models import db, PolymarketSnapshot
        from flask import current_app
        if not current_app:
            return
        for ev in events_data:
            snapshot = PolymarketSnapshot(
                event_id=str(ev.get("event_id", "")),
                event_slug=ev.get("event_slug", ""),
                title=ev.get("title", ""),
                description=(ev.get("description", "") or "")[:2000],
                category=ev.get("subcategory", ""),
                outcomes_json=_json.dumps(ev.get("outcomes", [])),
                volume_total=ev.get("volume_total", 0),
                volume_24hr=ev.get("volume_24hr", 0),
                liquidity=ev.get("liquidity", 0),
                end_date=ev.get("end_date", ""),
                image_url=ev.get("image_url", ""),
                market_slugs_json=_json.dumps(ev.get("market_slugs", [])),
                tags_json=_json.dumps(ev.get("tag_labels", [])),
            )
            db.session.add(snapshot)
        db.session.commit()
        logger.info("Saved %d Polymarket snapshots to DB", len(events_data))
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.debug("Polymarket snapshot save skipped: %s", e)


def fetch_polymarket(query="", subcategory=None, max_results=20):
    """
    Fetch prediction market events from Polymarket's Gamma API.
    - If subcategory is given, filter by Polymarket tag ID.
    - If query is given, fetch a large batch and filter client-side.
    - Caches snapshots to SQLite for AI sentiment analysis.
    - Returns article-like dicts with embed slugs and live odds.
    """
    import json as _json

    try:
        params = {
            "active": "true",
            "closed": "false",
            "limit": 100 if query else max_results,
            "order": "volume24hr",
            "ascending": "false",
        }
        if subcategory and subcategory in POLYMARKET_TAG_MAP:
            params["tag_id"] = POLYMARKET_TAG_MAP[subcategory]

        resp = requests.get(
            "https://gamma-api.polymarket.com/events",
            params=params,
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 2,
        )
        resp.raise_for_status()
        events = resp.json()

        if not isinstance(events, list):
            logger.warning("Polymarket returned unexpected format: %s", type(events))
            return []

        # Client-side text search when query is provided
        if query:
            q_lower = query.lower()
            q_words = q_lower.split()
            filtered = []
            for event in events:
                text = (
                    event.get("title", "") + " " +
                    event.get("description", "") + " " +
                    " ".join(m.get("question", "") for m in event.get("markets", []))
                ).lower()
                if any(w in text for w in q_words):
                    filtered.append(event)
            events = filtered[:max_results]
        else:
            events = events[:max_results]

        articles = []
        snapshot_data = []  # for DB caching
        for event in events:
            title = event.get("title", "")
            slug = event.get("slug", "")
            event_id = event.get("id", "")
            description = event.get("description", "")
            image = event.get("image", "")
            volume = event.get("volume", 0)
            volume_24hr = event.get("volume24hr", 0)
            liquidity = event.get("liquidity", 0)
            end_date = event.get("endDate", "")

            # Detect subcategory from tags (API returns tag IDs as strings)
            event_subcategory = ""
            tag_labels = []
            for tag in event.get("tags", []):
                tag_labels.append(tag.get("label", ""))
                try:
                    tag_id = int(tag.get("id", 0))
                except (ValueError, TypeError):
                    continue
                if tag_id in _TAG_ID_TO_SUBCATEGORY and not event_subcategory:
                    event_subcategory = _TAG_ID_TO_SUBCATEGORY[tag_id]

            # Build odds summary and collect market slugs for embeds
            markets = event.get("markets", [])
            odds_parts = []
            polymarket_outcomes = []
            market_slugs = []  # for embed iframes

            # Sort active markets by highest "Yes" price (leading outcomes first)
            active_markets = [m for m in markets if not m.get("closed")]
            def _market_yes_price(m):
                try:
                    prices = _json.loads(m.get("outcomePrices", "[]"))
                    return float(prices[0]) if prices else 0
                except Exception:
                    return 0
            active_markets.sort(key=_market_yes_price, reverse=True)

            for market in active_markets:

                market_slug = market.get("slug", "")
                if market_slug:
                    market_slugs.append(market_slug)

                question = market.get("question", "")
                outcomes_raw = market.get("outcomes", "[]")
                prices_raw = market.get("outcomePrices")

                try:
                    outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                    prices = _json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                except (TypeError, _json.JSONDecodeError):
                    continue

                if not prices or not outcomes:
                    continue

                # Format outcome odds
                outcome_strs = []
                for i, (outcome, price) in enumerate(zip(outcomes, prices)):
                    try:
                        pct = float(price) * 100
                    except (ValueError, TypeError):
                        continue
                    outcome_strs.append(f"{outcome}: {pct:.0f}%")
                    polymarket_outcomes.append({
                        "label": outcome,
                        "pct": round(pct, 1),
                        "question": question,
                    })

                if outcome_strs:
                    label = question if question != title else ""
                    odds_str = " / ".join(outcome_strs)
                    if label:
                        odds_parts.append(f"{label} — {odds_str}")
                    else:
                        odds_parts.append(odds_str)

                # Show at most 3 sub-markets in the summary
                if len(odds_parts) >= 3:
                    break

            # Format volume
            try:
                vol_num = float(volume)
                if vol_num >= 1_000_000:
                    vol_str = f"${vol_num / 1_000_000:.1f}M"
                elif vol_num >= 1_000:
                    vol_str = f"${vol_num / 1_000:.0f}K"
                else:
                    vol_str = f"${vol_num:.0f}"
            except (ValueError, TypeError):
                vol_str = ""

            summary = " | ".join(odds_parts) if odds_parts else description[:300]
            if vol_str:
                summary += f" — {vol_str} volume"

            # Normalize end date
            date_str = ""
            if end_date:
                try:
                    date_str = end_date[:10]
                except Exception:
                    pass

            url = f"https://polymarket.com/event/{slug}" if slug else ""

            # Pick the best market slug for the primary embed (highest volume active market)
            primary_embed_slug = market_slugs[0] if market_slugs else ""

            articles.append({
                "title": title,
                "summary": summary,
                "content": description[:500] if description else "",
                "source_name": "Polymarket",
                "source_credibility": 8,
                "category": "polymarket",
                "subcategory": event_subcategory,
                "url": url,
                "image_url": image,
                "published_at": date_str,
                "tags": ["prediction-market", "enhanced"],
                "_state": _detect_state(title),
                "_enhanced": True,
                "_provider": "polymarket",
                "_score": 0,
                "_highlights": {},
                "_polymarket_outcomes": polymarket_outcomes[:6],
                "_polymarket_volume": vol_str,
                "_polymarket_embed_slug": primary_embed_slug,
                "_polymarket_market_slugs": market_slugs[:5],
            })

            # Collect data for DB snapshot
            snapshot_data.append({
                "event_id": str(event_id),
                "event_slug": slug,
                "title": title,
                "description": description,
                "subcategory": event_subcategory,
                "outcomes": polymarket_outcomes[:10],
                "volume_total": float(volume or 0),
                "volume_24hr": float(volume_24hr or 0),
                "liquidity": float(liquidity or 0),
                "end_date": date_str,
                "image_url": image,
                "market_slugs": market_slugs[:5],
                "tag_labels": tag_labels,
            })

        logger.info("Polymarket returned %d events for query='%s' sub='%s'",
                     len(articles), query, subcategory)

        # Cache snapshots to DB (best-effort, non-blocking)
        if snapshot_data:
            _save_polymarket_snapshots(snapshot_data)

        return articles

    except requests.Timeout:
        logger.warning("Polymarket API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("Polymarket API error: %s", e)
        return []


# --- State name detection for prediction markets ---
STATE_NAME_TO_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
ABBREV_TO_STATE_NAME = {v: k.title() for k, v in STATE_NAME_TO_ABBREV.items()}


def _detect_state(text):
    """Detect US state from text, return abbreviation or empty string."""
    text_lower = text.lower()
    # Check longer names first (e.g., "new hampshire" before "new")
    for name in sorted(STATE_NAME_TO_ABBREV.keys(), key=len, reverse=True):
        if name in text_lower:
            return STATE_NAME_TO_ABBREV[name]
    return ""


# --- Manifold Markets provider ---

# Manifold topic slugs for subcategory filtering
MANIFOLD_TOPIC_MAP = {
    "elections": "us-politics",
    "global-conflicts": "geopolitics",
    "sports": "sports",
    "crypto": "crypto-speculation",
    "finance": "economics",
    "tech": "technology",
    "culture": "entertainment",
}


def fetch_manifold(query="", subcategory=None, max_results=20):
    """
    Fetch prediction markets from Manifold Markets API.
    Public, no auth, 1000 req/min. Returns article-like dicts.
    """
    try:
        params = {
            "term": query or "",
            "limit": max_results,
            "sort": "liquidity",
            "filter": "open",
        }

        resp = requests.get(
            "https://api.manifold.markets/v0/search-markets",
            params=params,
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 2,
        )
        resp.raise_for_status()
        markets = resp.json()

        if not isinstance(markets, list):
            return []

        articles = []
        snapshot_data = []
        for market in markets:
            outcome_type = market.get("outcomeType", "")
            question = market.get("question", "")
            prob = market.get("probability")
            url = market.get("url", "")
            slug = market.get("slug", "")
            volume = market.get("volume", 0)
            liquidity = market.get("totalLiquidity", 0)
            bettors = market.get("uniqueBettorCount", 0)

            # Parse timestamps (epoch ms)
            close_time = market.get("closeTime")
            date_str = ""
            if close_time:
                try:
                    from datetime import datetime as _dt
                    date_str = _dt.fromtimestamp(close_time / 1000).strftime("%Y-%m-%d")
                except Exception:
                    pass

            # Build outcomes for display
            polymarket_outcomes = []
            if outcome_type == "BINARY" and prob is not None:
                yes_pct = round(prob * 100, 1)
                no_pct = round((1 - prob) * 100, 1)
                polymarket_outcomes = [
                    {"label": "Yes", "pct": yes_pct, "question": question},
                    {"label": "No", "pct": no_pct, "question": question},
                ]

            # Format volume
            try:
                vol_num = float(volume)
                if vol_num >= 1_000_000:
                    vol_str = f"{vol_num / 1_000_000:.1f}M mana"
                elif vol_num >= 1_000:
                    vol_str = f"{vol_num / 1_000:.0f}K mana"
                else:
                    vol_str = f"{vol_num:.0f} mana"
            except (ValueError, TypeError):
                vol_str = ""

            # Build summary
            if polymarket_outcomes:
                summary = f"Yes: {polymarket_outcomes[0]['pct']:.0f}% / No: {polymarket_outcomes[1]['pct']:.0f}%"
            else:
                summary = market.get("textDescription", "")[:300] if market.get("textDescription") else ""
            if vol_str:
                summary += f" — {vol_str}"
            if bettors:
                summary += f" ({bettors} traders)"

            articles.append({
                "title": question,
                "summary": summary,
                "content": market.get("textDescription", "")[:500] if market.get("textDescription") else "",
                "source_name": "Manifold",
                "source_credibility": 6,
                "category": "polymarket",
                "subcategory": "",
                "url": url,
                "image_url": "",
                "published_at": date_str,
                "tags": ["prediction-market", "enhanced"],
                "_state": _detect_state(question),
                "_enhanced": True,
                "_provider": "manifold",
                "_score": 0,
                "_highlights": {},
                "_polymarket_outcomes": polymarket_outcomes,
                "_polymarket_volume": vol_str,
                "_polymarket_embed_slug": "",
                "_polymarket_market_slugs": [],
            })

            snapshot_data.append({
                "event_id": market.get("id", ""),
                "event_slug": slug,
                "title": question,
                "description": market.get("textDescription", "")[:2000] if market.get("textDescription") else "",
                "subcategory": "",
                "outcomes": polymarket_outcomes,
                "volume_total": float(volume or 0),
                "volume_24hr": float(market.get("volume24Hours", 0) or 0),
                "liquidity": float(liquidity or 0),
                "end_date": date_str,
                "image_url": "",
                "market_slugs": [],
                "tag_labels": ["manifold"],
            })

        logger.info("Manifold returned %d markets for query='%s'", len(articles), query)

        if snapshot_data:
            _save_polymarket_snapshots(snapshot_data)

        return articles

    except requests.Timeout:
        logger.warning("Manifold API timed out for '%s'", query)
        return []
    except Exception as e:
        logger.warning("Manifold API error: %s", e)
        return []


# --- PredictIt provider ---

def fetch_manifold_by_state(state_abbrev, max_results=10):
    """Fetch Manifold markets related to a specific US state."""
    state_name = ABBREV_TO_STATE_NAME.get(state_abbrev, "")
    if not state_name:
        return []
    return fetch_manifold(query=f"{state_name} 2026", max_results=max_results)


def fetch_predictit_by_state(state_abbrev, max_results=20):
    """Fetch PredictIt markets for a specific state (filters from full list)."""
    all_markets = fetch_predictit(query="", max_results=251)
    return [m for m in all_markets if m.get("_state") == state_abbrev][:max_results]


def fetch_predictit(query="", max_results=20):
    """
    Fetch prediction markets from PredictIt.
    Single endpoint returns all markets, filter client-side.
    US politics focus. No auth required.
    """
    try:
        resp = requests.get(
            "https://www.predictit.org/api/marketdata/all/",
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT + 3,
        )
        resp.raise_for_status()
        data = resp.json()

        all_markets = data.get("markets", [])

        # Client-side text filter
        if query:
            q_lower = query.lower()
            q_words = q_lower.split()
            filtered = []
            for m in all_markets:
                text = (m.get("name", "") + " " +
                        " ".join(c.get("name", "") for c in m.get("contracts", []))).lower()
                if any(w in text for w in q_words):
                    filtered.append(m)
            all_markets = filtered

        # Sort by total volume (sum of contract trades) — approximate by number of contracts
        # PredictIt doesn't give volume, so sort by number of contracts (more = more active)
        all_markets.sort(key=lambda m: len(m.get("contracts", [])), reverse=True)
        all_markets = all_markets[:max_results]

        articles = []
        snapshot_data = []
        for market in all_markets:
            name = market.get("name", "")
            market_url = market.get("url", "")
            image = market.get("image", "")
            contracts = market.get("contracts", [])

            # Sort contracts by lastTradePrice descending (leading outcomes first)
            contracts.sort(key=lambda c: float(c.get("lastTradePrice") or 0), reverse=True)

            # Build outcomes from top contracts
            polymarket_outcomes = []
            for contract in contracts[:6]:
                c_name = contract.get("name", "")
                price = contract.get("lastTradePrice")
                if price is not None:
                    pct = round(float(price) * 100, 1)
                    polymarket_outcomes.append({
                        "label": c_name,
                        "pct": pct,
                        "question": name,
                    })

            # Summary
            top_parts = []
            for o in polymarket_outcomes[:3]:
                top_parts.append(f"{o['label']}: {o['pct']:.0f}%")
            summary = " / ".join(top_parts) if top_parts else ""
            summary += f" ({len(contracts)} contracts)"

            # End date from first contract
            end_date = ""
            if contracts:
                end_date = (contracts[0].get("dateEnd") or "")[:10]

            detected_state = _detect_state(name)

            articles.append({
                "title": name,
                "summary": summary,
                "content": "",
                "source_name": "PredictIt",
                "source_credibility": 7,
                "category": "polymarket",
                "subcategory": "elections",
                "_state": detected_state,
                "url": market_url,
                "image_url": image,
                "published_at": end_date,
                "tags": ["prediction-market", "enhanced"],
                "_enhanced": True,
                "_provider": "predictit",
                "_score": 0,
                "_highlights": {},
                "_polymarket_outcomes": polymarket_outcomes[:6],
                "_polymarket_volume": "",
                "_polymarket_embed_slug": "",
                "_polymarket_market_slugs": [],
            })

            snapshot_data.append({
                "event_id": str(market.get("id", "")),
                "event_slug": "",
                "title": name,
                "description": "",
                "subcategory": "elections",
                "outcomes": polymarket_outcomes[:10],
                "volume_total": 0,
                "volume_24hr": 0,
                "liquidity": 0,
                "end_date": end_date,
                "image_url": image,
                "market_slugs": [],
                "tag_labels": ["predictit", "us-politics"],
            })

        logger.info("PredictIt returned %d markets for query='%s'", len(articles), query)

        if snapshot_data:
            _save_polymarket_snapshots(snapshot_data)

        return articles

    except requests.Timeout:
        logger.warning("PredictIt API timed out")
        return []
    except Exception as e:
        logger.warning("PredictIt API error: %s", e)
        return []


def fetch_grokipedia(query, max_results=3):
    """
    Fetch search results from Grokipedia (grokipedia.com).
    Returns articles formatted for Profoundd's result list.
    """
    try:
        resp = requests.get(
            "https://grokipedia.com/search",
            params={"q": query},
            headers={"User-Agent": "Profoundd/1.0 (search engine)"},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        articles = []
        # Grokipedia results are <a> tags with data-search-result-link attribute
        for link in soup.select("a[data-search-result-link]"):
            slug = link.get("data-slug", "")
            if not slug:
                continue

            title_el = link.find("span", class_=lambda c: c and "font-medium" in c)
            snippet = link.get("data-search-snippet", "")
            title = title_el.get_text(strip=True) if title_el else slug.replace("_", " ")
            url = f"https://grokipedia.com/page/{slug}"

            # Fetch full article page for a longer excerpt (~100+ words)
            long_summary = snippet
            try:
                page_resp = requests.get(
                    url,
                    headers={"User-Agent": "Profoundd/1.0 (search engine)"},
                    timeout=5,
                )
                if page_resp.ok:
                    page_soup = BeautifulSoup(page_resp.text, "html.parser")
                    # Try article/main content area, fall back to all paragraphs
                    content_area = (
                        page_soup.find("article")
                        or page_soup.find("main")
                        or page_soup.find("div", class_=lambda c: c and "content" in c.lower())
                    )
                    if content_area:
                        paragraphs = content_area.find_all("p")
                    else:
                        paragraphs = page_soup.find_all("p")
                    # Combine paragraphs until we hit ~100 words
                    words = []
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if not text or len(text) < 20:
                            continue
                        words.extend(text.split())
                        if len(words) >= 120:
                            break
                    if len(words) > 20:
                        long_summary = " ".join(words[:150])
                        if len(words) > 150:
                            long_summary += "..."
            except Exception:
                pass  # Fall back to search snippet

            articles.append({
                "title": title,
                "summary": long_summary,
                "content": "",
                "source_name": "Grokipedia",
                "source_credibility": 7,
                "category": "news",
                "url": url,
                "published_at": "",
                "tags": ["grokipedia"],
                "_enhanced": True,
                "_provider": "grokipedia",
                "_score": 0,
                "_highlights": {},
            })

            if len(articles) >= max_results:
                break

        logger.info("Grokipedia returned %d results for '%s'", len(articles), query)
        return articles

    except Exception as e:
        logger.warning("Grokipedia error: %s", e)
        return []
