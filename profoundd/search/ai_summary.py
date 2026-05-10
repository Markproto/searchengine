"""
AI-powered search summary.
Generates a brief AI answer from search results on every query.
Provider chosen via /admin/ai-provider (role='summary'); defaults to Anthropic.
"""
import logging

logger = logging.getLogger(__name__)


def generate_summary(query, articles, max_articles=12):
    """
    Send search results to the configured LLM and get an AI summary.

    Provider is determined by /admin/ai-provider (role='summary').
    Falls back to Anthropic when no config exists.

    Returns a dict with:
        - answer: The AI-generated answer/summary text
        - error: Error message if something went wrong, else None
    """
    if not articles:
        return {"answer": "", "error": None}

    # Build context from top results
    top = articles[:max_articles]
    context_parts = []
    for i, a in enumerate(top, 1):
        title = a.get("title", "")
        source = a.get("source_name", "")
        provider = a.get("_provider", "")
        summary = (a.get("summary") or "")[:500]
        url = a.get("url", "")
        source_label = source
        if provider == "grokipedia":
            source_label = f"Grokipedia ({source})"
        elif provider == "searxng":
            source_label = f"{source} (Web)"
        context_parts.append(
            f"[{i}] \"{title}\" — {source_label}\n"
            f"    {summary}\n"
            f"    URL: {url}"
        )
    context = "\n\n".join(context_parts)

    prompt = (
        f'You are a search assistant for Profoundd, an independent news search engine.\n\n'
        f'The user searched for: "{query}"\n\n'
        f'Here are the top search results from THIS index — your ONLY source of truth:\n\n'
        f'{context}\n\n'
        f'STRICT RULES (RAG-only mode):\n'
        f'- You may use ONLY information present in the numbered results above.\n'
        f'- You may NOT draw on background knowledge from your training data. Do NOT supplement,\n'
        f'  contextualize, or "fill in" facts from outside this corpus. If the corpus is silent\n'
        f'  on a sub-point, say so explicitly ("These results do not address X").\n'
        f'- If the results DISAGREE with each other, surface the disagreement rather than picking\n'
        f'  a winner. Quote both sides briefly.\n'
        f'- Cite by source name (e.g., "according to [3] ProPublica…"). Use the bracket number\n'
        f'  so the reader can map back to the result.\n'
        f'- If NONE of the results answer the query, say plainly: "These results do not directly\n'
        f'  cover this topic." Do not synthesize an answer from training data instead.\n'
        f'- Do not call any result "the consensus" or "mainstream" — describe what each says\n'
        f'  on its own.\n\n'
        f'Write a concise summary (under 200 words):\n'
        f'1. What each relevant result says about "{query}"\n'
        f'2. Where they agree, where they disagree, where the corpus is silent\n'
        f'3. Cite sources by bracket number and name\n\n'
        f'Be factual. Do not guess. Do not invent citations.'
    )

    try:
        from profoundd.utils.editorial_constitution import prepend as ec_prepend
        from profoundd.search.llm_provider import build_llm, invoke_text
        llm = build_llm("summary", max_tokens=500, temperature=0.2)
        answer = invoke_text(llm, ec_prepend(prompt)).strip()
        logger.info("AI summary generated for '%s' (%d chars)", query, len(answer))
        return {"answer": answer, "error": None}
    except RuntimeError as e:
        logger.warning("AI summary provider not configured: %s", e)
        return {"answer": "", "error": "AI not configured"}
    except Exception as e:
        msg = str(e)
        if "rate" in msg.lower() and "limit" in msg.lower():
            logger.warning("LLM rate limited: %s", msg)
            return {"answer": "", "error": "AI service busy"}
        if "connect" in msg.lower() or "timeout" in msg.lower():
            logger.warning("LLM not reachable: %s", msg)
            return {"answer": "", "error": "AI service unavailable"}
        logger.error("LLM error: %s", msg)
        return {"answer": "", "error": msg}


def is_available():
    """Check if any AI provider can be built for the summary role."""
    try:
        from profoundd.search.llm_provider import build_llm
        build_llm("summary")
        return True
    except Exception:
        return False
