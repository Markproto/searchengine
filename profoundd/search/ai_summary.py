"""
AI-powered search summary using Claude API.
Generates a brief AI answer from search results on every query.
"""
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"


def generate_summary(query, articles, max_articles=5):
    """
    Send search results to Claude and get an AI summary.

    Returns a dict with:
        - answer: The AI-generated answer/summary text
        - error: Error message if something went wrong, else None
    """
    if not articles:
        return {"answer": "", "error": None}

    if not ANTHROPIC_API_KEY:
        return {"answer": "", "error": "AI not configured"}

    # Build context from top results
    top = articles[:max_articles]
    context_parts = []
    for i, a in enumerate(top, 1):
        title = a.get("title", "")
        source = a.get("source_name", "")
        summary = (a.get("summary") or "")[:300]
        url = a.get("url", "")
        context_parts.append(
            f"[{i}] \"{title}\" — {source}\n"
            f"    {summary}\n"
            f"    URL: {url}"
        )
    context = "\n\n".join(context_parts)

    prompt = (
        f'You are a search assistant for Profoundd, an independent news search engine.\n\n'
        f'The user searched for: "{query}"\n\n'
        f'Here are the top search results:\n\n'
        f'{context}\n\n'
        f'RULES:\n'
        f'- ONLY use information from the results above. Do NOT add outside knowledge.\n'
        f'- When citing a source, use its exact name (e.g. "according to Reuters").\n'
        f'- If a result is not relevant to the query, ignore it.\n'
        f'- If none of the results answer the query well, say "These results don\'t directly cover this topic."\n\n'
        f'Write a brief summary (under 150 words):\n'
        f'1. What the results say about "{query}"\n'
        f'2. Which source is most relevant and why\n\n'
        f'Be factual and concise. Do not guess or speculate.'
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = message.content[0].text.strip()
        logger.info("AI summary generated for '%s' (%d chars)", query, len(answer))
        return {"answer": answer, "error": None}

    except anthropic.APIConnectionError:
        logger.warning("Claude API not reachable")
        return {"answer": "", "error": "AI service unavailable"}
    except anthropic.RateLimitError:
        logger.warning("Claude API rate limited")
        return {"answer": "", "error": "AI service busy"}
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return {"answer": "", "error": str(e)}


def is_available():
    """Check if Claude API key is configured."""
    return bool(ANTHROPIC_API_KEY)
