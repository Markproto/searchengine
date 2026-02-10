"""
AI-powered URL content analyzer.
Fetches a URL, extracts content, and uses Claude or Grok to analyze it.
"""
import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Max content length to send to the AI (characters)
MAX_CONTENT_LENGTH = 15000


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


ANALYSIS_PROMPT = """Analyze the following web page content and extract structured information for a search engine index.

URL: {url}
Page Title: {page_title}

Content:
{text}

Respond with EXACTLY this format (no markdown, no extra text):
TITLE: [A clear, accurate article title]
SUMMARY: [A 2-3 sentence summary of the key points]
CATEGORY: [One of: news, politics, legal, medical, science, tech, finance, markets, environment, education]
TAGS: [Comma-separated relevant tags, max 5]
CREDIBILITY: [Rate 1-10 based on source quality, citation of evidence, balanced reporting]
SOURCE_NAME: [The publication or website name]"""


def analyze_with_anthropic(content_data, api_key, model="claude-sonnet-4-5-20250929"):
    """Use Anthropic's Claude to analyze extracted content."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        prompt = ANALYSIS_PROMPT.format(
            url=content_data["url"],
            page_title=content_data["page_title"],
            text=content_data["text"],
        )

        message = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = message.content[0].text
        return _parse_analysis(response_text, content_data["url"]), None

    except Exception as e:
        logger.error("Anthropic API error: %s", e)
        return None, f"Claude analysis failed: {e}"


def analyze_with_xai(content_data, api_key, model="grok-2-latest"):
    """Use xAI's Grok to analyze extracted content."""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

        prompt = ANALYSIS_PROMPT.format(
            url=content_data["url"],
            page_title=content_data["page_title"],
            text=content_data["text"],
        )

        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.choices[0].message.content
        return _parse_analysis(response_text, content_data["url"]), None

    except Exception as e:
        logger.error("xAI API error: %s", e)
        return None, f"Grok analysis failed: {e}"


def _parse_analysis(text, url):
    """Parse the structured AI response into a dict."""
    result = {
        "title": "",
        "summary": "",
        "category": "news",
        "tags": "",
        "credibility": 7,
        "source_name": "",
        "url": url,
    }

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("TITLE:"):
            result["title"] = line[6:].strip()
        elif line.startswith("SUMMARY:"):
            result["summary"] = line[8:].strip()
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
        elif line.startswith("SOURCE_NAME:"):
            result["source_name"] = line[12:].strip()

    return result
