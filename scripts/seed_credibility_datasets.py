#!/usr/bin/env python3
"""Seed DomainCredibility from public datasets.

Sources (all open-licensed, no API keys):
  - CRED-1 (aloth/cred-1)  — 2,674 domains scored 0.0-1.0 with multi-signal composite
                              (Iffy.news labels are already merged in via the iffy_* columns,
                              so a separate Iffy fetch is unnecessary)
  - Wikipedia RSP          — Reliable Sources Perennial list, ~600 hand-rated outlets

Re-run idempotent. Existing rows updated in place; provenance accumulated in `sources` field.

Usage:
    docker exec -e PYTHONPATH=/app profoundd python3 /app/scripts/seed_credibility_datasets.py
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import requests

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from profoundd.app import create_app
from profoundd.utils.models import db, DomainCredibility


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


CRED1_URL = "https://raw.githubusercontent.com/aloth/cred-1/main/data/cred1_current.csv"
RSP_URL = "https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/Perennial_sources"


def _fetch(url: str, accept: str = "text/csv") -> str:
    r = requests.get(url, headers={"User-Agent": "ProfoundCurator/1.0", "Accept": accept}, timeout=60)
    r.raise_for_status()
    return r.text


def _normalize_domain(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc or s
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    s = s.strip(".")
    if not s or "." not in s:
        return None
    if not re.match(r"^[a-z0-9.\-]+$", s):
        return None
    return s


def _parse_cred1(text: str) -> Iterable[dict]:
    """CRED-1 schema (verified against repo):
        domain, category, credibility_score, sources, iffy_factual, iffy_bias, iffy_score,
        tranco_rank, domain_age_years, domain_registered, factcheck_claims, safe_browsing_flagged,
        score_cat, score_iffy, score_tranco, score_age, score_factcheck, score_safebrowsing
    """
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        domain = _normalize_domain(row.get("domain") or "")
        if not domain:
            continue
        try:
            cred = float(row.get("credibility_score") or 0)
        except ValueError:
            continue
        cred = max(0.0, min(1.0, cred))
        cat = (row.get("category") or "").strip().lower()[:50] or None
        # iffy_bias values: "", "CP" (conspiracy/pseudoscience), "FN" (fake news), "LC" (low credibility) — tags, not 1-10
        iffy_bias = (row.get("iffy_bias") or "").strip().upper()
        notes_parts = []
        if iffy_bias:
            notes_parts.append(f"iffy_bias={iffy_bias}")
        if row.get("iffy_factual"):
            notes_parts.append(f"iffy_factual={row['iffy_factual']}")
        if row.get("safe_browsing_flagged") and row["safe_browsing_flagged"].strip().lower() in ("true", "1", "yes"):
            notes_parts.append("safe_browsing_flagged")
        yield {
            "domain": domain,
            "credibility": cred,
            "bias_score": None,
            "category": cat,
            "source": "cred1",
            "notes": "; ".join(notes_parts)[:500],
        }


# Wikipedia RSP scrape — extract from the article HTML.
RSP_STATUS_TO_SCORE = {
    "generally reliable": 0.80,
    "no consensus": 0.50,
    "generally unreliable": 0.20,
    "deprecated": 0.10,
    "blacklisted": 0.05,
}

RSP_DOMAIN_HINT = re.compile(
    r"\b([a-z0-9][a-z0-9\-]*\.(?:com|org|net|co|io|news|info|gov|edu|tv|uk|us|de|fr|ru|cn|jp|ca|au))\b",
    re.I,
)


def _parse_rsp(html: str) -> Iterable[dict]:
    """Heuristic: split on table rows / list items, find a status phrase + a domain token."""
    blocks = re.split(r"<(?:tr|li)[^>]*>", html)
    for block in blocks:
        text = re.sub(r"<[^>]+>", " ", block)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        lower = text.lower()
        status = None
        for s in RSP_STATUS_TO_SCORE:
            if s in lower:
                status = s
                break
        if not status:
            continue
        m = RSP_DOMAIN_HINT.search(text)
        if not m:
            continue
        domain = _normalize_domain(m.group(1))
        if not domain:
            continue
        yield {
            "domain": domain,
            "credibility": RSP_STATUS_TO_SCORE[status],
            "bias_score": None,
            "category": status.replace(" ", "-"),
            "source": "rsp",
            "notes": text[:200],
        }


def _upsert(records: Iterable[dict]) -> tuple[int, int]:
    added = updated = 0
    now = datetime.now(timezone.utc)
    for rec in records:
        domain = rec["domain"]
        existing = db.session.query(DomainCredibility).filter_by(domain=domain).first()
        if existing:
            srcs = set(filter(None, (existing.sources or "").split(",")))
            srcs.add(rec["source"])
            existing.credibility = round((existing.credibility + rec["credibility"]) / 2, 3)
            if rec.get("bias_score") and not existing.bias_score:
                existing.bias_score = rec["bias_score"]
            if rec.get("category") and not existing.category:
                existing.category = rec["category"]
            existing.sources = ",".join(sorted(srcs))
            existing.last_updated = now
            updated += 1
        else:
            row = DomainCredibility(
                domain=domain,
                credibility=round(rec["credibility"], 3),
                bias_score=rec.get("bias_score"),
                category=rec.get("category"),
                sources=rec["source"],
                notes=rec.get("notes", ""),
                last_updated=now,
            )
            db.session.add(row)
            added += 1
        if (added + updated) % 500 == 0:
            db.session.commit()
    db.session.commit()
    return added, updated


def main():
    app = create_app()
    with app.app_context():
        db.create_all()  # ensure domain_credibility table exists

        try:
            log.info("Fetching CRED-1 dataset...")
            text = _fetch(CRED1_URL)
            recs = list(_parse_cred1(text))
            a, u = _upsert(recs)
            log.info("CRED-1: %d records, +%d added, ~%d updated", len(recs), a, u)
        except Exception as e:
            log.error("CRED-1 fetch/parse failed: %s", e)

        try:
            log.info("Fetching Wikipedia RSP...")
            html = _fetch(RSP_URL, accept="text/html")
            recs = list(_parse_rsp(html))
            seen = {}
            for r in recs:
                seen[r["domain"]] = r
            recs = list(seen.values())
            a, u = _upsert(recs)
            log.info("RSP: %d records, +%d added, ~%d updated", len(recs), a, u)
        except Exception as e:
            log.error("RSP fetch/parse failed: %s", e)

        total_rows = db.session.query(DomainCredibility).count()
        log.info("Done. domain_credibility now has %d rows.", total_rows)


if __name__ == "__main__":
    main()
