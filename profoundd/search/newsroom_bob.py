"""
NewsRoom Bob - AI-generated stories from original source articles.
Takes an article, rewrites it with SEO optimization, and publishes to /newsroom.
"""
import logging
import re

logger = logging.getLogger(__name__)

BOB_PROMPT = """You are NewsRoom Bob, a sharp, no-nonsense journalist for Profoundd.com.
Your job: take the original article below and write a FRESH, original story based on it.
Do NOT copy the original — rewrite it in your own voice. Be direct, informative, and engaging.
Write like a seasoned reporter who respects the reader's time.

Original Article:
Title: {title}
Source: {source_name}
Category: {category}
URL: {url}

Content/Summary:
{content}

Write your response in EXACTLY this format:

HEADLINE: [A compelling, SEO-friendly headline — clear, not clickbait, under 80 chars]
SUMMARY: [2-3 sentence summary that hooks the reader and covers the key facts]
BODY: [Full article body, 3-6 paragraphs. Use facts from the original but in your own words. Include context and analysis. Reference the original source naturally, e.g. "according to [source]" or "as reported by [source]".]
SEO_KEYWORDS: [8-12 comma-separated keywords/phrases relevant to the story, optimized for search]
SEO_DESCRIPTION: [A 150-160 character meta description for search engines]
IMAGE_SEARCH: [A short phrase to describe what image would accompany this story, e.g. "capitol building washington dc"]"""


def generate_bob_story(article_data, api_key, model="claude-sonnet-4-6"):
    """Generate a Bob story from an article using Claude.

    article_data should have: title, url, source_name, category, summary/content
    Returns dict with: headline, summary, body, seo_keywords, seo_description, image_search
    """
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        content = article_data.get("content") or article_data.get("summary") or article_data.get("title", "")
        if len(content) > 12000:
            content = content[:12000] + "\n\n[Content truncated...]"

        prompt = BOB_PROMPT.format(
            title=article_data.get("title", "Untitled"),
            source_name=article_data.get("source_name", "Unknown"),
            category=article_data.get("category", "news"),
            url=article_data.get("url", ""),
            content=content,
        )

        message = client.messages.create(
            model=model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text
        return _parse_bob_response(response_text), None

    except Exception as e:
        logger.error("Bob generation failed: %s", e)
        return None, f"Bob couldn't write this one: {e}"


def _parse_bob_response(text):
    """Parse Bob's structured response into a dict."""
    result = {
        "headline": "",
        "summary": "",
        "body": "",
        "seo_keywords": "",
        "seo_description": "",
        "image_search": "",
    }

    # Section markers — Claude sometimes wraps them in Markdown bold (**BODY:**)
    # or uses ## headers. Strip those before checking.
    _section_re = re.compile(r'^[*#\s]*(HEADLINE|SUMMARY|BODY|SEO_KEYWORDS|SEO_DESCRIPTION|IMAGE_SEARCH)[*#\s]*:\s*', re.IGNORECASE)

    lines = text.strip().split("\n")
    current_field = None
    body_lines = []

    for line in lines:
        stripped = line.strip()
        m = _section_re.match(stripped)

        if m:
            field = m.group(1).upper()
            rest = stripped[m.end():].strip()

            if field == "HEADLINE":
                current_field = "headline"
                result["headline"] = rest
            elif field == "SUMMARY":
                current_field = "summary"
                result["summary"] = rest
            elif field == "BODY":
                current_field = "body"
                if rest:
                    body_lines.append(rest)
            elif field == "SEO_KEYWORDS":
                current_field = "seo_keywords"
                result["seo_keywords"] = rest
            elif field == "SEO_DESCRIPTION":
                current_field = "seo_description"
                result["seo_description"] = rest
            elif field == "IMAGE_SEARCH":
                current_field = "image_search"
                result["image_search"] = rest
        elif current_field == "body":
            body_lines.append(line)
        elif current_field == "summary":
            result["summary"] += " " + stripped

    result["body"] = "\n".join(body_lines).strip()
    result["summary"] = result["summary"].strip()

    return result


def make_slug(title):
    """Generate a URL-friendly slug from a title."""
    slug = title.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip('-')
    return slug[:200]
