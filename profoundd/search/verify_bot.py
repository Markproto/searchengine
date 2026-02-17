"""
Verification Bot — Analyzes claims about legislation from dual perspectives.

Takes a social media post or claim, searches for primary sources, and produces
a balanced analysis showing both conservative and progressive viewpoints with
nuance highlighted for each.
"""
import logging
import json

import requests

logger = logging.getLogger(__name__)

# Timeout for fetching external URLs (keep tight to avoid gateway timeouts)
URL_FETCH_TIMEOUT = 10


def fetch_url_content(url, max_chars=12000):
    """
    Fetch a web page or PDF and extract its text content.
    Returns plain text, or empty string on failure.
    """
    if not url:
        return ""

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Profoundd/1.0 (verification bot)"},
            timeout=URL_FETCH_TIMEOUT,
            stream=True,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()

        if "pdf" in content_type or url.lower().endswith(".pdf"):
            # Try to extract text from PDF
            return _extract_pdf_text(resp.content, max_chars)
        else:
            # HTML or plain text
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text[:500000], "lxml")
            for tag in soup(["script", "style", "meta", "link", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... [truncated]"
            return text

    except Exception as e:
        logger.warning("Failed to fetch URL %s: %s", url, e)
        return ""


def _extract_pdf_text(pdf_bytes, max_chars=12000):
    """Extract text from PDF bytes. Tries multiple methods."""
    # Try PyPDF2 first
    try:
        import io
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text = []
        for page in reader.pages:
            pages_text.append(page.extract_text() or "")
        text = "\n".join(pages_text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated]"
        if text.strip():
            return text
    except ImportError:
        logger.debug("PyPDF2 not available for PDF extraction")
    except Exception as e:
        logger.warning("PyPDF2 extraction failed: %s", e)

    # Fallback: try pdfplumber
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
        text = "\n".join(pages_text)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [truncated]"
        if text.strip():
            return text
    except ImportError:
        logger.debug("pdfplumber not available for PDF extraction")
    except Exception as e:
        logger.warning("pdfplumber extraction failed: %s", e)

    logger.warning("No PDF extraction library available. Install PyPDF2: pip install PyPDF2")
    return "[PDF content could not be extracted — install PyPDF2 on server]"


def extract_search_queries(claim_text, api_key, model="claude-sonnet-4-5-20250929"):
    """Use AI to extract the key claims and generate targeted search queries."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    "Extract the key factual claims from this post and generate 3-5 targeted "
                    "search queries that would help verify or refute them. Focus on specific "
                    "bill names, section numbers, agency names, and policy details.\n\n"
                    "Also extract any specific references mentioned: bill section numbers, "
                    "page numbers, URLs, agency names, law names (e.g. FIFRA), and named people.\n\n"
                    "Return ONLY valid JSON in this format:\n"
                    '{"claims": ["claim 1", "claim 2"], '
                    '"queries": ["query 1", "query 2"], '
                    '"references": {"sections": ["10205", "10206"], '
                    '"pages": ["685", "686"], '
                    '"urls": ["https://..."], '
                    '"laws": ["FIFRA"], '
                    '"people": ["Lee Zeldin"], '
                    '"bills": ["Farm Bill 2026"]}}\n\n'
                    f"Post:\n{claim_text[:3000]}"
                ),
            }],
        )
        text = message.content[0].text.strip()
        # Parse JSON from response (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        logger.warning("Failed to extract claims: %s", e)
        # Fallback: use first 100 chars as a single query
        return {
            "claims": [claim_text[:200]],
            "queries": [claim_text[:100]],
        }


def search_for_evidence(queries, search_engine, congress_api_key=None,
                        references=None):
    """
    Search Profoundd index and external APIs for evidence.
    Also fetches actual bill text and referenced URL content when available.
    """
    from profoundd.search.external_providers import (
        fetch_congress_gov, fetch_federal_register,
        fetch_bill_text, fetch_bill_summary,
    )

    evidence = {
        "profoundd_results": [],
        "congress_results": [],
        "federal_register_results": [],
        "bill_text": [],          # Actual bill text excerpts
        "fetched_documents": [],  # Content from URLs in the claim
        "uploaded_documents": [], # User-uploaded bill PDFs/text
    }
    references = references or {}

    for query in queries[:5]:
        # Search our own index
        try:
            results = search_engine.search(
                query=query, category="legislative", page=1, per_page=5, sort_by="relevance",
            )
            for article in results.get("articles", []):
                evidence["profoundd_results"].append({
                    "title": article.get("title", ""),
                    "source": article.get("source_name", ""),
                    "summary": (article.get("summary") or "")[:300],
                    "url": article.get("url", ""),
                    "date": article.get("published_at", ""),
                })
        except Exception as e:
            logger.warning("Profoundd search failed for '%s': %s", query, e)

        # Congress.gov API
        if congress_api_key:
            try:
                results = fetch_congress_gov(query, api_key=congress_api_key, max_results=3)
                for r in results:
                    evidence["congress_results"].append({
                        "title": r["title"],
                        "source": r["source_name"],
                        "summary": r["summary"],
                        "url": r["url"],
                        "date": r.get("published_at", ""),
                        "_bill_type": r.get("_bill_type", ""),
                        "_bill_number": r.get("_bill_number", ""),
                        "_congress": r.get("_congress", ""),
                    })
            except Exception as e:
                logger.warning("Congress.gov search failed: %s", e)

        # Federal Register (no key needed)
        try:
            results = fetch_federal_register(query, max_results=3)
            for r in results:
                evidence["federal_register_results"].append({
                    "title": r["title"],
                    "source": r["source_name"],
                    "summary": r["summary"],
                    "url": r["url"],
                    "date": r.get("published_at", ""),
                })
        except Exception as e:
            logger.warning("Federal Register search failed: %s", e)

    # Deduplicate by URL (for metadata results)
    for key in ["profoundd_results", "congress_results", "federal_register_results"]:
        seen = set()
        unique = []
        for item in evidence[key]:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        evidence[key] = unique

    # --- Fetch actual bill text from Congress.gov ---
    if congress_api_key and evidence["congress_results"]:
        fetched_bills = set()
        for result in evidence["congress_results"][:3]:
            bill_type = result.get("_bill_type", "")
            bill_number = result.get("_bill_number", "")
            congress = result.get("_congress", "")
            if bill_type and bill_number and congress:
                bill_key = f"{congress}-{bill_type}-{bill_number}"
                if bill_key in fetched_bills:
                    continue
                fetched_bills.add(bill_key)

                # Get CRS summary (fast, small response)
                summary = fetch_bill_summary(congress, bill_type, bill_number, congress_api_key)
                if summary:
                    evidence["bill_text"].append({
                        "title": f"CRS Summary: {result['title']}",
                        "content": summary,
                        "url": result["url"],
                        "type": "summary",
                    })

                # Get actual bill text (slower, large response)
                text = fetch_bill_text(congress, bill_type, bill_number, congress_api_key)
                if text:
                    evidence["bill_text"].append({
                        "title": f"Full Text: {result['title']}",
                        "content": text,
                        "url": result["url"],
                        "type": "full_text",
                    })

    # --- Fetch content from URLs referenced in the claim ---
    ref_urls = references.get("urls", [])
    if ref_urls:
        fetched_urls = set()
        for url in ref_urls[:3]:
            if url in fetched_urls or "..." in url:
                continue
            fetched_urls.add(url)
            logger.info("Fetching referenced URL: %s", url)
            content = fetch_url_content(url)
            if content and content.strip():
                evidence["fetched_documents"].append({
                    "title": f"Referenced document: {url}",
                    "content": content,
                    "url": url,
                })

    return evidence


def generate_verification_story(claim_text, claims_data, evidence, api_key,
                                model="claude-sonnet-4-5-20250929"):
    """
    Generate a balanced verification story with both conservative and
    progressive perspectives, highlighting nuances of each position.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Compile evidence summary for the AI
        evidence_text = ""
        # Metadata results (titles, summaries, URLs)
        for source_type in ["profoundd_results", "congress_results", "federal_register_results"]:
            items = evidence.get(source_type, [])
            if items:
                label = source_type.replace("_", " ").title()
                evidence_text += f"\n--- {label} ---\n"
                for item in items[:8]:
                    evidence_text += f"- {item['title']} ({item.get('source', '')}, {item.get('date', '')})\n"
                    if item.get('summary'):
                        evidence_text += f"  {item['summary'][:200]}\n"
                    evidence_text += f"  URL: {item['url']}\n"

        # Actual bill text and CRS summaries — the critical content for verification
        bill_text_section = ""
        for item in evidence.get("bill_text", []):
            bill_text_section += f"\n--- {item['title']} ---\n"
            bill_text_section += f"URL: {item['url']}\n"
            bill_text_section += item["content"][:8000] + "\n"

        # Fetched documents from URLs referenced in the claim
        fetched_docs_section = ""
        for item in evidence.get("fetched_documents", []):
            fetched_docs_section += f"\n--- {item['title']} ---\n"
            fetched_docs_section += item["content"][:8000] + "\n"

        # User-uploaded bill documents — highest priority evidence
        uploaded_docs_section = ""
        for item in evidence.get("uploaded_documents", []):
            uploaded_docs_section += f"\n--- {item['title']} ---\n"
            uploaded_docs_section += item["content"][:15000] + "\n"

        claims_list = "\n".join(f"- {c}" for c in claims_data.get("claims", []))

        # Build structured references section from extracted data
        refs = claims_data.get("references", {})
        ref_lines = []
        if refs.get("sections"):
            ref_lines.append(f"Sections referenced: {', '.join(refs['sections'])}")
        if refs.get("pages"):
            ref_lines.append(f"Pages referenced: {', '.join(refs['pages'])}")
        if refs.get("bills"):
            ref_lines.append(f"Bills mentioned: {', '.join(refs['bills'])}")
        if refs.get("laws"):
            ref_lines.append(f"Laws cited: {', '.join(refs['laws'])}")
        if refs.get("people"):
            ref_lines.append(f"People named: {', '.join(refs['people'])}")
        if refs.get("urls"):
            ref_lines.append(f"URLs provided: {', '.join(refs['urls'])}")
        references_text = "\n".join(ref_lines) if ref_lines else "No specific references extracted."

        prompt = (
            "You are a senior editorial analyst for Profoundd, a non-partisan search engine. "
            "Your job is to verify legislative claims and present both sides fairly.\n\n"
            "A user shared a social media post making claims about legislation. "
            "Analyze it and produce a VERIFICATION REPORT with these sections:\n\n"
            "1. **CLAIM SUMMARY**: Brief summary of what's being claimed (2-3 sentences)\n\n"
            "2. **VERIFICATION**: For each claim, state whether it's TRUE, PARTIALLY TRUE, "
            "UNVERIFIED, or FALSE based on the evidence. Be specific — cite bill sections, "
            "dates, and sources.\n\n"
            "3. **CONSERVATIVE PERSPECTIVE**: How right-leaning media and commentators "
            "view this issue. Include their strongest arguments, concerns, and framing. "
            "Present this position charitably — steel-man it.\n\n"
            "4. **PROGRESSIVE PERSPECTIVE**: How left-leaning media and commentators "
            "view this issue. Include their strongest arguments, concerns, and framing. "
            "Present this position charitably — steel-man it.\n\n"
            "5. **NUANCE & CONTEXT**: What both sides get right. What both sides miss or "
            "oversimplify. Historical context that matters. Technical details that change "
            "the interpretation. This is the most important section.\n\n"
            "6. **PRIMARY SOURCES**: List the actual government documents, bill text URLs, "
            "or official sources where readers can verify for themselves.\n\n"
            "Rules:\n"
            "- Be rigorously fair. Do NOT favor either side.\n"
            "- Present each side's BEST arguments, not strawmen.\n"
            "- Acknowledge when evidence is incomplete or contested.\n"
            "- Use plain language, not academic jargon.\n"
            "- Include specific bill numbers, section numbers, and page references when available.\n"
            "- If something cannot be verified from the evidence provided, say so clearly.\n"
            "- CRITICAL: For EVERY talking point in sections 3, 4, and 5, you MUST include a "
            "parenthetical reference citing exactly WHERE in the original post or bill text "
            "the point is supported or contradicted. Use the format: "
            "*(See: Section XXXX, page XXX of the bill)* or "
            "*(Post claims: \"[exact quote]\")* or "
            "*(Not addressed in the post)* if the post omits it. "
            "Every single argument must be traceable back to a specific passage, section, "
            "page number, or quote. If the claim references specific sections or pages, "
            "cite those exact references. If a point is NOT in the original claim, "
            "explicitly say so — e.g., *(Not mentioned in the post — this is additional context)*.\n\n"
            f"=== ORIGINAL POST ===\n{claim_text[:4000]}\n\n"
            f"=== KEY CLAIMS IDENTIFIED ===\n{claims_list}\n\n"
            f"=== SPECIFIC REFERENCES FROM THE POST ===\n{references_text}\n\n"
            f"=== EVIDENCE FOUND (metadata) ===\n{evidence_text}\n\n"
            + (f"=== UPLOADED BILL DOCUMENT (PRIMARY SOURCE) ===\n{uploaded_docs_section}\n\n" if uploaded_docs_section else "")
            + (f"=== ACTUAL BILL TEXT / CRS SUMMARIES ===\n{bill_text_section}\n\n" if bill_text_section else
               ("" if uploaded_docs_section else "=== ACTUAL BILL TEXT ===\n[No bill text could be retrieved from Congress.gov]\n\n"))
            + (f"=== FETCHED DOCUMENTS FROM REFERENCED URLS ===\n{fetched_docs_section}\n\n" if fetched_docs_section else "")
            + "Write the verification report now. Use markdown formatting. "
            "Remember: EVERY talking point MUST cite back to specific sections, pages, "
            "or quotes from the original post. If actual bill text was provided above, "
            "use it to verify or refute specific claims — quote the relevant passages directly.\n\n"
            "IMPORTANT: At the very end of your report, on a new line, add this exact metadata block:\n"
            "```json-meta\n"
            '{"headline": "short headline max 100 chars", "summary": "2-3 sentence summary", '
            '"seo_keywords": "comma,separated,keywords"}\n'
            "```"
        )

        message = client.messages.create(
            model=model,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_output = message.content[0].text.strip()

        # Extract metadata block from end of report (saves a separate AI call)
        meta = {
            "headline": "Legislative Claim Verification",
            "summary": "A dual-perspective analysis of claims about pending legislation.",
            "seo_keywords": "verification, legislation, fact-check",
        }
        report = raw_output
        if "```json-meta" in raw_output:
            parts = raw_output.split("```json-meta")
            report = parts[0].strip()
            meta_block = parts[1].split("```")[0].strip() if "```" in parts[1] else parts[1].strip()
            try:
                meta = json.loads(meta_block)
            except json.JSONDecodeError:
                pass  # Use defaults

        return {
            "report": report,
            "headline": meta.get("headline", "Legislative Claim Verification"),
            "summary": meta.get("summary", ""),
            "seo_keywords": meta.get("seo_keywords", ""),
        }, None

    except Exception as e:
        logger.error("Verification story generation failed: %s", e)
        return None, str(e)
