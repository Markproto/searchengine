"""
Verification Bot — Analyzes claims about legislation from dual perspectives.

Takes a social media post or claim, searches for primary sources, and produces
a balanced analysis showing both conservative and progressive viewpoints with
nuance highlighted for each.
"""
import logging
import json

logger = logging.getLogger(__name__)


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


def search_for_evidence(queries, search_engine, congress_api_key=None):
    """Search Profoundd index and external APIs for evidence."""
    from profoundd.search.external_providers import (
        fetch_congress_gov, fetch_federal_register,
    )

    evidence = {
        "profoundd_results": [],
        "congress_results": [],
        "federal_register_results": [],
    }

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

    # Deduplicate by URL
    for key in evidence:
        seen = set()
        unique = []
        for item in evidence[key]:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)
        evidence[key] = unique

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
        for source_type, items in evidence.items():
            if items:
                label = source_type.replace("_", " ").title()
                evidence_text += f"\n--- {label} ---\n"
                for item in items[:8]:
                    evidence_text += f"- {item['title']} ({item['source']}, {item['date']})\n"
                    if item['summary']:
                        evidence_text += f"  {item['summary'][:200]}\n"
                    evidence_text += f"  URL: {item['url']}\n"

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
            f"=== EVIDENCE FOUND ===\n{evidence_text}\n\n"
            "Write the verification report now. Use markdown formatting. "
            "Remember: EVERY talking point MUST cite back to specific sections, pages, "
            "or quotes from the original post."
        )

        message = client.messages.create(
            model=model,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )

        report = message.content[0].text.strip()

        # Also generate a short headline and summary for indexing
        meta_msg = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": (
                    "Given this verification report, generate a JSON object with:\n"
                    '{"headline": "short headline (max 100 chars)", '
                    '"summary": "2-3 sentence summary of the findings", '
                    '"seo_keywords": "comma-separated keywords"}\n\n'
                    f"Report:\n{report[:2000]}"
                ),
            }],
        )
        meta_text = meta_msg.content[0].text.strip()
        if "```" in meta_text:
            meta_text = meta_text.split("```")[1]
            if meta_text.startswith("json"):
                meta_text = meta_text[4:]
        try:
            meta = json.loads(meta_text)
        except json.JSONDecodeError:
            meta = {
                "headline": "Legislative Claim Verification",
                "summary": "A dual-perspective analysis of claims about pending legislation.",
                "seo_keywords": "verification, legislation, fact-check",
            }

        return {
            "report": report,
            "headline": meta.get("headline", "Legislative Claim Verification"),
            "summary": meta.get("summary", ""),
            "seo_keywords": meta.get("seo_keywords", ""),
        }, None

    except Exception as e:
        logger.error("Verification story generation failed: %s", e)
        return None, str(e)
