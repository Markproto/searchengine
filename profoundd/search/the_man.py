"""
The Man — AI editorial guidance system.
Analyzes articles against editorial guidelines and past ranking actions,
then suggests or applies boost/demote decisions.
"""
import json
import logging

logger = logging.getLogger(__name__)

THE_MAN_PROMPT = """You are "The Man", the editorial AI for Profoundd search engine. Your job is to review articles and decide whether each should be promoted, demoted, or left alone in search rankings.

## Editorial Guidelines (the only criteria you may apply)
{guidelines}

## Past Admin Decisions (the precedent you should follow)
{past_actions}

## Articles to Review
{articles}

## Strict decision rules (RAG-only mode)
- Base every decision ONLY on (a) the editorial guidelines above and
  (b) the patterns visible in past admin decisions. Do not import
  reputation judgments from your training data ("X is a known
  conspiracy site," "Y is a respected mainstream outlet"). The
  admin's past decisions are the only authoritative signal for
  source-level reputation.
- If an article matches a pattern in past decisions, follow that
  pattern. State which past decision you're following in REASON.
- Do not use words like "misinformation," "credible," "fringe," or
  "mainstream" as standalone justifications. Cite the specific
  editorial rule you are applying.
- If you genuinely cannot determine from the guidelines + past
  decisions whether to promote/demote, choose SKIP.

## Instructions
Review each article above. For each one, decide:
- PROMOTE: if it aligns with the editorial guidelines AND/OR matches
  past PROMOTE precedent
- DEMOTE: if it conflicts with the editorial guidelines AND/OR matches
  past DEMOTE precedent
- SKIP: if it's neutral, ambiguous, or unprecedented

Respond with EXACTLY one line per article in this format:
ARTICLE_URL | ACTION | REASON

Where:
- ARTICLE_URL is the exact URL from the article
- ACTION is one of: PROMOTE, DEMOTE, SKIP
- REASON is a brief explanation (one sentence) citing the specific
  rule or precedent applied. Example REASONS:
    "Pfizer-sponsored outlet per guideline #3"
    "Matches past PROMOTE on COVID origins coverage"
    "Past decisions silent on this source/topic — defaulting to SKIP"

Only output the lines, no other text."""


def format_past_actions(actions, limit=30):
    """Format recent admin ranking actions as context for The Man."""
    if not actions:
        return "No past actions yet. Use the editorial guidelines to make decisions."

    lines = []
    for a in actions[:limit]:
        lines.append(f"- {a.action.upper()} \"{a.article_title}\" from {a.source_name} ({a.category}) [searched: {a.search_query or 'n/a'}]")
    return "\n".join(lines)


def format_articles(articles, limit=50):
    """Format articles from ES for The Man to review."""
    lines = []
    for i, art in enumerate(articles[:limit], 1):
        title = art.get("title", "Unknown")
        source = art.get("source_name", "Unknown")
        category = art.get("category", "unknown")
        summary = (art.get("summary") or "")[:200]
        url = art.get("url", "")
        boost = art.get("admin_boost", 0) or 0
        cred = art.get("source_credibility", 5)
        lines.append(f"{i}. [{url}]\n   Title: {title}\n   Source: {source} (credibility: {cred})\n   Category: {category}\n   Current boost: {boost}\n   Summary: {summary}")
    return "\n\n".join(lines)


def parse_the_man_response(response_text):
    """Parse The Man's response into a list of decisions."""
    decisions = []
    for line in response_text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            url = parts[0].strip()
            action = parts[1].strip().upper()
            reason = parts[2].strip()
            if action in ("PROMOTE", "DEMOTE", "SKIP"):
                decisions.append({
                    "url": url,
                    "action": action.lower(),
                    "reason": reason,
                })
    return decisions


def run_the_man(engine, guidelines, past_actions, api_key, model="claude-sonnet-4-6", auto_apply=False):
    """
    Run The Man: fetch recent articles, analyze with Claude, return decisions.

    Args:
        engine: SearchEngine instance
        guidelines: editorial guidelines text
        past_actions: list of AdminRankingAction objects
        api_key: Anthropic API key
        model: Claude model to use
        auto_apply: if True, apply boost changes directly to ES

    Returns:
        (decisions, error) tuple
    """
    try:
        import anthropic
    except ImportError:
        return None, "anthropic package not installed"

    if not api_key:
        return None, "No Anthropic API key configured. Set it in AI Settings."

    if not guidelines:
        return None, "No editorial guidelines set. Write guidelines first so The Man knows what to look for."

    # Fetch recent articles from ES (last 200, sorted by recency)
    try:
        result = engine.es.search(
            index=engine.index_name,
            body={
                "query": {"match_all": {}},
                "sort": [{"crawled_at": {"order": "desc"}}],
                "size": 50,
                "_source": ["title", "summary", "source_name", "category", "url",
                            "admin_boost", "source_credibility", "published_at"],
            }
        )
        articles = [hit["_source"] for hit in result["hits"]["hits"]]
    except Exception as e:
        return None, f"Failed to fetch articles from ES: {e}"

    if not articles:
        return None, "No articles found in the index."

    # Build the prompt
    prompt = THE_MAN_PROMPT.format(
        guidelines=guidelines,
        past_actions=format_past_actions(past_actions),
        articles=format_articles(articles),
    )

    # Call via centralized llm_provider (role='the_man').
    try:
        from profoundd.utils.editorial_constitution import prepend as ec_prepend
        from profoundd.search.llm_provider import build_llm, invoke_text
        llm = build_llm("the_man", max_tokens=4096, temperature=0.2)
        response_text = invoke_text(llm, ec_prepend(prompt))
    except Exception as e:
        logger.error("The Man LLM error: %s", e)
        return None, f"LLM call failed: {e}"

    # Parse decisions
    decisions = parse_the_man_response(response_text)

    if not decisions:
        return None, "The Man returned no actionable decisions. Raw response logged."

    # Auto-apply if requested
    if auto_apply:
        applied = 0
        for d in decisions:
            if d["action"] == "skip":
                continue
            article = engine.get_article(d["url"])
            if not article:
                continue
            old_boost = article.get("admin_boost", 0) or 0
            new_boost = old_boost + (1 if d["action"] == "promote" else -1)
            new_boost = max(-5, min(5, new_boost))
            if engine.update_boost(d["url"], new_boost):
                d["old_boost"] = old_boost
                d["new_boost"] = new_boost
                d["applied"] = True
                applied += 1
            else:
                d["applied"] = False
        logger.info("The Man auto-applied %d/%d decisions", applied, len(decisions))

    return decisions, None
