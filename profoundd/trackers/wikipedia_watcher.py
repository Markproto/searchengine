"""Wikipedia revision watcher for tracked public figures.

Weekly cron walks every active TrackedFigure, queries Wikipedia's
revisions API for edits since the last-seen revid, aggregates the
edits into a single WikipediaChange row, and calls the LLM (Hermes3
on Tark1 by default, with the editorial constitution prepended) to
explain the change for users.

Wikipedia API docs:
  https://www.mediawiki.org/wiki/API:Revisions
  https://www.mediawiki.org/wiki/API:Compare

Endpoint shape:
  GET https://en.wikipedia.org/w/api.php
      ?action=query
      &prop=revisions
      &titles=Gavin_Newsom
      &rvprop=ids|timestamp|user|comment|size
      &rvlimit=50
      &rvend=<last-seen-timestamp>
      &format=json
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = "ProfounddWatcher/1.0 (contact: mark.hutto@protonmail.com)"

# Per-figure budget: don't load more than this many revisions in one run.
MAX_REVISIONS_PER_CHECK = 100
# How long to wait between API calls — Wikipedia's etiquette: max 200 req/min,
# but for a low-volume task like this 0.5s between calls is plenty.
API_SLEEP_SECONDS = 0.5


def _api_get(params: dict, timeout: int = 15) -> dict:
    """Wrapper around the Wikipedia API GET. Honours the user-agent etiquette."""
    headers = {"User-Agent": WIKI_UA, "Accept": "application/json"}
    full = dict(params)
    full.setdefault("format", "json")
    full.setdefault("formatversion", "2")
    resp = requests.get(WIKI_API, params=full, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fetch_revisions(title: str, since_revid: int | None) -> list[dict]:
    """Return list of revision dicts newer than since_revid, oldest-first."""
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": title,
        "rvprop": "ids|timestamp|user|comment|size",
        "rvlimit": MAX_REVISIONS_PER_CHECK,
        "rvdir": "newer",  # oldest -> newest
    }
    if since_revid:
        # rvstartid is inclusive; we'll filter the matching one out later
        params["rvstartid"] = since_revid
    data = _api_get(params)
    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return []
    page = pages[0]
    if page.get("missing"):
        logger.warning("Wikipedia page missing: %s", title)
        return []
    revs = page.get("revisions", []) or []
    # Filter out the since_revid itself if present
    if since_revid is not None:
        revs = [r for r in revs if r.get("revid", 0) > since_revid]
    return revs


def _fetch_compare_diff(from_revid: int, to_revid: int) -> str:
    """Return a plain-text diff between two revids using the compare API.

    Wikipedia returns the diff as HTML by default; we strip tags for the
    LLM prompt context.
    """
    try:
        data = _api_get({
            "action": "compare",
            "fromrev": from_revid,
            "torev": to_revid,
            "prop": "diff",
            "difftype": "table",  # only option supported by MW
        }, timeout=30)
    except Exception as e:
        logger.debug("compare API failed for %s..%s: %s", from_revid, to_revid, e)
        return ""

    html = (data.get("compare") or {}).get("body") or ""
    if not html:
        return ""
    # Convert <td>'s and <tr>'s to readable diff text. Cheap regex strip works
    # because we only need the LLM to read the change, not preserve markup.
    import re
    # Remove diff markers (line numbers, tags)
    text = re.sub(r"<td[^>]*class=\"diff-marker[^\"]*\"[^>]*>[^<]*</td>", "", html)
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate aggressively — LLM context window
    return text[:6000]


def _ai_explain_change(figure, change_data: dict) -> str:
    """Ask the LLM to explain what changed and why it might matter.

    Uses the centralized llm_provider with the editorial constitution
    prepended. Defaults to whatever role='analyzer' is configured at
    /admin/ai-provider (Hermes3 on Tark1 by default).
    """
    try:
        from profoundd.utils.editorial_constitution import prepend as ec_prepend
        from profoundd.search.llm_provider import build_llm, invoke_text
    except Exception as e:
        logger.warning("LLM unavailable for change explanation: %s", e)
        return ""

    comments = change_data.get("editor_comments", "")[:2000]
    diff = change_data.get("diff_text", "")[:4000]
    edit_count = change_data.get("edit_count", 0)
    editor_count = change_data.get("editor_count", 0)
    size_delta = change_data.get("size_delta_chars", 0)

    prompt = (
        f"A Wikipedia article about {figure.name} ({figure.role}) has changed "
        f"in the last week.\n\n"
        f"Number of edits: {edit_count}\n"
        f"Distinct editors: {editor_count}\n"
        f"Net size change: {size_delta:+d} characters\n\n"
        f"EDIT SUMMARIES (what the Wikipedia editors wrote in their commit "
        f"messages):\n{comments or '(none provided)'}\n\n"
        f"CONTENT DIFF (highlights of additions/removals):\n{diff or '(unavailable)'}\n\n"
        f"In 2-3 sentences, explain to a Profoundd reader: WHAT changed factually "
        f"about this person's biography, and what the editing pattern looks like "
        f"(routine update, narrative shaping, contested editing, ideological re-framing, "
        f"sanitizing controversy, etc.). Treat Wikipedia as one source whose editors "
        f"have their own perspectives — do not assume edits reflect neutral fact. "
        f"Be specific about concrete claims added or removed."
    )
    try:
        llm = build_llm("analyzer", max_tokens=400, temperature=0.3)
        return invoke_text(llm, ec_prepend(prompt)).strip()
    except Exception as e:
        logger.warning("AI explain failed for figure %s: %s", figure.slug, e)
        return ""


def check_figure(figure) -> dict:
    """Check one figure for new Wikipedia revisions. Returns summary dict."""
    from profoundd.utils.models import db, WikipediaChange
    now = datetime.now(timezone.utc)

    try:
        revs = _fetch_revisions(figure.wikipedia_title, figure.last_revision_id)
    except Exception as e:
        logger.warning("revisions fetch failed for %s: %s", figure.slug, e)
        return {"figure": figure.slug, "status": "fetch_failed", "error": str(e)[:200]}
    time.sleep(API_SLEEP_SECONDS)

    if not revs:
        figure.last_checked_at = now
        db.session.commit()
        return {"figure": figure.slug, "status": "no_changes", "edit_count": 0}

    # First-time check: just record the latest revid as baseline; no change row.
    if figure.last_revision_id is None:
        latest = revs[-1]
        figure.last_revision_id = latest["revid"]
        figure.last_checked_at = now
        db.session.commit()
        logger.info("baseline recorded for %s @ revid=%s", figure.slug, latest["revid"])
        return {"figure": figure.slug, "status": "baseline", "to_revid": latest["revid"]}

    # We have a prior revid AND new revisions — assemble the change row.
    from_revid = figure.last_revision_id
    to_revid = revs[-1]["revid"]
    first_rev = revs[0]
    last_rev = revs[-1]
    size_delta = (last_rev.get("size", 0) or 0) - (first_rev.get("size", 0) or 0)
    editors = {r.get("user", "?") for r in revs}
    comments = "\n".join(
        f"- {r.get('user', '?')} ({r.get('timestamp', '')[:10]}): {r.get('comment', '') or '(no comment)'}"
        for r in revs
    )

    # Diff is expensive — fetch only if there were >=1 edits
    diff_text = _fetch_compare_diff(from_revid, to_revid)
    time.sleep(API_SLEEP_SECONDS)

    change_data = {
        "edit_count": len(revs),
        "editor_count": len(editors),
        "size_delta_chars": size_delta,
        "editor_comments": comments,
        "diff_text": diff_text,
    }
    ai_text = _ai_explain_change(figure, change_data)

    row = WikipediaChange(
        figure_id=figure.id,
        from_revid=from_revid,
        to_revid=to_revid,
        edit_count=len(revs),
        editor_count=len(editors),
        size_delta_chars=size_delta,
        editor_comments=comments[:4000],
        diff_text=diff_text[:8000],
        ai_explanation=ai_text[:2000],
    )
    db.session.add(row)
    figure.last_revision_id = to_revid
    figure.last_checked_at = now
    figure.last_changed_at = now
    db.session.commit()

    return {
        "figure": figure.slug,
        "status": "change",
        "edits": len(revs),
        "editors": len(editors),
        "size_delta": size_delta,
        "explained": bool(ai_text),
    }


def run_weekly(app=None) -> dict:
    """Check every active TrackedFigure. Returns a summary dict for logging."""
    from profoundd.utils.models import TrackedFigure
    ctx = None
    if app is not None:
        ctx = app.app_context()
        ctx.__enter__()
    try:
        figures = TrackedFigure.query.filter_by(is_active=True).all()
        summary = {"total": len(figures), "results": []}
        logger.info("wikipedia_watcher: checking %d figures", len(figures))
        for fig in figures:
            try:
                result = check_figure(fig)
            except Exception as e:
                logger.exception("check_figure(%s) failed: %s", fig.slug, e)
                result = {"figure": fig.slug, "status": "error", "error": str(e)[:200]}
            summary["results"].append(result)
        logger.info("wikipedia_watcher summary: %s",
                    {k: v for k, v in summary.items() if k != "results"})
        return summary
    finally:
        if ctx:
            ctx.__exit__(None, None, None)
