"""
Shared helper for AI-powered "Explain" endpoints across Epstein, Climate, and
WEF documents. Consolidates what was three near-duplicate endpoints into one
pipeline with:

1. Pre-flight guard — if none of the query's significant tokens appear in
   the content, return a cheap static message. No LLM call, no spend.

2. Smart content window — instead of content[:8000] which loses matches
   deeper in a doc, find where the query appears and take ~8000 chars
   centered on that location.

3. Exact-key SQLite cache — reuses the existing two-tier cache
   (profoundd.utils.cache) keyed on (doc_type, doc_id, page_number,
   normalized_query). 24h TTL. Cache hits return in <1 ms.

4. LangChain provider swap — AI_EXPLAIN_PROVIDER env var selects:
     anthropic (default)  -> ChatAnthropic via langchain_anthropic
     ollama               -> ChatOllama via langchain_ollama
   Same shared prompt templates for either provider. Ollama target is
   Apollo9 today and Tark1 GPU once that host is 24/7.

Public API:
    explain(doc_type, doc_id, page_number, content, query, meta, prompt_template)
    -> (dict, int)  # (body, http_status)
"""
import hashlib
import logging
import os
import re

from profoundd.utils.cache import cache_get, cache_set, AI_SUMMARY_TTL

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "what", "who",
    "why", "how", "when", "where", "does", "was", "are", "is", "be",
    "a", "an", "of", "in", "on", "to", "as", "by", "or", "it", "its",
    "did", "do", "have", "has", "had", "not", "but", "can", "could",
    "would", "should", "will", "shall", "may", "might", "must",
}

_PUNCT_RE = re.compile(r"[^a-z0-9 ]")


def _normalize(s):
    return " ".join(_PUNCT_RE.sub(" ", (s or "").lower()).split())


def _tokens(s):
    """Significant tokens (>=3 chars, not stopwords)."""
    return [t for t in _normalize(s).split() if len(t) >= 3 and t not in _STOPWORDS]


def _cache_key(doc_type, doc_id, page_number, query_norm):
    raw = f"explain:{doc_type}:{doc_id}:{page_number}:{query_norm}"
    return f"explain:{hashlib.md5(raw.encode()).hexdigest()}"


def _summary_cache_key(doc_type, doc_id, page_number):
    """Per-page summary cache key — independent of user's query so every
    pre-flight miss on the same page shares one cached answer."""
    raw = f"explain:{doc_type}:{doc_id}:{page_number}:__summary__"
    return f"explain:{hashlib.md5(raw.encode()).hexdigest()}"


def _get_or_make_summary(doc_type, doc_id, page_number, content, meta):
    """Return (summary_text, was_cached). Returns (None, False) on failure.

    Summaries are keyed per-page and reused across every query that
    pre-flights away. One LLM call per page, no matter how many users
    search terms that don't match.
    """
    template = SUMMARY_PROMPTS.get(doc_type)
    if not template:
        return None, False

    key = _summary_cache_key(doc_type, doc_id, page_number or 0)
    cached = cache_get(key)
    if cached is not None:
        try:
            txt = cached.decode("utf-8") if isinstance(cached, (bytes, memoryview)) else str(cached)
        except Exception:
            txt = str(cached)
        return txt, True

    # Build the summary prompt (no query involved — just the page content)
    window = (content or "")[:8000]
    safe_meta = {k: (v if v is not None else "") for k, v in (meta or {}).items()}
    try:
        prompt = template.format(content_window=window, **safe_meta)
    except KeyError as e:
        logger.warning("summary prompt missing key %s", e)
        return None, False

    try:
        llm = _build_llm()
    except Exception as e:
        logger.warning("summary LLM init failed: %s", e)
        return None, False

    try:
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content=prompt)])
        summary_text = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        logger.warning("summary LLM call failed: %s", e)
        return None, False

    try:
        cache_set(key, summary_text, ttl=AI_SUMMARY_TTL)
    except Exception:
        pass
    return summary_text, False


def _pre_flight(content, query):
    """Return True if at least one significant query token appears in content."""
    toks = _tokens(query)
    if not toks:
        # Query is all stopwords or too short — let the LLM try anyway
        return True
    ncontent = _normalize(content)
    return any(t in ncontent for t in toks)


