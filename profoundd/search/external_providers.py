"""
External search providers for enhanced results.
Queries CourtListener (legal) and PubMed/arXiv (medical/science) APIs
in real-time when the user's search matches those domains.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# Timeout for all external API calls (seconds)
API_TIMEOUT = 5

# Categories that trigger each provider
LEGAL_CATEGORIES = {"legal"}
SCIENCE_CATEGORIES = {"medical", "science"}

# Keywords that trigger providers when category is "all"
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


def should_enhance(query, category):
    """
    Determine which providers to activate.
    Returns a set of provider names: {'courtlistener', 'pubmed'}
    """
    providers = set()
    q_lower = (query or "").lower()

    if category in LEGAL_CATEGORIES:
        providers.add("courtlistener")
    elif category in SCIENCE_CATEGORIES:
        providers.add("pubmed")
    elif category == "all" or not category:
        # Auto-detect from query keywords
        if any(kw in q_lower for kw in LEGAL_KEYWORDS):
            providers.add("courtlistener")
        if any(kw in q_lower for kw in SCIENCE_KEYWORDS):
            providers.add("pubmed")

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


def fetch_all_enhanced(query, category, max_per_provider=5):
    """
    Fetch results from all applicable external providers.
    Returns (articles_list, active_providers_set).
    """
    providers = should_enhance(query, category)
    if not providers:
        return [], set()

    all_articles = []
    active = set()

    if "courtlistener" in providers:
        results = fetch_courtlistener(query, max_results=max_per_provider)
        if results:
            all_articles.extend(results)
            active.add("courtlistener")

    if "pubmed" in providers:
        results = fetch_pubmed(query, max_results=max_per_provider)
        if results:
            all_articles.extend(results)
            active.add("pubmed")

    return all_articles, active


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
