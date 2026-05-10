#!/usr/bin/env python3
"""Train the v1 Curator source-quality classifier.

v1 architecture: TF-IDF over per-domain article samples (titles + leads)
  → 3 sklearn LogisticRegression heads:
    - block_likelihood (0..1)
    - sponsor_signal   (0..1)
    - credibility      (1..10, scaled to 0..1 internally)

Lightweight: trains in seconds, runs in <1ms inference, no torch dep.
ModernBERT upgrade can swap the feature step later without changing
the score_domain() consumer API.

Training labels:
  - profoundd.utils.models.DomainCredibility (2,659 seeded rows from
    CRED-1 + Wikipedia RSP) → primary credibility + block labels
  - SiteSetting('blocked_domains') → strong positive for block
  - profoundd.utils.models.Source.sponsor_tags → sponsor signal positives

Output:
  /app/data/curator_classifier.joblib

Usage:
  docker exec -e PYTHONPATH=/app profoundd python3 \
      /app/scripts/train_curator_classifier.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from profoundd.app import create_app
from profoundd.utils.models import db, SiteSetting, Source, AdminRankingAction, CuratorProposal


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


MODEL_PATH = "/app/data/curator_classifier.joblib"
SAMPLES_PER_DOMAIN = 5         # ES articles to embed per domain
MIN_SAMPLES_PER_DOMAIN = 1     # skip domains with 0 articles
MAX_VOCAB = 20_000             # TF-IDF vocab cap


def _domain_of(url: str) -> str | None:
    """Extract apex domain from a URL string."""
    if not url:
        return None
    d = url.lower().strip()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    if d.startswith("www."):
        d = d[4:]
    d = d.split("/")[0].split("?")[0].split("#")[0]
    return d or None


def load_labels(app):
    """Build (domain → label dict) from USER-OWNED editorial signals only.

    Deliberately excludes external datasets (CRED-1, Wikipedia RSP) since
    those import mainstream gatekeeper bias the editorial constitution is
    specifically designed to escape. The classifier learns from labels
    the user has personally generated:

      1. Source.credibility (1-10)       — user-set per RSS feed
      2. Source.is_active=False          — manually deactivated feeds
      3. Source.sponsor_tags             — user-set sponsor positives
      4. SiteSetting('blocked_domains')  — explicit blocks
      5. AdminRankingAction              — promote/demote history
      6. CuratorProposal applied/rejected — user verdicts on past proposals

    Each signal contributes to one or more of:
      credibility (0.0-1.0)  - mapped to 1-10 at train time
      block       (0/1)
      sponsor     (0/1)
    """
    with app.app_context():
        labels: dict[str, dict] = {}

        def _ensure(d):
            if d not in labels:
                labels[d] = {"credibility": 0.5, "block": 0, "sponsor": 0,
                             "category": "", "sources": []}

        # 1. Source table — primary user-curated labels.
        # Map credibility 1-10 -> 0.1-1.0. Inactive sources flagged for block.
        sources = db.session.query(Source).all()
        for s in sources:
            domain = _domain_of(s.url)
            if not domain:
                continue
            _ensure(domain)
            cred_normalized = max(0.05, min(1.0, (s.credibility or 5) / 10.0))
            labels[domain]["credibility"] = cred_normalized
            labels[domain]["sources"].append("source_table")
            if not s.is_active:
                # Manually deactivated → strong block signal
                labels[domain]["block"] = 1
                labels[domain]["sources"].append("source_deactivated")
            if s.sponsor_tags:
                labels[domain]["sponsor"] = 1
                labels[domain]["sources"].append("sponsor_tags")
            if s.category:
                labels[domain]["category"] = s.category
        log.info("Source-table labels: %d", len(labels))

        # 2. blocked_domains SiteSetting — hard block positives.
        blocked_raw = SiteSetting.get("blocked_domains", "")
        n_blocked_added = 0
        for d in [s.strip().lower() for s in blocked_raw.replace("\r", "").split("\n") if s.strip()]:
            _ensure(d)
            labels[d]["block"] = 1
            labels[d]["credibility"] = min(labels[d]["credibility"], 0.2)
            labels[d]["sources"].append("blocked_domains")
            n_blocked_added += 1
        log.info("blocked_domains signals: %d (total labels now %d)", n_blocked_added, len(labels))

        # 3. AdminRankingAction history — promote/demote signals.
        # Each action targets an article URL; we aggregate by domain.
        try:
            actions = db.session.query(AdminRankingAction).all()
            promote_counts: dict[str, int] = {}
            demote_counts: dict[str, int] = {}
            for a in actions:
                domain = _domain_of(getattr(a, "article_url", "") or getattr(a, "url", ""))
                if not domain:
                    continue
                act = (getattr(a, "action", "") or "").lower()
                if act == "promote":
                    promote_counts[domain] = promote_counts.get(domain, 0) + 1
                elif act == "demote":
                    demote_counts[domain] = demote_counts.get(domain, 0) + 1
            # Convert to per-domain credibility nudges
            for d in set(list(promote_counts.keys()) + list(demote_counts.keys())):
                p = promote_counts.get(d, 0)
                dm = demote_counts.get(d, 0)
                net = p - dm
                if net == 0:
                    continue
                _ensure(d)
                # Nudge credibility by 0.05 per net action, clamp to [0.05, 1.0]
                cur = labels[d]["credibility"]
                labels[d]["credibility"] = max(0.05, min(1.0, cur + 0.05 * net))
                labels[d]["sources"].append(f"admin_rank({p}+,{dm}-)")
                if dm >= 3 and dm > p * 2:
                    labels[d]["block"] = 1
                    labels[d]["sources"].append("admin_rank_demote_heavy")
            log.info("AdminRankingAction signals on %d domains", len(set(list(promote_counts.keys()) + list(demote_counts.keys()))))
        except Exception as e:
            log.warning("AdminRankingAction lookup failed: %s", e)

        # 4. CuratorProposal verdicts — user-confirmed positives/negatives.
        try:
            applied = db.session.query(CuratorProposal).filter_by(status="applied").all()
            rejected = db.session.query(CuratorProposal).filter_by(status="rejected").all()
            for p in applied:
                d = (p.target_domain or "").lower().strip()
                if not d:
                    continue
                _ensure(d)
                if p.proposal_type == "block_domain":
                    labels[d]["block"] = 1
                    labels[d]["credibility"] = min(labels[d]["credibility"], 0.15)
                    labels[d]["sources"].append("curator_block_applied")
                elif p.proposal_type == "sponsor_tag":
                    labels[d]["sponsor"] = 1
                    labels[d]["sources"].append("curator_sponsor_applied")
                elif p.proposal_type == "credibility_adjust":
                    try:
                        new_cred = int(p.proposed_value) / 10.0
                        labels[d]["credibility"] = new_cred
                        labels[d]["sources"].append("curator_cred_applied")
                    except (ValueError, TypeError):
                        pass
            for p in rejected:
                d = (p.target_domain or "").lower().strip()
                if not d:
                    continue
                _ensure(d)
                # User REJECTED a block proposal → treat as positive signal
                # for credibility (the user disagreed that this domain should be blocked).
                if p.proposal_type == "block_domain":
                    labels[d]["block"] = 0
                    labels[d]["credibility"] = max(labels[d]["credibility"], 0.6)
                    labels[d]["sources"].append("curator_block_rejected")
            log.info("CuratorProposal verdicts: %d applied / %d rejected", len(applied), len(rejected))
        except Exception as e:
            log.warning("CuratorProposal lookup failed: %s", e)

        log.info("Total user-labeled domains: %d", len(labels))
        # Quick distribution snapshot
        cred_buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, ">=0.7": 0}
        for d, lbl in labels.items():
            c = lbl["credibility"]
            if c < 0.3: cred_buckets["<0.3"] += 1
            elif c < 0.5: cred_buckets["0.3-0.5"] += 1
            elif c < 0.7: cred_buckets["0.5-0.7"] += 1
            else: cred_buckets[">=0.7"] += 1
        log.info("Credibility distribution: %s", cred_buckets)
        log.info("Block positives: %d", sum(1 for l in labels.values() if l["block"] == 1))
        log.info("Sponsor positives: %d", sum(1 for l in labels.values() if l["sponsor"] == 1))

        return labels


def fetch_samples(domain, es_url, n=SAMPLES_PER_DOMAIN):
    """Pull n article samples (title + lead 200 chars) from ES for a domain."""
    import requests
    try:
        r = requests.get(
            f"{es_url}/profoundd_articles/_search",
            json={
                "size": n,
                "query": {"wildcard": {"url": f"*{domain}*"}},
                "_source": ["title", "summary", "content"],
            },
            timeout=8,
        )
        if r.status_code != 200:
            return []
        hits = r.json().get("hits", {}).get("hits", [])
        out = []
        for h in hits:
            src = h.get("_source", {})
            title = src.get("title") or ""
            lead = (src.get("summary") or src.get("content") or "")[:200]
            if title or lead:
                out.append((title + " — " + lead).strip())
        return out
    except Exception as e:
        log.debug("ES sample fetch failed for %s: %s", domain, e)
        return []


def build_corpus(labels, es_url):
    """For each labeled domain, pull article samples and concatenate.
    Returns (X_text, y_block, y_sponsor, y_cred, kept_domains)."""
    X_text = []
    y_block = []
    y_sponsor = []
    y_cred = []
    kept = []

    progress = 0
    total = len(labels)
    for domain, lbl in labels.items():
        progress += 1
        if progress % 250 == 0:
            log.info("  fetched samples for %d/%d domains", progress, total)
        samples = fetch_samples(domain, es_url)
        if len(samples) < MIN_SAMPLES_PER_DOMAIN:
            # No content in our index — skip rather than train on the
            # domain string alone (which the TF-IDF would memorize).
            continue
        # Concatenate samples into one feature blob; TF-IDF will tokenize.
        text = " \n ".join(samples)
        X_text.append(text)
        y_block.append(int(lbl["block"]))
        y_sponsor.append(int(lbl["sponsor"]))
        # Convert credibility 0..1 to 1..10
        y_cred.append(round(lbl["credibility"] * 10))
        kept.append(domain)

    log.info("Built corpus: %d domains with samples (out of %d labeled)", len(kept), total)
    return X_text, np.array(y_block), np.array(y_sponsor), np.array(y_cred), kept


def train_heads(X_text, y_block, y_sponsor, y_cred):
    """Train TF-IDF vectorizer + 3 LR heads. Return dict of artifacts."""
    log.info("Vectorizing (TF-IDF, max_features=%d)...", MAX_VOCAB)
    vectorizer = TfidfVectorizer(
        max_features=MAX_VOCAB,
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.95,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    X = vectorizer.fit_transform(X_text)
    log.info("Feature matrix: %s", X.shape)

    artifacts = {"vectorizer": vectorizer, "heads": {}}

    # Block head — binary
    log.info("Training block head...")
    Xtr, Xte, ytr, yte = train_test_split(X, y_block, test_size=0.2, random_state=42, stratify=y_block if len(set(y_block)) > 1 else None)
    block_clf = LogisticRegression(max_iter=400, class_weight="balanced", C=1.0)
    block_clf.fit(Xtr, ytr)
    log.info("Block head report:\n%s", classification_report(yte, block_clf.predict(Xte), zero_division=0))
    artifacts["heads"]["block"] = block_clf

    # Sponsor head — binary, often very imbalanced
    sponsor_pos = int(y_sponsor.sum())
    log.info("Training sponsor head (positive=%d / %d)...", sponsor_pos, len(y_sponsor))
    if sponsor_pos >= 5:
        Xtr, Xte, ytr, yte = train_test_split(X, y_sponsor, test_size=0.2, random_state=42, stratify=y_sponsor if sponsor_pos >= 5 else None)
        sponsor_clf = LogisticRegression(max_iter=400, class_weight="balanced", C=1.0)
        sponsor_clf.fit(Xtr, ytr)
        log.info("Sponsor head report:\n%s", classification_report(yte, sponsor_clf.predict(Xte), zero_division=0))
        artifacts["heads"]["sponsor"] = sponsor_clf
    else:
        log.warning("Too few sponsor positives (%d); skipping sponsor head. Will return 0.0 always.", sponsor_pos)
        artifacts["heads"]["sponsor"] = None

    # Credibility — multiclass 1..10 (treat as ordinal-ish)
    log.info("Training credibility head (10 classes)...")
    Xtr, Xte, ytr, yte = train_test_split(X, y_cred, test_size=0.2, random_state=42)
    cred_clf = LogisticRegression(max_iter=400, multi_class="multinomial", C=1.0)
    cred_clf.fit(Xtr, ytr)
    log.info("Credibility head accuracy: %.3f", cred_clf.score(Xte, yte))
    log.info("Credibility classes: %s", cred_clf.classes_.tolist())
    artifacts["heads"]["credibility"] = cred_clf

    return artifacts


def save(artifacts, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(artifacts, path)
    log.info("Saved classifier bundle to %s (%.1f MB)",
             path, os.path.getsize(path) / 1024 / 1024)


def main():
    es_url = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
    app = create_app()
    log.info("Loading labels...")
    labels = load_labels(app)
    log.info("Building corpus from ES samples (es=%s)...", es_url)
    X_text, y_block, y_sponsor, y_cred, kept = build_corpus(labels, es_url)
    if not X_text:
        log.error("No training data assembled. Bail.")
        return 1
    artifacts = train_heads(X_text, y_block, y_sponsor, y_cred)
    artifacts["domains_seen"] = kept[:]
    artifacts["version"] = "v1-tfidf-lr"
    save(artifacts, MODEL_PATH)

    # Quick sanity: score 5 known-bad and 5 known-good domains.
    log.info("Sanity-check predictions:")
    sample_domains = []
    for d, lbl in labels.items():
        if lbl["block"] == 1 and len(sample_domains) < 5:
            sample_domains.append((d, "block"))
    for d, lbl in labels.items():
        if lbl["credibility"] >= 0.7 and len(sample_domains) < 10:
            sample_domains.append((d, "good"))

    vec = artifacts["vectorizer"]
    block_clf = artifacts["heads"]["block"]
    cred_clf = artifacts["heads"]["credibility"]
    for d, expected in sample_domains:
        if d not in kept:
            continue
        idx = kept.index(d)
        v = vec.transform([X_text[idx]])
        block_p = block_clf.predict_proba(v)[0]
        block_score = block_p[list(block_clf.classes_).index(1)] if 1 in block_clf.classes_ else 0.0
        cred_pred = int(cred_clf.predict(v)[0])
        log.info("  %-35s expected=%-6s block=%.2f cred=%d", d, expected, block_score, cred_pred)
    return 0


if __name__ == "__main__":
    sys.exit(main())