def _smart_window(content, query, window_total=8000):
    """
    Return ~window_total chars centered on the first occurrence of `query`
    (or any significant token of it). If nothing matches, fall back to
    content[:window_total].
    """
    if not content:
        return ""
    if len(content) <= window_total:
        return content
    half = window_total // 2
    lc = content.lower()
    idx = lc.find(query.lower())
    if idx < 0:
        for t in _tokens(query):
            idx = lc.find(t)
            if idx >= 0:
                break
    if idx < 0:
        return content[:window_total]
    start = max(0, idx - half)
    end = min(len(content), start + window_total)
    return content[start:end]


_llm_cache = {"provider": None, "llm": None}


def _build_llm():
    """Lazy-construct and cache a LangChain LLM handle per provider config."""
    provider = os.environ.get("AI_EXPLAIN_PROVIDER", "anthropic").lower()
    if _llm_cache["provider"] == provider and _llm_cache["llm"] is not None:
        return _llm_cache["llm"]

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "mistral:latest"),
            base_url=os.environ.get("OLLAMA_URL", "http://10.0.6.1:11434"),
            temperature=0.2,
            num_predict=1200,
        )
    else:
        from langchain_anthropic import ChatAnthropic
        from profoundd.admin.routes import get_anthropic_key
        from profoundd.utils.models import SiteSetting
        model = SiteSetting.get("ai_anthropic_model", "claude-sonnet-4-6")
        api_key = get_anthropic_key()
        if not api_key:
            raise RuntimeError("Anthropic API key not configured")
        llm = ChatAnthropic(
            model=model,
            api_key=api_key,
            max_tokens=1200,
            temperature=0.2,
            # Retry on transient overload (HTTP 529) + rate limits with
            # exponential backoff. Anthropic SDK handles 429/529/5xx natively.
            max_retries=4,
            timeout=60,
        )

    _llm_cache["provider"] = provider
    _llm_cache["llm"] = llm
    return llm


def _try_grok_fallback(prompt):
    """Best-effort xAI Grok fallback for when Claude is overloaded/unavailable.

    Returns (explanation_text, None) on success, (None, error_message) on
    failure. Returns (None, None) when xAI is simply not configured (no key).
    """
    try:
        from profoundd.utils.models import SiteSetting
        xai_key = SiteSetting.get("ai_xai_key", "") or os.environ.get("XAI_API_KEY", "")
        if not xai_key:
            return None, None
        model = SiteSetting.get("ai_xai_model", "grok-2-latest")
    except Exception as e:
        logger.debug("grok fallback config lookup failed: %s", e)
        return None, str(e)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1", timeout=60)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1200,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content if response.choices else ""
        if not text:
            return None, "Grok returned empty response"
        return text, None
    except Exception as e:
        logger.warning("grok fallback failed: %s", e)
        return None, str(e)


