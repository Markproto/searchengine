"""
Cross-collection "Related docs" finder.

For a given source document, find topically-related pages in every other
collection (Climate, WEF, Epstein, news articles). Uses Elasticsearch's
More-Like-This query against each target index in one msearch call so the
four queries run in parallel on the ES side. Results are cached per source
doc for 24 hours.

Public API:
    find_related(source_type, source_id, title, content, limit=3) -> dict

    Returns:
        {
            "climate": [{title, url, snippet, score}, ...],
            "wef":     [...],
            "epstein": [...],
            "articles":[...],
        }
    Skips the source's own collection. Missing/failing collections come
    back as [] instead of raising.
"""
import hashlib
import json
import logging

from profoundd.utils.cache import cache_get, cache_set, AI_SUMMARY_TTL

logger = logging.getLogger(__name__)

# index_name + URL builder per target type
_TARGETS = {
    "climate":  ("profoundd_climate_docs", "doc_id",      "/climate-docs/{doc_id}/{page_number}"),
    "wef":      ("profoundd_wef_docs",     "doc_id",      "/wef-docs/{doc_id}/{page_number}"),
    "epstein":  ("profoundd_epstein_docs", "bates_number","/epstein-docs/{bates_number}"),
    "articles": ("profoundd_articles",     "url",         "{url}"),
}

_CACHE_PREFIX = "related:"


def _cache_key(source_type, source_id):
    raw = f"{_CACHE_PREFIX}{source_type}:{source_id}"
    return f"{_CACHE_PREFIX}{hashlib.md5(raw.encode()).hexdigest()}"


def _mlt_body(like_text, size):
    """Construct the More-Like-This query body for one target index."""
    return {
        "query": {
            "more_like_this": {
                "fields": ["content", "title", "document_name"],
                "like": (like_text or "")[:3000],
                "min_term_freq": 1,
                "min_doc_freq": 2,
                "max_query_terms": 25,
                "minimum_should_match": "30%",
            }
        },
        "size": size,
    }


def _format_hit(target_type, src):
    """Turn an ES _source into a {title, url, snippet, score} dict."""
    url_tmpl = _TARGETS[target_type][2]
    try:
        url = url_tmpl.format(**{
            "doc_id": src.get("doc_id", ""),
            "page_number": src.get("page_number", 1) or 1,
            "bates_number": src.get("bates_number", ""),
            "url": src.get("url", "#"),
        })
    except Exception:
        url = "#"

    if target_type == "articles":
        title = src.get("title") or "Untitled article"
    elif target_type == "epstein":
        title = f"{src.get('bates_number', '')} — {src.get('title', '')}".strip(" —")
        if not title:
            title = src.get("bates_number") or "Court document"
    elif target_type == "climate":
        title = f"{src.get('city', '')} — {src.get('document_name', '')}".strip(" —")
        if src.get("page_number"):
            title = f"{title} p.{src['page_number']}"
    elif target_type == "wef":
        title = src.get("document_name", "WEF document")
        if src.get("page_number"):
            title = f"{title} p.{src['page_number']}"
    else:
        title = src.get("title", "(untitled)")

    snippet_src = src.get("summary") or src.get("content") or ""
    snippet = (snippet_src[:220] + "…") if len(snippet_src) > 220 else snippet_src

    return {
        "title": title[:180],
        "url": url,
        "snippet": snippet,
    }


def find_related(es, source_type, source_id, title, content, limit=3):
    """Return related docs from every collection other than source_type.

    `es` is an elasticsearch client. On any error the result dict still
    has all keys (with empty lists) so templates don't crash.
    """
    # Cache check first
    key = _cache_key(source_type, source_id)
    cached = cache_get(key)
    if cached is not None:
        try:
            data = cached.decode("utf-8") if isinstance(cached, (bytes, memoryview)) else str(cached)
            return json.loads(data)
        except Exception as e:
            logger.debug("related cache parse error: %s", e)

    # Build "like" text from source title + content. Title gets triple
    # weight by appearing three times — cheap hack, works well with MLT.
    like_text = ""
    if title:
        like_text += f"{title}\n{title}\n{title}\n"
    like_text += (content or "")[:2500]

    results = {k: [] for k in _TARGETS if k != source_type}

    if not es or not like_text.strip():
        return results

    # Build msearch body — one request per target index
    msearch_lines = []
    active_targets = []
    for target_type in results:
        index_name = _TARGETS[target_type][0]
        msearch_lines.append(json.dumps({"index": index_name}))
        msearch_lines.append(json.dumps(_mlt_body(like_text, limit)))
        active_targets.append(target_type)

    body = "\n".join(msearch_lines) + "\n"

    try:
        resp = es.msearch(body=body, request_timeout=3)
    except Exception as e:
        logger.warning("related msearch failed: %s", e)
        return results

    responses = resp.get("responses", []) if isinstance(resp, dict) else []
    for target_type, r in zip(active_targets, responses):
        if not isinstance(r, dict) or "hits" not in r:
            continue
        for hit in r["hits"].get("hits", []):
            src = hit.get("_source", {})
            # Don't include the source doc itself
            id_field = _TARGETS[target_type][1]
            if source_type == target_type and src.get(id_field) == source_id:
                continue
            results[target_type].append(_format_hit(target_type, src))

    try:
        cache_set(key, json.dumps(results), ttl=AI_SUMMARY_TTL)
    except Exception:
        pass

    return results
