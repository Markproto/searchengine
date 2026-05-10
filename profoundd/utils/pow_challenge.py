"""Proof-of-Work human-verification challenge.

Stateless server-side: challenges are HMAC-bound to (ip_hash, target_path,
5-minute time bucket) using SECRET_KEY. No DB row per challenge — verify
by recomputing.

Browser solves SHA-256 puzzle: find nonce N such that
    sha256(challenge_hex || N).digest()
has at least `difficulty` leading zero bits.

Once solved, the visitor's Flask session gets a `human_verified` token
good for `VERIFY_TTL_SECONDS` and `MAX_DOWNLOADS_PER_SESSION` downloads.

Reused by ALL gated download routes via the enforce_human_gate middleware.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

# Reasonable bounds enforced even if SiteSetting is misconfigured.
DIFFICULTY_MIN = 12
DIFFICULTY_MAX = 26
DIFFICULTY_DEFAULT = 18  # ~250 ms median solve on modern desktop

CHALLENGE_BUCKET_SECONDS = 300       # 5-min HMAC time bucket (challenge validity window)
VERIFY_TTL_SECONDS = 3600            # 1 hour after solve
MAX_DOWNLOADS_PER_SESSION = 50       # then re-challenge


def _secret() -> bytes:
    """Resolve SECRET_KEY for HMAC. Falls back to a stable per-process value."""
    sk = os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or ""
    if not sk:
        # Last resort: use machine-id so HMAC at least survives within one boot.
        sk = "profoundd-fallback-secret-please-set-SECRET_KEY"
    return sk.encode("utf-8") if isinstance(sk, str) else sk


def _bucket(now: Optional[float] = None) -> int:
    """Current HMAC time bucket (5-minute granularity)."""
    now = now if now is not None else time.time()
    return int(now // CHALLENGE_BUCKET_SECONDS)


def _challenge_string(ip_hash: str, target_path: str, bucket: int) -> str:
    """Deterministic 16-hex challenge for (ip, target, bucket)."""
    msg = f"{ip_hash}|{target_path}|{bucket}".encode("utf-8")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:16]


def issue_challenge(ip_hash: str, target_path: str, difficulty: int) -> dict:
    """Build a challenge dict to send to the client.

    Client receives {challenge, difficulty, target, issued_at}. It does not
    need to remember any of these between requests — the POST verify route
    accepts them back and re-derives the HMAC.
    """
    difficulty = max(DIFFICULTY_MIN, min(DIFFICULTY_MAX, int(difficulty)))
    now = time.time()
    bucket = _bucket(now)
    return {
        "challenge": _challenge_string(ip_hash, target_path, bucket),
        "difficulty": difficulty,
        "target": target_path,
        "issued_at": int(now),
    }


def _count_leading_zero_bits(b: bytes) -> int:
    """Count leading zero BITS in the given byte string."""
    count = 0
    for byte in b:
        if byte == 0:
            count += 8
            continue
        # bit_length: minimum bits to represent the byte (1..8). Leading zeros = 8 - bit_length.
        count += 8 - byte.bit_length()
        break
    return count


def verify_nonce(ip_hash: str, target_path: str, challenge: str, nonce: str,
                 difficulty: int) -> tuple[bool, str]:
    """Check if `nonce` solves the challenge at the requested difficulty.

    Returns (ok, reason). Reason is a short tag for logging (e.g. "stale",
    "wrong_challenge", "low_bits").
    """
    if not isinstance(challenge, str) or not isinstance(nonce, str):
        return False, "bad_input"
    if len(nonce) > 32 or not nonce.isprintable():
        return False, "bad_nonce"

    # Allow current bucket + previous bucket (covers the case where the
    # challenge was issued just before a 5-min boundary)
    now = time.time()
    valid = False
    for bucket in (_bucket(now), _bucket(now) - 1):
        expected = _challenge_string(ip_hash, target_path, bucket)
        if hmac.compare_digest(expected, challenge):
            valid = True
            break
    if not valid:
        return False, "stale_or_forged"

    sha = hashlib.sha256((challenge + nonce).encode("utf-8")).digest()
    bits = _count_leading_zero_bits(sha)
    if bits < int(difficulty):
        return False, f"low_bits({bits}<{difficulty})"
    return True, "ok"


def mark_verified(session) -> str:
    """Stamp the Flask session as human-verified. Returns the issued token."""
    issued = int(time.time())
    # Token bound to session SID (session.sid not always present; fall back to
    # a per-session random already provided by Flask's signed cookie).
    sid = str(session.get("_id") or session.get("_fresh") or "anon") + ":" + str(issued)
    token = hmac.new(_secret(), sid.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    session["human_verified"] = token
    session["human_verified_at"] = issued
    session["human_downloads_used"] = 0
    return token


def is_verified(session) -> bool:
    """True if session has an unexpired human_verified token + downloads quota left."""
    token = session.get("human_verified")
    issued = session.get("human_verified_at", 0)
    used = session.get("human_downloads_used", 0)
    if not token or not issued:
        return False
    if time.time() - issued > VERIFY_TTL_SECONDS:
        return False
    if used >= MAX_DOWNLOADS_PER_SESSION:
        return False
    return True


def increment_download_count(session) -> int:
    """Increment per-session download counter. Returns new count."""
    n = int(session.get("human_downloads_used", 0)) + 1
    session["human_downloads_used"] = n
    return n


def invalidate_session(session) -> None:
    """Forcibly clear human_verified state (e.g., admin "re-issue all")."""
    for k in ("human_verified", "human_verified_at", "human_downloads_used"):
        session.pop(k, None)