def explain(doc_type, doc_id, page_number, content, query, meta, prompt_template):
    """
    Execute an AI Explain call with pre-flight guard + cache + LangChain.

    Returns (body, http_status) so callers can `return jsonify(body), status`.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "Missing query"}, 400

    content = content or ""
    if not content:
        return {"error": "This page has no extracted text"}, 400

    # 1) Pre-flight guard — if the query doesn't appear on this page, return
    #    a cached page summary instead of a "not found" message. Summary is
    #    keyed per-page (not per-query) so every miss on the same page shares
    #    the same cached LLM response — bounded cost, better UX than a bare
    #    rejection.
    if not _pre_flight(content, query):
        summary, summary_cached = _get_or_make_summary(
            doc_type, doc_id, page_number, content, meta
        )
        if summary is None:
            # LLM/config failure — fall back to the old static message
            return {
                "explanation": (
                    f"\"{query}\" doesn't appear on this page, and the "
                    f"summary fallback is unavailable. Try the main search."
                ),
                "cached": True,
                "skipped_llm": True,
                "summary_fallback": False,
                "provider": "preflight",
            }, 200
        preface = (
            f"\"{query}\" doesn't appear on this page. "
            f"Here's what this page covers:\n\n"
        )
        return {
            "explanation": preface + summary,
            "cached": summary_cached,
            "skipped_llm": summary_cached,
            "summary_fallback": True,
            "provider": "cache" if summary_cached else (
                os.environ.get("AI_EXPLAIN_PROVIDER", "anthropic").lower()
            ),
        }, 200

    # 2) Cache lookup
    query_norm = _normalize(query)
    key = _cache_key(doc_type, doc_id, page_number or 0, query_norm)
    cached = cache_get(key)
    if cached is not None:
        try:
            cached_str = cached.decode("utf-8") if isinstance(cached, (bytes, memoryview)) else str(cached)
        except Exception:
            cached_str = str(cached)
        logger.info("explain cache hit: %s/%s p=%s", doc_type, doc_id, page_number)
        return {
            "explanation": cached_str,
            "cached": True,
            "skipped_llm": False,
            "provider": "cache",
        }, 200

    # 3) Smart window — catch matches past the first 8000 chars
    content_window = _smart_window(content, query)

    # 4) Build prompt + LLM call
    safe_meta = {k: (v if v is not None else "") for k, v in (meta or {}).items()}
    try:
        prompt = prompt_template.format(
            query=query, content_window=content_window, **safe_meta
        )
    except KeyError as e:
        logger.warning("explain prompt missing key %s", e)
        return {"error": f"Prompt template missing key: {e}"}, 500

    try:
        llm = _build_llm()
    except RuntimeError as e:
        return {"error": str(e)}, 500
    except Exception as e:
        logger.warning("explain LLM init failed: %s", e)
        return {"error": f"AI provider unavailable: {e}"}, 500

    primary_provider = os.environ.get("AI_EXPLAIN_PROVIDER", "anthropic").lower()
    explanation = None
    used_provider = primary_provider
    fallback_reason = None

    try:
        from langchain_core.messages import HumanMessage
        result = llm.invoke([HumanMessage(content=prompt)])
        explanation = result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        msg = str(e)
        logger.warning("explain primary (%s) failed: %s", primary_provider, msg)
        is_overload = "529" in msg or "overloaded" in msg.lower()
        is_ratelimit = "rate" in msg.lower() and "limit" in msg.lower()

        # Try Grok as automatic fallback when Claude is unavailable.
        grok_explanation, grok_err = _try_grok_fallback(prompt)
        if grok_explanation is not None:
            explanation = grok_explanation
            used_provider = "grok"
            fallback_reason = (
                "claude_overloaded" if is_overload
                else ("claude_ratelimit" if is_ratelimit else "claude_error")
            )
            logger.info("explain fell back to Grok after %s", fallback_reason)
        else:
            # Both providers failed — return a friendly message
            if is_overload:
                return {
                    "error": (
                        "Both Claude and Grok are unavailable right now. "
                        "Try again in a minute or two."
                    ),
                    "fallback_attempted": grok_err is not None,
                }, 503
            if is_ratelimit:
                return {"error": "Rate limit hit — try again in a moment."}, 429
            return {"error": f"AI analysis failed: {msg}"}, 500

    # 5) Cache write (24h TTL — same as existing ai-summary cache)
    try:
        cache_set(key, explanation, ttl=AI_SUMMARY_TTL)
    except Exception as e:
        logger.debug("explain cache write failed: %s", e)

    return {
        "explanation": explanation,
        "cached": False,
        "skipped_llm": False,
        "provider": used_provider,
        "fallback_reason": fallback_reason,
    }, 200


# Prompt templates per doc_type — kept identical to the original inline
# prompts so existing UX doesn't regress. Template variables:
#   {query}, {content_window}, + doc-type-specific meta keys.
PROMPTS = {
    "epstein": """You are analyzing a court document from the Jeffrey Epstein case files (DOJ release).

The user searched for: "{query}"
Document title: {title}
Bates number: {bates}
Custodian: {custodian}

Document text (centered on the search term):
{content_window}

Based ONLY on what this document says, explain:
1. Who or what is "{query}" in relation to this document?
2. Why are they mentioned? What is the context?
3. What role do they appear to play — victim, witness, associate, attorney, judge, reporter, or other?
4. Any other relevant details from this specific document.

