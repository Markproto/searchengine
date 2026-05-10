"""Curator nightly job — sample recent corpus, score domains, emit proposals.

Runs nightly (03:00 UTC) via APScheduler. For each domain that appeared
in the last 24h of ingest with ≥ MIN_SAMPLES articles, calls the
user-trained classifier (profoundd/curator/classifier.py) and emits
CuratorProposal rows when scores cross thresholds.

Proposals land in /admin/curator-queue for human review. Apply/revert
mechanics live in profoundd/curator/apply.py (Layer-2 rollback).

Tunable via SiteSetting:
  curator_block_threshold     (default 0.70)
  curator_sponsor_threshold   (default 0.70)
  curator_cred_drift_min      (default 2)     — min |new_cred - current| to propose
  curator_min_samples         (default 3)     — articles per domain required
  curator_max_proposals_per_run (default 20)
  curator_skip_known_domains  (default "1")   — if 1, skip Source-table domains
                                                 (don't propose against user's
                                                  own curated feeds)
"""
from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _domain_of(url: str) -> str | None:
    if not url:
        return None
    try:
        d = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    except Exception:
        d = url.lower()
    if d.startswith("www."):
        d = d[4:]
    d = d.split(":")[0]
    return d or None


def _get_setting_int(key: str, default: int) -> int:
    from profoundd.utils.models import SiteSetting
    try:
        return int(SiteSetting.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _get_setting_float(key: str, default: float) -> float:
    from profoundd.utils.models import SiteSetting
    try:
        return float(SiteSetting.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _sample_recent_domains(es_url: str, hours: int = 24, max_domains: int = 200,
                            min_samples: int = 3):
    """Return {domain: article_count} for domains with >= min_samples in last `hours`."""
    import requests
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "size": 0,
        "query": {"range": {"crawled_at": {"gte": cutoff}}},
        "aggs": {
            "by_domain": {
                "terms": {"field": "domain", "size": max_domains}
            }
        }
    }
    try:
        r = requests.get(f"{es_url}/profoundd_articles/_search", json=body, timeout=30)
        if r.status_code != 200:
            logger.warning("ES aggregation failed (status %s): %s", r.status_code, r.text[:200])
            return {}
        buckets = r.json().get("aggregations", {}).get("by_domain", {}).get("buckets", [])
        return {b["key"]: b["doc_count"] for b in buckets if b["doc_count"] >= min_samples}
    except Exception as e:
        # Some ES indices don't have a 'domain' keyword field; fall back to URL scan.
        logger.info("ES domain agg unavailable (%s) — falling back to URL scan", e)

    # Fallback: pull last-24h URLs and aggregate in Python
    body2 = {
        "size": 500,
        "query": {"range": {"crawled_at": {"gte": cutoff}}},
        "_source": ["url"],
    }
    try:
        r = requests.get(f"{es_url}/profoundd_articles/_search", json=body2, timeout=30)
        counter = Counter()
        for hit in r.json().get("hits", {}).get("hits", []):
            d = _domain_of((hit.get("_source") or {}).get("url"))
            if d:
                counter[d] += 1
        return {d: c for d, c in counter.items() if c >= min_samples}
    except Exception as e:
        logger.exception("ES URL-fallback failed: %s", e)
        return {}


def _existing_proposal_states(target_domain: str) -> set:
    """Return set of CuratorProposal.status values for any prior proposal on this domain."""
    from profoundd.utils.models import db, CuratorProposal
    rows = (db.session.query(CuratorProposal.status, CuratorProposal.proposal_type)
            .filter_by(target_domain=target_domain)
            .all())
    return {(s, t) for s, t in rows}


def _current_source_credibility(domain: str) -> int | None:
    """Return Source.credibility (1-10) for a feed matching this domain, or None."""
    from profoundd.utils.models import db, Source
    src = (db.session.query(Source)
           .filter(Source.url.ilike(f"%{domain}%"))
           .first())
    return src.credibility if src else None


def _is_in_user_sources(domain: str) -> bool:
    from profoundd.utils.models import db, Source
    return db.session.query(Source).filter(Source.url.ilike(f"%{domain}%")).count() > 0


def _evidence_urls_for_domain(es_url: str, domain: str, n: int = 3) -> list[str]:
    """Pull up to n recent article URLs for this domain so admin can spot-check the call."""
    import requests
    try:
        r = requests.get(
            f"{es_url}/profoundd_articles/_search",
            json={
                "size": n,
                "query": {"wildcard": {"url": f"*{domain}*"}},
                "_source": ["url"],
                "sort": [{"crawled_at": {"order": "desc"}}],
            },
            timeout=8,
        )
        return [h["_source"]["url"] for h in r.json().get("hits", {}).get("hits", []) if "_source" in h]
    except Exception:
        return []


def run_nightly(app=None, *, hours: int = 24, dry_run: bool = False) -> dict:
    """Score recent-ingest domains via the user-trained classifier and emit
    CuratorProposal rows. Returns a dict summary for logging / admin view."""
    from profoundd.curator import classifier as curator_clf
    from profoundd.utils.models import db, CuratorProposal, SiteSetting

    # If called from inside a Flask context already, reuse it; else require an app
    if app is not None:
        ctx = app.app_context()
        ctx.__enter__()
    try:
        es_url = (os.environ.get("ELASTICSEARCH_URL")
                  or (app.config.get("ELASTICSEARCH_URL") if app else None)
                  or "http://elasticsearch:9200")

        block_thresh = _get_setting_float("curator_block_threshold", 0.70)
        sponsor_thresh = _get_setting_float("curator_sponsor_threshold", 0.70)
        cred_drift_min = _get_setting_int("curator_cred_drift_min", 2)
        min_samples = _get_setting_int("curator_min_samples", 3)
        max_per_run = _get_setting_int("curator_max_proposals_per_run", 20)
        skip_known = SiteSetting.get("curator_skip_known_domains", "1") == "1"

        # 1. Sample recent corpus
        domain_counts = _sample_recent_domains(es_url, hours=hours, min_samples=min_samples)
        logger.info("Curator nightly: %d domains with >=%d articles in last %dh",
                    len(domain_counts), min_samples, hours)

        if not curator_clf._load():
            return {"status": "skipped", "reason": "classifier not loaded",
                    "domains_seen": len(domain_counts), "proposals_emitted": 0}

        summary = {
            "status": "ok",
            "domains_seen": len(domain_counts),
            "domains_scored": 0,
            "skipped_known": 0,
            "skipped_existing_proposal": 0,
            "proposals_emitted": 0,
            "proposed_by_type": defaultdict(int),
            "dry_run": dry_run,
        }

        # Sort by article count desc so prolific domains get scored first
        sorted_domains = sorted(domain_counts.items(), key=lambda x: -x[1])

        for domain, article_count in sorted_domains:
            if summary["proposals_emitted"] >= max_per_run:
                logger.info("Curator: hit per-run cap of %d proposals", max_per_run)
                break

            # Skip user's curated Source feeds unless explicitly enabled
            if skip_known and _is_in_user_sources(domain):
                summary["skipped_known"] += 1
                continue

            score = curator_clf.score_domain(domain, es_url=es_url)
            summary["domains_scored"] += 1
            if not score.get("model_loaded"):
                continue

            existing = _existing_proposal_states(domain)
            # If there's a pending or applied proposal for ANY type, skip the whole domain
            # to avoid noise. Reverted/rejected don't block.
            has_open = any(s in ("pending", "applied") for s, _ in existing)
            if has_open:
                summary["skipped_existing_proposal"] += 1
                continue

            evidence = _evidence_urls_for_domain(es_url, domain, n=3)
            evidence_str = "\n".join(evidence)

            # ---------- 1. Block proposal ----------
            if score["block"] >= block_thresh:
                # Skip if we previously rejected a block for this domain (user said it's fine)
                if any(t == "block_domain" and s == "rejected" for s, t in existing):
                    continue
                if not dry_run:
                    p = CuratorProposal(
                        proposal_type="block_domain",
                        target_domain=domain,
                        proposed_value=domain,
                        reasoning=(
                            f"Classifier scored block={score['block']:.2f} "
                            f"on {score['n_samples']} sample article(s) over the last {hours}h. "
                            f"Predicted credibility {score['credibility']}/10."
                        ),
                        evidence_urls=evidence_str,
                        confidence=score["block"],
                        status="pending",
                    )
                    db.session.add(p)
                summary["proposals_emitted"] += 1
                summary["proposed_by_type"]["block_domain"] += 1
                continue  # block supersedes other proposals for this domain

            # ---------- 2. Sponsor proposal ----------
            if score["sponsor"] >= sponsor_thresh:
                if any(t == "sponsor_tag" and s == "rejected" for s, t in existing):
                    pass  # don't propose again
                else:
                    if not dry_run:
                        p = CuratorProposal(
                            proposal_type="sponsor_tag",
                            target_domain=domain,
                            proposed_value="Suspected sponsor",
                            reasoning=(
                                f"Classifier sponsor_signal={score['sponsor']:.2f} "
                                f"on {score['n_samples']} sample article(s). Admin should "
                                f"confirm specific sponsor name before applying."
                            ),
                            evidence_urls=evidence_str,
                            confidence=score["sponsor"],
                            status="pending",
                        )
                        db.session.add(p)
                    summary["proposals_emitted"] += 1
                    summary["proposed_by_type"]["sponsor_tag"] += 1

            # ---------- 3. Credibility-adjust proposal ----------
            current = _current_source_credibility(domain)
            predicted = score["credibility"]
            if current is not None and abs(predicted - current) >= cred_drift_min:
                if any(t == "credibility_adjust" and s == "rejected" for s, t in existing):
                    continue
                if not dry_run:
                    p = CuratorProposal(
                        proposal_type="credibility_adjust",
                        target_domain=domain,
                        proposed_value=str(predicted),
                        before_value=str(current),
                        reasoning=(
                            f"Classifier predicts credibility {predicted}/10 for content "
                            f"recently crawled from {domain}; current Source rating is "
                            f"{current}/10 (drift={predicted - current:+d}). "
                            f"Sampled {score['n_samples']} article(s)."
                        ),
                        evidence_urls=evidence_str,
                        confidence=abs(predicted - current) / 10.0,
                        status="pending",
                    )
                    db.session.add(p)
                summary["proposals_emitted"] += 1
                summary["proposed_by_type"]["credibility_adjust"] += 1

        if not dry_run:
            db.session.commit()

        summary["proposed_by_type"] = dict(summary["proposed_by_type"])
        logger.info("Curator nightly summary: %s", summary)
        return summary
    finally:
        if app is not None:
            ctx.__exit__(None, None, None)
