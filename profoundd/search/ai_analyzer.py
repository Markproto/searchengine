"""
AI-powered URL content analyzer.
Fetches a URL, extracts content, and uses Claude or Grok to analyze it.
Supports a draft -> feedback -> revise loop so editors can refine the AI
output without re-fetching the source URL.
"""
import hashlib
import logging

import requests
from bs4 import BeautifulSoup

from profoundd.utils.cache import cache_get, cache_set

logger = logging.getLogger(__name__)

# Max content length to send to the AI (characters)
MAX_CONTENT_LENGTH = 15000

# How long the fetched page content is cached for revision passes (1 hour).
ANALYZE_CONTENT_TTL = 3600


def _content_cache_key(url: str) -> str:
    return f"analyze_url:content:{hashlib.md5(url.encode('utf-8')).hexdigest()}"


def cache_content(content_data: dict) -> None:
    """Store fetched URL content so revise passes don't re-fetch."""
    if not content_data or not content_data.get("url"):
        return
    try:
        import json
        cache_set(_content_cache_key(content_data["url"]),
                  json.dumps(content_data),
                  ttl=ANALYZE_CONTENT_TTL)
    except Exception as e:
        logger.debug("cache_content failed: %s", e)


def get_cached_content(url: str) -> dict | None:
    """Pull cached fetched content; re-fetches via fetch_url_content if absent."""
    try:
        import json
        raw = cache_get(_content_cache_key(url))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="ignore")
        return json.loads(raw)
    except Exception as e:
        logger.debug("get_cached_content miss/error: %s", e)
        return None


def fetch_url_content(url):
    """Fetch and extract readable text content from a URL."""
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ProfounddBot/1.0)",
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Try to find article content
        article = soup.find("article") or soup.find("main") or soup.find("body")
        if not article:
            return None, "Could not extract content from URL"

        text = article.get_text(separator="\n", strip=True)

        # Get page title
        page_title = ""
        title_tag = soup.find("title")
        if title_tag:
            page_title = title_tag.get_text(strip=True)

        # Get meta description
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        # Truncate content if too long
        if len(text) > MAX_CONTENT_LENGTH:
            text = text[:MAX_CONTENT_LENGTH] + "\n\n[Content truncated...]"

        return {
            "url": url,
            "page_title": page_title,
            "meta_description": meta_desc,
            "text": text,
        }, None

    except requests.Timeout:
        return None, "Request timed out"
    except requests.RequestException as e:
        return None, f"Failed to fetch URL: {e}"
    except Exception as e:
        return None, f"Error extracting content: {e}"


ANALYSIS_PROMPT = """You are a senior editorial analyst for Profoundd, a multi-domain search engine. Analyze the following web page content thoroughly and extract structured information.

URL: {url}
Page Title: {page_title}

Content:
{text}

Provide a detailed analysis using EXACTLY this format. Each field should be on its own line starting with the label. Be thorough and informative.

TITLE: [A clear, accurate, compelling article title — not clickbait]
SUMMARY: [A detailed 4-6 sentence summary covering the main argument, key evidence presented, notable claims, and conclusion. Be specific — include names, dates, statistics, and findings mentioned in the article.]
KEY_POINTS: [3-5 bullet points of the most important facts or claims, separated by semicolons. Example: FDA approved drug X for condition Y; Study included 3,000 participants over 2 years; Side effects reported in 12% of cases]
CATEGORY: [One of: news, politics, legal, medical, science, tech, finance, markets, environment, education]
TAGS: [Comma-separated relevant tags, 5-8 tags covering topic, people, organizations, and themes]
CREDIBILITY: [Rate 1-10. Consider: Does it cite primary sources? Are claims verifiable? Is the reporting balanced? Is the author/outlet established? 8-10 = peer-reviewed/official sources, 6-7 = established journalism, 4-5 = opinion/blog, 1-3 = unverified/misleading]
CREDIBILITY_REASONING: [One sentence explaining the credibility score. Example: "Published by Reuters with multiple named sources and official data citations."]
SOURCE_NAME: [The publication, organization, or website name]
AUTHOR: [Author name if identifiable, otherwise "Unknown"]
DATE_PUBLISHED: [Publication date if found in content, in YYYY-MM-DD format, otherwise "Unknown"]
BIAS_NOTES: [Brief note on any detectable bias or perspective. Example: "Article presents primarily the plaintiff's perspective" or "Balanced coverage with quotes from both sides" or "None detected"]"""


def analyze_with_anthropic(content_data, api_key=None, model=None):
    """Analyze content with whichever provider is configured for role='analyzer'.

    Provider/model args are kept for legacy callers but are ignored —
    the centralized llm_provider.build_llm('analyzer') honors the
    /admin/ai-provider toggle. Function name preserved for back-compat.
    """
    return _run_analyzer(content_data, provider_override="anthropic" if api_key else None,
                         model_override=model)


def analyze_with_xai(content_data, api_key=None, model=None):
    """Same as analyze_with_anthropic but pinned to xAI when called directly."""
    return _run_analyzer(content_data, provider_override="xai" if api_key else None,
                         model_override=model)


def _run_analyzer(content_data, provider_override=None, model_override=None):
    try:
        from profoundd.utils.editorial_constitution import prepend as ec_prepend
        from profoundd.search.llm_provider import build_llm, invoke_text
        prompt = ANALYSIS_PROMPT.format(
            url=content_data["url"],
            page_title=content_data["page_title"],
            text=content_data["text"],
        )
        llm = build_llm("analyzer", max_tokens=2048, temperature=0.2,
                        provider_override=provider_override,
                        model_override=model_override)
        response_text = invoke_text(llm, ec_prepend(prompt))
        return _parse_analysis(response_text, content_data["url"]), None
    except Exception as e:
        logger.error("Analyzer LLM error: %s", e)
        return None, f"Analysis failed: {e}"