Be factual and concise. Only state what the document contains. If the search term doesn't clearly appear, say so. Do not speculate beyond the text.""",

    "climate": """You are analyzing a page from a public Oregon city planning document.

User's question / search term: "{query}"
Document: {document_name}
City: {city}
Page: {page_number}
Adopted: {adopted_date}

Page text (centered on the search term):
{content_window}

Based ONLY on what this page says, answer:
1. How does this page treat "{query}"?
2. What specific commitments, rules, goals, or recommendations relate to it?
3. Quote the exact key sentence(s) if present.
4. If "{query}" does not appear or is not relevant on this page, say so plainly.

Be factual and concise. Only state what the page contains.""",

    "wef": """You are analyzing a page from a World Economic Forum publication.

User's question / search term: "{query}"
Document: {document_name}
Year: {year}
Page: {page_number}

Page text (centered on the search term):
{content_window}

Based ONLY on what this page says, answer:
1. How does this page treat "{query}"?
2. What specific WEF positions, recommendations, or forecasts relate to it?
3. Quote key sentences verbatim where relevant.
4. If "{query}" doesn't appear or isn't relevant on this page, say so plainly.

Be factual and concise. Only state what the page contains.""",

    "fwp": """You are analyzing a Federal Writers' Project document from the
Library of Congress (1936-1940 WPA-era oral histories and life stories).

User's question / search term: "{query}"
Title: {title}
Contributors / interviewers: {contributors}
Location: {location}
Date: {doc_date}

Document text (centered on the search term):
{content_window}

Based ONLY on what this document says, answer:
1. How does this narrative relate to "{query}"?
2. Quote the key sentences verbatim where relevant.
3. Note who is speaking (the interviewer or the narrator) and the historical
   context if it is clear from the text.
4. If "{query}" doesn't appear, say so plainly and describe what the
   document is actually about.

This is a historical document — preserve original phrasing in quotes even
where the language reflects the era. Be factual and concise.""",
}


# Query-less summary prompts — fired when pre-flight finds the user's search
# term nowhere on the page. One summary per page, cached 24h, shared across
# every query that misses. Result is a concise description of what the page
# actually contains, so users get something useful even when their term
# doesn't match.
SUMMARY_PROMPTS = {
    "epstein": """You are summarizing a court document from the Jeffrey Epstein case files (DOJ release).

Document title: {title}
Bates number: {bates}
Custodian: {custodian}

Document text:
{content_window}

In 3-5 sentences, describe what this document actually contains:
- Type of document (email, pleading, deposition excerpt, exhibit, etc.)
- Key people named and their apparent roles
- Main subject or topic being discussed
- Any notable dates, places, or events mentioned

Be factual and concise. Only state what the document contains.""",

    "climate": """You are summarizing a page from a public Oregon city planning document.

Document: {document_name}
City: {city}
Page: {page_number}

Page text:
{content_window}

In 3-5 sentences, describe what this page actually contains:
- Section or topic of the page
- Main commitments, rules, or goals it lays out
- Any specific numbers, deadlines, or named programs
- How it fits into the broader document

Be factual and concise. Only state what the page contains.""",

    "wef": """You are summarizing a page from a World Economic Forum publication.

Document: {document_name}
Year: {year}
Page: {page_number}

Page text:
{content_window}

In 3-5 sentences, describe what this page actually contains:
- Section or topic
- Main WEF positions, recommendations, or forecasts on the page
- Any specific numbers, named initiatives, or quoted authorities
- The page's role in the broader document

Be factual and concise. Only state what the page contains.""",

    "fwp": """You are summarizing a Federal Writers' Project document from
the Library of Congress (1936-1940 WPA-era oral histories).

Title: {title}
Contributors / interviewers: {contributors}
Location: {location}
Date: {doc_date}

Document text:
{content_window}

In 3-5 sentences, describe what this document actually contains:
- Whose life story or topic it covers
- The narrator's apparent background (occupation, era of life recounted)
- Main events, places, or themes mentioned
- Any historically notable details or named people

Preserve original phrasing in quotes where useful. Be factual and concise.""",
}
