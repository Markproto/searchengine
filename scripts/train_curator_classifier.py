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
from profoundd.utils.models import db, DomainCredibility, SiteSetting, Source


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


MODEL_PATH = "/app/data/curator_classifier.joblib"
SAMPLES_PER_DOMAIN = 5         # ES articles to embed per domain
MIN_SAMPLES_PER_DOMAIN = 1     # skip domains with 0 articles
MAX_VOCAB = 20_000             # TF-IDF vocab cap


def load_labels(app):
    """Build (domain → label dict) from DB sources of truth."""
    with app.app_context():
        labels = {}

        # 1. DomainCredibility — the primary credibility label.
        # Map 0.0-1.0 score → block/cred/etc.
        rows = db.session.query(DomainCredibility).all()
        for r in rows:
            cred = r.credibility or 0.5
            labels[r.domain] = {
                "credibility": cred,
                "block": 1 if cred < 0.25 else 0,
                "sponsor": 0,  # default; overridden below
                "category": r.category or "",
            }
        log.info("DomainCredibility rows: %d", len(labels))

        # 2. blocked_domains SiteSetting — hard positives for block head.
        blocked_raw = SiteSetting.get("blocked_domains", "")
        for d in [s.strip().lower() for s in blocked_raw.replace("\r", "").split("\n") if s.strip()]:
            if d not in labels:
                labels[d] = {"credibility": 0.1, "block": 1, "sponsor": 0, "category": "user_blocked"}
            else:
                labels[d]["block"] = 1
                labels[d]["credibility"] = min(labels[d]["credibility"], 0.2)
        log.info("After blocked_domains merge: %d labels", len(labels))

        # 3. Source.sponsor_tags — positives for sponsor head.
        sources = db.session.query(Source).filter(Source.sponsor_tags != "").filter(Source.sponsor_tags.isnot(None)).all()
        for s in sources:
            domain = (s.url or "").lower()
            for prefix in ("https://", "http://", "www."):
                if domain.startswith(prefix):
                    domain = domain[len(prefix):]
            domain = domain.split("/")[0]
            if not domain:
                continue
            if domain not in labels:
                labels[domain] = {"credibility": 0.5, "block": 0, "sponsor": 1, "category": "sponsored"}
            else:
                labels[domain]["sponsor"] = 1
        log.info("After sponsor_tags merge: %d labels", len(labels))

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