REVISION_PROMPT = """You are a senior editorial analyst for Profoundd. You previously produced a draft for the URL below. The editor has reviewed it and given specific feedback. Apply the feedback faithfully — do not change fields the editor did not ask to change. Output the FULL revised draft using the same exact format.

URL: {url}
Page Title: {page_title}

ORIGINAL SOURCE CONTENT (the article being analyzed):
{text}

CURRENT DRAFT (your previous output, possibly with minor manual edits by the editor):
TITLE: {title}
SUMMARY: {summary}
KEY_POINTS: {key_points}
CATEGORY: {category}
TAGS: {tags}
CREDIBILITY: {credibility}
CREDIBILITY_REASONING: {credibility_reasoning}
SOURCE_NAME: {source_name}
AUTHOR: {author}
DATE_PUBLISHED: {date_published}
BIAS_NOTES: {bias_notes}

EDITOR FEEDBACK (apply this):
{feedback}

Now produce the REVISED draft. Use EXACTLY the same field labels, one per line, in the same order. Be specific and substantive. Do not editorialize about the feedback itself — just apply it.

TITLE: ...
SUMMARY: ...
KEY_POINTS: ...
CATEGORY: ...
TAGS: ...
CREDIBILITY: ...
CREDIBILITY_REASONING: ...
SOURCE_NAME: ...
AUTHOR: ...
DATE_PUBLISHED: ...
BIAS_NOTES: ..."""


def _build_revision_prompt(content_data: dict, current_draft: dict, feedback: str) -> str:
    return REVISION_PROMPT.format(
        url=content_data.get("url", ""),
        page_title=content_data.get("page_title", ""),
        text=content_data.get("text", ""),
        title=current_draft.get("title", ""),
        summary=current_draft.get("summary", ""),
        key_points=current_draft.get("key_points", ""),
        category=current_draft.get("category", "news"),
        tags=current_draft.get("tags", ""),
        credibility=current_draft.get("credibility", 7),
        credibility_reasoning=current_draft.get("credibility_reasoning", ""),
        source_name=current_draft.get("source_name", ""),
        author=current_draft.get("author", ""),
        date_published=current_draft.get("date_published", ""),
        bias_notes=current_draft.get("bias_notes", ""),
        feedback=(feedback or "").strip() or "(none — just polish and tighten the draft)",
    )


def revise_with_anthropic(content_data, current_draft, feedback, api_key=None, model=None):
    """Re-run draft with editor feedback. Provider via /admin/ai-provider role='analyzer'."""
    return _run_revise(content_data, current_draft, feedback,
                       provider_override="anthropic" if api_key else None,
                       model_override=model)


def revise_with_xai(content_data, current_draft, feedback, api_key=None, model=None):
    return _run_revise(content_data, current_draft, feedback,
                       provider_override="xai" if api_key else None,
                       model_override=model)


def _run_revise(content_data, current_draft, feedback,
                provider_override=None, model_override=None):
    try:
        from profoundd.utils.editorial_constitution import prepend as ec_prepend
        from profoundd.search.llm_provider import build_llm, invoke_text
        prompt = _build_revision_prompt(content_data, current_draft, feedback)
        llm = build_llm("analyzer", max_tokens=2048, temperature=0.2,
                        provider_override=provider_override,
                        model_override=model_override)
        response_text = invoke_text(llm, ec_prepend(prompt))
        return _parse_analysis(response_text, content_data.get("url", "")), None
    except Exception as e:
        logger.error("Revise LLM error: %s", e)
        return None, f"Revision failed: {e}"


def _parse_analysis(text, url):
    """Parse the structured AI response into a dict."""
    result = {
        "title": "",
        "summary": "",
        "key_points": "",
        "category": "news",
        "tags": "",
        "credibility": 7,
        "credibility_reasoning": "",
        "source_name": "",
        "author": "",
        "date_published": "",
        "bias_notes": "",
        "url": url,
    }

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("TITLE:"):
            result["title"] = line[6:].strip()
        elif line.startswith("SUMMARY:"):
            result["summary"] = line[8:].strip()
        elif line.startswith("KEY_POINTS:"):
            result["key_points"] = line[11:].strip()
        elif line.startswith("CATEGORY:"):
            cat = line[9:].strip().lower()
            valid_cats = {"news", "politics", "legal", "medical", "science",
                         "tech", "finance", "markets", "environment", "education"}
            if cat in valid_cats:
                result["category"] = cat
        elif line.startswith("TAGS:"):
            result["tags"] = line[5:].strip()
        elif line.startswith("CREDIBILITY:"):
            try:
                val = int(line[12:].strip().split("/")[0].split(" ")[0])
                result["credibility"] = max(1, min(10, val))
            except (ValueError, IndexError):
                pass
        elif line.startswith("CREDIBILITY_REASONING:"):
            result["credibility_reasoning"] = line[22:].strip()
        elif line.startswith("SOURCE_NAME:"):
            result["source_name"] = line[12:].strip()
        elif line.startswith("AUTHOR:"):
            result["author"] = line[7:].strip()
        elif line.startswith("DATE_PUBLISHED:"):
            result["date_published"] = line[15:].strip()
        elif line.startswith("BIAS_NOTES:"):
            result["bias_notes"] = line[11:].strip()

    return result
