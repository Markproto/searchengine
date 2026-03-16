"""
Local AI-powered search summary using Ollama + Mistral.
Generates an AI answer/summary from search results on every query.
Runs entirely locally on Apollo9 — no external API calls.
"""
import logging
import os
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = "mistral"
OLLAMA_TIMEOUT = 90  # CPU-only Mistral 7B can take up to ~60s


def generate_summary(query, articles, max_articles=5):
    """
    Send search results to local Mistral via Ollama and get an AI summary.

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
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 400,
                },
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("response", "").strip()
        logger.info("AI summary generated for '%s' (%d chars)", query, len(answer))
        return {"answer": answer, "error": None}

    except requests.Timeout:
        logger.warning("Ollama timed out for '%s'", query)
        return {"answer": "", "error": "AI summary timed out"}
    except requests.ConnectionError:
        logger.warning("Ollama not reachable")
        return {"answer": "", "error": "AI service unavailable"}
    except Exception as e:
        logger.error("Ollama error: %s", e)
        return {"answer": "", "error": str(e)}


def is_available():
    """Check if Ollama is running and has the model loaded."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if resp.status_code != 200:
            return False
        models = [m["name"] for m in resp.json().get("models", [])]
        return any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return False
