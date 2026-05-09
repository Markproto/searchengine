"""Apply / revert mechanics for CuratorProposal.

Each apply captures before_value at mutation time so revert is exact.
All mutations are recorded in CuratorAuditLog (append-only).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from profoundd.utils.models import (
    db, CuratorProposal, CuratorAuditLog, SiteSetting, Source,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _audit(proposal: CuratorProposal, action: str, before: str, after: str, actor: str):
    """Append an audit row. Must be called from within an active transaction."""
    db.session.add(CuratorAuditLog(
        proposal_id=proposal.id,
        action=action,
        before_value=before,
        after_value=after,
        actor=actor,
    ))


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# block_domain — append target_domain to SiteSetting("blocked_domains")
# ---------------------------------------------------------------------------

def _apply_block_domain(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    cur = SiteSetting.get("blocked_domains", "")
    parts = [s.strip() for s in cur.replace("\r", "").split("\n") if s.strip()]
    if p.target_domain.lower() in [s.lower() for s in parts]:
        return False, f"{p.target_domain} already blocked"
    p.before_value = cur
    parts.append(p.target_domain)
    new_value = "\n".join(parts)
    SiteSetting.set("blocked_domains", new_value)
    _audit(p, "apply", cur, new_value, actor)
    return True, f"Blocked {p.target_domain}"


def _revert_block_domain(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    cur = SiteSetting.get("blocked_domains", "")
    parts = [s.strip() for s in cur.replace("\r", "").split("\n") if s.strip()]
    parts = [s for s in parts if s.lower() != p.target_domain.lower()]
    new_value = "\n".join(parts)
    SiteSetting.set("blocked_domains", new_value)
    _audit(p, "revert", cur, new_value, actor)
    return True, f"Unblocked {p.target_domain}"


# ---------------------------------------------------------------------------
# unblock_domain — inverse, used by Curator if it changes its mind
# ---------------------------------------------------------------------------

def _apply_unblock_domain(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    return _revert_block_domain(p, actor)


def _revert_unblock_domain(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    return _apply_block_domain(p, actor)


# ---------------------------------------------------------------------------
# sponsor_tag — update Source.sponsor_tags for matching source(s)
# ---------------------------------------------------------------------------

def _apply_sponsor_tag(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    sources = _sources_for_domain(p.target_domain, p.target_source_id)
    if not sources:
        return False, f"No Source row matches {p.target_domain}"
    befores = []
    for s in sources:
        befores.append(f"{s.id}:{s.sponsor_tags or ''}")
        existing = set(filter(None, (s.sponsor_tags or "").split(",")))
        existing.add(p.proposed_value.strip())
        s.sponsor_tags = ",".join(sorted(existing))
    p.before_value = "|".join(befores)
    _audit(p, "apply", p.before_value, p.proposed_value, actor)
    return True, f"Tagged {len(sources)} source(s) as sponsored by {p.proposed_value}"


def _revert_sponsor_tag(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    if not p.before_value:
        return False, "no before_value to revert from"
    parts = [b for b in p.before_value.split("|") if ":" in b]
    revert_count = 0
    for b in parts:
        sid, _, tags = b.partition(":")
        s = db.session.query(Source).get(int(sid))
        if s:
            s.sponsor_tags = tags
            revert_count += 1
    _audit(p, "revert", p.proposed_value, p.before_value, actor)
    return True, f"Reverted sponsor_tags on {revert_count} source(s)"


# ---------------------------------------------------------------------------
# credibility_adjust — set Source.credibility (1-10) for matching source(s)
# ---------------------------------------------------------------------------

def _apply_credibility_adjust(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    sources = _sources_for_domain(p.target_domain, p.target_source_id)
    if not sources:
        return False, f"No Source row matches {p.target_domain}"
    try:
        new_cred = int(p.proposed_value)
    except ValueError:
        return False, f"proposed_value must be int 1-10, got {p.proposed_value!r}"
    new_cred = max(1, min(10, new_cred))
    befores = []
    for s in sources:
        befores.append(f"{s.id}:{s.credibility}")
        s.credibility = new_cred
    p.before_value = "|".join(befores)
    _audit(p, "apply", p.before_value, str(new_cred), actor)
    return True, f"Set credibility={new_cred} on {len(sources)} source(s)"


def _revert_credibility_adjust(p: CuratorProposal, actor: str) -> tuple[bool, str]:
    if not p.before_value:
        return False, "no before_value to revert from"
    parts = [b for b in p.before_value.split("|") if ":" in b]
    revert_count = 0
    for b in parts:
        sid, _, cred = b.partition(":")
        try:
            cred_int = int(cred)
        except ValueError:
            continue
        s = db.session.query(Source).get(int(sid))
        if s:
            s.credibility = cred_int
            revert_count += 1
    _audit(p, "revert", p.proposed_value, p.before_value, actor)
    return True, f"Reverted credibility on {revert_count} source(s)"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_APPLY_HANDLERS = {
    "block_domain": _apply_block_domain,
    "unblock_domain": _apply_unblock_domain,
    "sponsor_tag": _apply_sponsor_tag,
    "credibility_adjust": _apply_credibility_adjust,
}

_REVERT_HANDLERS = {
    "block_domain": _revert_block_domain,
    "unblock_domain": _revert_unblock_domain,
    "sponsor_tag": _revert_sponsor_tag,
    "credibility_adjust": _revert_credibility_adjust,
}


def _sources_for_domain(domain: str, explicit_id: int | None):
    if explicit_id:
        s = db.session.query(Source).get(explicit_id)
        return [s] if s else []
    # Match by URL containing apex domain
    domain = domain.lower().strip()
    if not domain:
        return []
    return db.session.query(Source).filter(Source.url.ilike(f"%{domain}%")).all()


def apply_proposal(proposal_id: int, actor: str = "admin") -> tuple[bool, str]:
    """Apply a pending/approved proposal. Returns (ok, message)."""
    p = db.session.query(CuratorProposal).get(proposal_id)
    if not p:
        return False, "proposal not found"
    if p.status not in ("pending", "approved"):
        return False, f"proposal status is {p.status}, cannot apply"
    handler = _APPLY_HANDLERS.get(p.proposal_type)
    if not handler:
        return False, f"unknown proposal_type {p.proposal_type}"
    ok, msg = handler(p, actor)
    if ok:
        p.status = "applied"
        p.applied_at = _now()
        p.reviewed_at = _now()
        p.reviewed_by = actor
        db.session.commit()
    else:
        db.session.rollback()
    return ok, msg


def revert_proposal(proposal_id: int, actor: str = "admin") -> tuple[bool, str]:
    """Undo a previously-applied proposal."""
    p = db.session.query(CuratorProposal).get(proposal_id)
    if not p:
        return False, "proposal not found"
    if p.status != "applied":
        return False, f"proposal status is {p.status}, cannot revert"
    handler = _REVERT_HANDLERS.get(p.proposal_type)
    if not handler:
        return False, f"unknown proposal_type {p.proposal_type}"
    ok, msg = handler(p, actor)
    if ok:
        p.status = "reverted"
        p.reverted_at = _now()
        db.session.commit()
    else:
        db.session.rollback()
    return ok, msg


def reject_proposal(proposal_id: int, actor: str = "admin") -> tuple[bool, str]:
    p = db.session.query(CuratorProposal).get(proposal_id)
    if not p:
        return False, "proposal not found"
    if p.status not in ("pending", "approved"):
        return False, f"proposal status is {p.status}"
    p.status = "rejected"
    p.reviewed_at = _now()
    p.reviewed_by = actor
    db.session.commit()
    return True, "rejected"
