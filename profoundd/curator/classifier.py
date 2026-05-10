"""Runtime interface to the trained Curator source-quality classifier.

Loads the joblib bundle once on first call (lazy). Provides a single
score_domain(domain) function the nightly Curator job + admin tools use.

Returns {"block": float, "sponsor": float, "credibility": int, "n_samples": int}.

When the model file is missing or fails to load, returns neutral
defaults so callers can degrade gracefully (block=0.0, cred=5).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_PATH = os.environ.get("CURATOR_MODEL_PATH", "/app/data/curator_classifier.joblib")

_lock = threading.Lock()
_artifacts: Optional[dict] = None
_load_failed: bool = False


def _load():
    global _artifacts, _load_failed
    if _artifacts is not None or _load_failed:
        return _artifacts
    with _lock:
        if _artifacts is not None or _load_failed:
            return _artifacts
        try:
            import joblib
            if not os.path.exists(MODEL_PATH):
                logger.info("Curator classifier not found at %s; using neutral defaults.", MODEL_PATH)
                _load_failed = True
                return None
            _artifacts = joblib.load(MODEL_PATH)
            logger.info("Loaded Curator classifier %s (vocab=%d)",
                        _artifacts.get("version", "?"),
                        len(_artifacts["vectorizer"].vocabulary_))
        except Exception as e:
            logger.warning("Curator classifier load failed: %s", e)
            _load_failed = True
        return _artifacts


def reload():
    """Force the next score_domain() to re-read the model file (after retraining)."""
    global _artifacts, _load_failed
    with _lock:
        _artifacts = None
        _load_failed = False


def _fetch_samples_es(domain: str, es_url: str = None, n: int = 5) -> list[str]:
    """Fetch up to n recent article (title + lead) samples for the domain."""
    import requests
    es_url = es_url or os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")
    try:
        r = requests.get(
            f"{es_url}/profoundd_articles/_search",
            json={
                "size": n,
                "query": {"wildcard": {"url": f"*{domain}*"}},
                "_source": ["title", "summary", "content"],
                "sort": [{"published_at": {"order": "desc"}}],
            },
            timeout=8,
        )
        if r.status_code != 200:
            return []
        out = []
        for h in r.json().get("hits", {}).get("hits", []):
            src = h.get("_source", {})
            title = src.get("title") or ""
            lead = (src.get("summary") or src.get("content") or "")[:200]
            if title or lead:
                out.append((title + " — " + lead).strip())
        return out
    except Exception as e:
        logger.debug("ES sample fetch failed for %s: %s", domain, e)
        return []


def _neutral():
    return {"block": 0.0, "sponsor": 0.0, "credibility": 5, "n_samples": 0,
            "model_loaded": False}


def score_domain(domain: str, es_url: str = None) -> dict:
    """Score a single domain. Returns dict with block/sponsor/credibility.

    block      : 0..1 likelihood the domain should be blocked
    sponsor    : 0..1 likelihood it has sponsor signals
    credibility: 1..10 predicted credibility
    n_samples  : how many ES articles were used (0 = no signal, neutral output)
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return _neutral()

    artifacts = _load()
    if artifacts is None:
        return _neutral()

    samples = _fetch_samples_es(domain, es_url=es_url)
    if not samples:
        out = _neutral()
        out["model_loaded"] = True
        return out

    text = " \n ".join(samples)
    vec = artifacts["vectorizer"]
    heads = artifacts["heads"]
    X = vec.transform([text])

    out = {"n_samples": len(samples), "model_loaded": True}

    block_clf = heads.get("block")
    if block_clf is not None and 1 in list(block_clf.classes_):
        proba = block_clf.predict_proba(X)[0]
        out["block"] = float(proba[list(block_clf.classes_).index(1)])
    else:
        out["block"] = 0.0

    sponsor_clf = heads.get("sponsor")
    if sponsor_clf is not None and 1 in list(sponsor_clf.classes_):
        proba = sponsor_clf.predict_proba(X)[0]
        out["sponsor"] = float(proba[list(sponsor_clf.classes_).index(1)])
    else:
        out["sponsor"] = 0.0

    cred_clf = heads.get("credibility")
    if cred_clf is not None:
        try:
            out["credibility"] = int(cred_clf.predict(X)[0])
        except Exception:
            out["credibility"] = 5
    else:
        out["credibility"] = 5

    return out


def score_text(text: str) -> dict:
    """Score a free-form text blob (no ES lookup). Used for testing /
    scoring an article directly."""
    artifacts = _load()
    if artifacts is None or not text:
        return _neutral()
    vec = artifacts["vectorizer"]
    heads = artifacts["heads"]
    X = vec.transform([text])
    out = {"n_samples": 1, "model_loaded": True}

    block_clf = heads.get("block")
    if block_clf is not None and 1 in list(block_clf.classes_):
        proba = block_clf.predict_proba(X)[0]
        out["block"] = float(proba[list(block_clf.classes_).index(1)])
    else:
        out["block"] = 0.0

    sponsor_clf = heads.get("sponsor")
    if sponsor_clf is not None and 1 in list(sponsor_clf.classes_):
        proba = sponsor_clf.predict_proba(X)[0]
        out["sponsor"] = float(proba[list(sponsor_clf.classes_).index(1)])
    else:
        out["sponsor"] = 0.0

    cred_clf = heads.get("credibility")
    if cred_clf is not None:
        try:
            out["credibility"] = int(cred_clf.predict(X)[0])
        except Exception:
            out["credibility"] = 5
    else:
        out["credibility"] = 5

    return out
