#!/usr/bin/env python3
"""Build a training corpus for Profoundd LoRA fine-tuning.

Extracts editorial-perspective training pairs from:
  1. Bob NewsRoom stories — instruction: "Write a Profoundd article about X"
     response: the actual story body (your editorial voice).
  2. Approved curator proposals — instruction: "Should we block/sponsor-tag/
     adjust credibility of <domain>? Sample articles: ..." response: the
     applied decision + reasoning.
  3. Source ratings — pair: (article excerpt from this source) →
     (credibility rating + your editorial take).
  4. Cached AI Explain responses (admin-approved via reaction) — pair:
     (document excerpt + query) → (the explanation as given).
  5. Editorial constitution — repeatedly seeded as the system message so
     the model internalizes the framing.
  6. Blocked-domain examples — instruction: "Profile this article" with
     a sample, response: "This source is blocked because [reason]. Do not
     use as primary evidence."

Output format: JSONL with {messages: [{role: ..., content: ...}]} entries,
compatible with trl.SFTTrainer's chat-template input.

Usage:
  docker exec -e PYTHONPATH=/app profoundd python3 \
      /app/scripts/build_training_corpus.py \
      --output /app/data/training_corpus.jsonl

Then SCP the output to Tark1 for training.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Iterator

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from profoundd.app import create_app
from profoundd.utils.models import (
    db, Source, BobStory, SiteSetting, CuratorProposal,
    AdminRankingAction, ResearchDocument,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _constitution_messages() -> list[dict]:
    """Read the live editorial constitution and prepare it as a system message."""
    from profoundd.utils.editorial_constitution import get_constitution
    return [{"role": "system", "content": get_constitution()}]


def _bob_stories(limit: int | None = None) -> Iterator[dict]:
    """Each Bob story becomes a (write a story about X) → (story body) pair.

    BobStory schema (verified): title, slug, content, summary, source_article_url,
    source_article_title.
    """
    q = db.session.query(BobStory).filter(BobStory.status == "published")
    if limit:
        q = q.limit(limit)
    for s in q.all():
        if not (s.content and s.title):
            continue
        if s.source_article_title:
            instr = (
                f"Profoundd's NewsRoom Bob received this source article: "
                f"\"{s.source_article_title}\" from {s.source_name or 'unknown'}. "
                f"Write a fresh Profoundd-perspective story based on it, "
                f"with a headline and ~3-paragraph body. Apply your editorial "
                f"principles: name regulators' funding sources, surface "
                f"dissenting peer-reviewed work, drop 'misinformation/debunked' "
                f"framings, cite primary sources where present."
            )
        else:
            instr = (
                f"Write a Profoundd newsroom story with the headline "
                f"'{s.title}'. Apply your editorial principles. Cite "
                f"primary sources. Keep the same voice."
            )
        response = f"HEADLINE: {s.title}\n\n{s.content}"
        yield {
            "messages": [
                *_constitution_messages(),
                {"role": "user", "content": instr},
                {"role": "assistant", "content": response},
            ],
            "_source": f"bob_story:{s.id}",
        }


def _approved_curator_proposals() -> Iterator[dict]:
    """Each Applied/Rejected proposal becomes a verdict-prediction pair."""
    rows = (db.session.query(CuratorProposal)
            .filter(CuratorProposal.status.in_(["applied", "rejected"]))
            .all())
    for p in rows:
        instr = (
            f"A nightly classifier proposed: {p.proposal_type} on domain "
            f"{p.target_domain}, proposed_value='{p.proposed_value}', "
            f"confidence={p.confidence:.2f}. Reasoning: {p.reasoning}\n\n"
            f"Should the admin approve or reject this proposal? Explain in "
            f"2-3 sentences applying Profoundd's editorial principles."
        )
        if p.status == "applied":
            verdict = "Approve."
        else:
            verdict = "Reject."
        response = (
            f"{verdict} "
            f"{'Apply the proposal and use the before_value snapshot to revert if needed. ' if p.status == 'applied' else 'Do not apply. The classifier signal does not justify this change given Profoundd editorial principles. '}"
            f"Reasoning context: {p.reasoning}"
        )
        yield {
            "messages": [
                *_constitution_messages(),
                {"role": "user", "content": instr},
                {"role": "assistant", "content": response},
            ],
            "_source": f"curator_proposal:{p.id}",
        }


def _source_credibility() -> Iterator[dict]:
    """Each Source row becomes a "rate this outlet" pair."""
    rows = db.session.query(Source).filter(Source.is_active == True).all()
    for s in rows:
        if not (s.url and s.credibility is not None):
            continue
        sponsor = f"Sponsor tags: {s.sponsor_tags}. " if s.sponsor_tags else ""
        bias = f"Bias score (1=far-left, 10=far-right): {s.bias_score}/10. " if s.bias_score else ""
        instr = (
            f"Rate the credibility of {s.name} ({s.url}) on Profoundd's "
            f"1-10 scale where 1 is unreliable opinion and 10 is "
            f"peer-reviewed primary source. {sponsor}{bias}Explain in one "
            f"sentence what kind of source this is and how Profoundd "
            f"weights it."
        )
        response = (
            f"Credibility: {s.credibility}/10. "
            f"{'This outlet has sponsor disclosure obligations on Profoundd — note funding when citing claims they make on related topics. ' if s.sponsor_tags else ''}"
            f"Category: {s.category}. "
            f"Treat as a Profoundd-curated feed worth surfacing in search results."
        )
        yield {
            "messages": [
                *_constitution_messages(),
                {"role": "user", "content": instr},
                {"role": "assistant", "content": response},
            ],
            "_source": f"source:{s.id}",
        }


def _blocked_domains() -> Iterator[dict]:
    """Each blocked domain becomes a "this is blocked, here's why" example."""
    raw = SiteSetting.get("blocked_domains", "")
    for d in [x.strip().lower() for x in raw.replace("\r", "").split("\n") if x.strip()]:
        instr = (
            f"A user asks Profoundd to summarize results about a topic, "
            f"and one of the search results is from {d}. How should "
            f"Profoundd treat that result?"
        )
        response = (
            f"{d} is on Profoundd's blocked-domains list. Skip it as a "
            f"primary source. If it must be referenced (e.g., to cite a "
            f"claim being rebutted by other sources), describe it as "
            f"'blocked by Profoundd editorial policy' and link the reader "
            f"to higher-tier primary sources covering the same topic."
        )
        yield {
            "messages": [
                *_constitution_messages(),
                {"role": "user", "content": instr},
                {"role": "assistant", "content": response},
            ],
            "_source": f"blocked:{d}",
        }


def _research_docs() -> Iterator[dict]:
    """Curated research documents become exemplars of Profoundd-style writing."""
    rows = db.session.query(ResearchDocument).all()
    for r in rows:
        if not (r.title and r.content):
            continue
        instr = (
            f"Profoundd has indexed a research document titled '{r.title}'. "
            f"Summarize what this document contains in 3-5 sentences for "
            f"a reader who is researching this topic. Apply RAG-only mode: "
            f"do not draw on training-data knowledge outside the document."
        )
        body = (r.content or "")[:3000]
        summary = (r.summary or "")[:800]
        if not summary:
            summary = body[:400]
        yield {
            "messages": [
                *_constitution_messages(),
                {"role": "user", "content": instr + f"\n\nDocument content:\n{body}"},
                {"role": "assistant", "content": summary},
            ],
            "_source": f"research:{r.id}",
        }


def build_corpus(out_path: str) -> dict:
    """Walk all extractors, write JSONL, return summary."""
    summary = {
        "bob_stories": 0,
        "curator_proposals": 0,
        "source_credibility": 0,
        "blocked_domains": 0,
        "research_docs": 0,
        "total": 0,
        "skipped_empty": 0,
    }

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for source_key, gen in [
            ("bob_stories", _bob_stories()),
            ("curator_proposals", _approved_curator_proposals()),
            ("source_credibility", _source_credibility()),
            ("blocked_domains", _blocked_domains()),
            ("research_docs", _research_docs()),
        ]:
            for entry in gen:
                # Strip the _source telemetry key from the JSONL payload, but
                # log it for provenance.
                src = entry.pop("_source", "")
                msgs = entry.get("messages", [])
                # Skip examples where the assistant content is empty
                if not any(m.get("role") == "assistant" and (m.get("content") or "").strip() for m in msgs):
                    summary["skipped_empty"] += 1
                    continue
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                summary[source_key] += 1
                summary["total"] += 1
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/app/data/training_corpus.jsonl")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        log.info("Building training corpus -> %s", args.output)
        summary = build_corpus(args.output)
        log.info("Corpus summary:")
        for k, v in summary.items():
            log.info("  %s: %d", k, v)
        log.info("Total bytes: %d", os.path.getsize(args.output) if os.path.exists(args.output) else 0)


if __name__ == "__main__":
    main()
