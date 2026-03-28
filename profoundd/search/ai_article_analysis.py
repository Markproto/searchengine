"""
Multi-provider AI article analysis.
Generates full analysis: sentiment, bias, key points, market odds comparison.
Supports Claude (Anthropic), ChatGPT (OpenAI), and Grok (xAI).
"""
import json
import logging

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Analyze this news article and provide a comprehensive breakdown.

ARTICLE:
Title: {title}
Source: {source}
Content: {content}

Provide your analysis in the following exact format (keep each section label on its own line):

SUMMARY:
A 2-3 sentence summary of what this article reports.

SENTIMENT:
One word: positive, negative, neutral, or mixed

BIAS_NOTES:
Identify any detectable bias in the reporting. Note the framing, loaded language, missing perspectives, or balanced presentation. Be specific but concise (2-3 sentences).

KEY_POINTS:
- Point 1
- Point 2
- Point 3
(3-5 bullet points of the most important takeaways)

MARKET_RELEVANCE:
How might this news affect prediction markets, elections, or public opinion? What would bettors or forecasters pay attention to? (2-3 sentences)

CREDIBILITY_NOTES:
Comment on source reliability, whether claims are verifiable, and if the reporting cites primary sources. (1-2 sentences)
"""


def analyze_article(url, title, summary, source_name, provider, api_key, model=None):
    """
    Run full AI analysis on an article.

    Args:
        url: Article URL
        title: Article title
        summary: Article summary/excerpt
        source_name: Name of the source
        provider: "anthropic", "openai", or "xai"
        api_key: User's API key
        model: Optional model override

    Returns:
        dict with analysis fields, or {"error": "..."} on failure
    """
    # Fetch full article content if possible
    content = summary or ""
    try:
        from profoundd.search.ai_analyzer import fetch_url_content
        full = fetch_url_content(url)
        if full and full.get("text"):
            content = full["text"][:8000]
    except Exception:
        pass  # Fall back to summary

    if not content:
        content = f"{title}. {summary or ''}"

    # Inject editorial context notes (pollster accuracy, anomalies, etc.)
    context_notes = ""
    try:
        from profoundd.search.analysis_notes import get_context_notes
        context_notes = get_context_notes(title or "", summary or "", source_name or "")
    except Exception:
        pass

    prompt = ANALYSIS_PROMPT.format(
        title=title or "Unknown",
        source=source_name or "Unknown",
        content=content[:8000],
    )

    if context_notes:
        prompt += f"\n\nThe following statistical context is provided for your reference. " \
                  f"Include relevant statistics in your analysis where appropriate. " \
                  f"Present these as factual data points — do not draw political conclusions, " \
                  f"but help the reader understand the statistical landscape:\n\n{context_notes}"

    try:
        if provider == "anthropic":
            return _call_anthropic(prompt, api_key, model or "claude-haiku-4-5-20251001")
        elif provider == "openai":
            return _call_openai(prompt, api_key, model or "gpt-4o-mini", base_url=None)
        elif provider == "xai":
            return _call_openai(prompt, api_key, model or "grok-2-latest", base_url="https://api.x.ai/v1")
        else:
            return {"error": f"Unknown provider: {provider}"}
    except Exception as e:
        logger.warning("AI analysis error (%s): %s", provider, e)
        return {"error": str(e)}


def _call_anthropic(prompt, api_key, model):
    """Call Claude API."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    return _parse_analysis(text)


def _call_openai(prompt, api_key, model, base_url=None):
    """Call OpenAI-compatible API (ChatGPT or Grok)."""
    import openai
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = openai.OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content
    return _parse_analysis(text)


def _parse_analysis(text):
    """Parse structured AI response into dict."""
    result = {
        "raw_text": text,
        "summary": "",
        "sentiment": "neutral",
        "bias_notes": "",
        "key_points": [],
        "market_relevance": "",
        "credibility_notes": "",
        "error": None,
    }

    current_field = None
    current_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        upper = stripped.upper().rstrip(":")

        if upper in ("SUMMARY", "SENTIMENT", "BIAS_NOTES", "BIAS NOTES",
                      "KEY_POINTS", "KEY POINTS", "MARKET_RELEVANCE",
                      "MARKET RELEVANCE", "CREDIBILITY_NOTES", "CREDIBILITY NOTES"):
            # Save previous field
            if current_field:
                _save_field(result, current_field, current_lines)
            current_field = upper.replace(" ", "_")
            current_lines = []
        else:
            current_lines.append(line)

    # Save last field
    if current_field:
        _save_field(result, current_field, current_lines)

    return result


def _save_field(result, field, lines):
    """Save parsed lines into the appropriate result field."""
    text = "\n".join(lines).strip()
    field = field.lower()

    if field == "summary":
        result["summary"] = text
    elif field == "sentiment":
        # Extract single word
        word = text.split()[0].lower().rstrip(".,") if text else "neutral"
        if word in ("positive", "negative", "neutral", "mixed"):
            result["sentiment"] = word
        else:
            result["sentiment"] = "neutral"
    elif field in ("bias_notes", "bias"):
        result["bias_notes"] = text
    elif field in ("key_points", "key"):
        points = [l.strip().lstrip("- ").lstrip("* ") for l in text.split("\n") if l.strip() and l.strip() != "-"]
        result["key_points"] = points
    elif field in ("market_relevance",):
        result["market_relevance"] = text
    elif field in ("credibility_notes",):
        result["credibility_notes"] = text
