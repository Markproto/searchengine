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
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
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

# Where weekly PNG snapshots are stored on disk
SNAPSHOT_ROOT = Path(os.environ.get("WIKI_SNAPSHOT_ROOT", "/app/data/wiki-snapshots"))
SCREENSHOT_VIEWPORT = (1024, 1400)   # width x height; tall enough for above-fold + intro


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


def _ai_describe_change(figure, change_data: dict) -> str:
    """Generate a NEUTRAL, descriptive summary of what changed.

    Deliberately NOT editorial — no judgments about why (e.g., 'narrative
    shaping', 'contested editing'). Just: this was added, this was removed,
    this paragraph now reads differently. Reader draws their own conclusions.

    Uses build_llm('analyzer') which routes to whatever provider is configured
    at /admin/ai-provider (Hermes3 on Tark1 by default).
    """
    try:
        from profoundd.search.llm_provider import build_llm, invoke_text
    except Exception as e:
        logger.warning("LLM unavailable for change description: %s", e)
        return ""

    diff = change_data.get("diff_text", "")[:4000]
    edit_count = change_data.get("edit_count", 0)
    size_delta = change_data.get("size_delta_chars", 0)

    prompt = (
        f"Two versions of the Wikipedia article about {figure.name} differ.\n\n"
        f"Number of edits in this window: {edit_count}\n"
        f"Net size change: {size_delta:+d} characters\n\n"
        f"CONTENT DIFF (what the article gained vs. lost):\n"
        f"{diff or '(diff unavailable — describe based on counts alone)'}\n\n"
        f"In 2-3 plain sentences, describe ONLY what changed. Stick to:\n"
        f"  - which sections were added or removed\n"
        f"  - which factual claims, dates, names, or quoted material differ\n"
        f"  - whether the changes are concentrated in one section or spread out\n"
        f"\n"
        f"Do NOT speculate about motive, bias, editorial intent, or whether the\n"
        f"edits are good or bad. Do NOT use words like 'narrative', 'agenda',\n"
        f"'contested', 'sanitized', 'whitewashed', 'shaped'. No judgment of any\n"
        f"kind. Just: 'X was added to the section about Y; the paragraph about\n"
        f"Z was rewritten to remove the phrase \"...\"; a new infobox field for\n"
        f"... was added.' That kind of dry, factual inventory."
    )
    try:
        llm = build_llm("analyzer", max_tokens=350, temperature=0.2)
        # Note: NOT calling ec_prepend — the editorial constitution would
        # encourage exactly the judgments we want to suppress here.
        return invoke_text(llm, prompt).strip()
    except Exception as e:
        logger.warning("AI describe failed for figure %s: %s", figure.slug, e)
        return ""


def _capture_snapshot(figure, revid: int) -> dict:
    """Render the Wikipedia article at this revid to a PNG file. Returns
    a dict with path/width/height/byte_size, or {} on failure.

    Uses Playwright + Chromium (installed via pip + playwright install).
    Skips silently if Playwright isn't available — image archive degrades
    gracefully, the rest of the watcher still works.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.info("playwright not installed; skipping snapshot for %s", figure.slug)
        return {}

    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    figure_dir = SNAPSHOT_ROOT / figure.slug
    figure_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{ts}-r{revid}.png"
    full_path = figure_dir / filename
    rel_path = f"{figure.slug}/{filename}"

    url = f"https://en.wikipedia.org/wiki/{figure.wikipedia_title}?oldid={revid}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox",          # required when running as root in container
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ])
            ctx = browser.new_context(
                viewport={"width": SCREENSHOT_VIEWPORT[0], "height": SCREENSHOT_VIEWPORT[1]},
                user_agent=WIKI_UA,
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # Strip nav / banners that aren't part of the article body
            page.evaluate("""() => {
                ['#mw-head', '#mw-navigation', '#mw-page-base', '#mw-head-base',
                 '#siteSub', '#contentSub', '#jump-to-nav', '.mw-jump-link',
                 '#footer', '.cookieinfo', '.mw-indicators',
                 '.mw-editsection', '.vector-page-titlebar'].forEach(sel => {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                });
            }""")
            page.screenshot(path=str(full_path), full_page=False,
                            type="png", clip={"x": 0, "y": 0,
                                              "width": SCREENSHOT_VIEWPORT[0],
                                              "height": SCREENSHOT_VIEWPORT[1]})
            browser.close()
        size = full_path.stat().st_size if full_path.exists() else 0
        logger.info("snapshot saved: %s (%.0f KB)", rel_path, size / 1024)
        return {
            "path": rel_path,
            "width": SCREENSHOT_VIEWPORT[0],
            "height": SCREENSHOT_VIEWPORT[1],
            "byte_size": size,
        }
    except Exception as e:
        logger.warning("snapshot capture failed for %s @ rev=%s: %s",
                       figure.slug, revid, e)
        return {}


def _save_snapshot_row(figure, revid: int, snap_info: dict):
    """Persist a WikipediaSnapshot row if we captured an image."""
    from profoundd.utils.models import db, WikipediaSnapshot
    if not snap_info.get("path"):
        return None
    row = WikipediaSnapshot(
        figure_id=figure.id,
        revision_id=revid,
        path=snap_info["path"],
        width=snap_info.get("width", 0),
        height=snap_info.get("height", 0),
        byte_size=snap_info.get("byte_size", 0),
    )
    db.session.add(row)
    db.session.flush()
    return row


def check_figure(figure) -> dict:
    """Check one figure for new Wikipedia revisions + capture a fresh snapshot.

    Even when there are no new revisions, we still take a screenshot so the
    weekly visual archive is unbroken.
    """
    from profoundd.utils.models import db, WikipediaChange
    now = datetime.now(timezone.utc)

    try:
        revs = _fetch_revisions(figure.wikipedia_title, figure.last_revision_id)
    except Exception as e:
        logger.warning("revisions fetch failed for %s: %s", figure.slug, e)
        return {"figure": figure.slug, "status": "fetch_failed", "error": str(e)[:200]}
    time.sleep(API_SLEEP_SECONDS)

    # ------------------------------------------------------------------ no edits
    if not revs:
        # Still capture an archival snapshot at current revid (visual history
        # is the point — readers see how the page looked each week).
        if figure.last_revision_id:
            snap = _capture_snapshot(figure, figure.last_revision_id)
            _save_snapshot_row(figure, figure.last_revision_id, snap)
        figure.last_checked_at = now
        db.session.commit()
        return {"figure": figure.slug, "status": "no_changes", "edit_count": 0}

    # ----------------------------------------------------- first-ever check (baseline)
    if figure.last_revision_id is None:
        latest = revs[-1]
        snap = _capture_snapshot(figure, latest["revid"])
        _save_snapshot_row(figure, latest["revid"], snap)
        figure.last_revision_id = latest["revid"]
        figure.last_checked_at = now
        db.session.commit()
        logger.info("baseline recorded for %s @ revid=%s", figure.slug, latest["revid"])
        return {"figure": figure.slug, "status": "baseline", "to_revid": latest["revid"]}

    # --------------------------------------------------- normal weekly change
    from_revid = figure.last_revision_id
    to_revid = revs[-1]["revid"]
    first_rev = revs[0]
    last_rev = revs[-1]
    size_delta = (last_rev.get("size", 0) or 0) - (first_rev.get("size", 0) or 0)
    editors = {r.get("user", "?") for r in revs}

    # Walk the revision chain to capture the TRUE volume of churn — net delta
    # hides cases where someone adds 1000c and another removes 1000c. Also
    # track the largest single edit so we can flag big rewrites.
    prev_size = first_rev.get("size", 0) or 0
    total_volume = 0
    largest_edit = 0
    for r in revs[1:]:
        cur_size = r.get("size", 0) or 0
        edit_delta = abs(cur_size - prev_size)
        total_volume += edit_delta
        if edit_delta > largest_edit:
            largest_edit = edit_delta
        prev_size = cur_size
    # Keep editor_comments in DB for forensic review but don't surface publicly
    comments = "\n".join(
        f"- {r.get('user', '?')} ({r.get('timestamp', '')[:10]}): {r.get('comment', '') or '(no comment)'}"
        for r in revs
    )

    diff_text = _fetch_compare_diff(from_revid, to_revid)
    time.sleep(API_SLEEP_SECONDS)

    change_data = {
        "edit_count": len(revs),
        "editor_count": len(editors),
        "size_delta_chars": size_delta,
        "diff_text": diff_text,
    }
    ai_text = _ai_describe_change(figure, change_data)

    snap = _capture_snapshot(figure, to_revid)
    _save_snapshot_row(figure, to_revid, snap)

    row = WikipediaChange(
        figure_id=figure.id,
        from_revid=from_revid,
        to_revid=to_revid,
        edit_count=len(revs),
        editor_count=len(editors),
        size_delta_chars=size_delta,
        total_volume_chars=total_volume,
        largest_edit_chars=largest_edit,
        editor_comments=comments[:4000],          # retained in DB, hidden from UI
        diff_text=diff_text[:8000],               # retained in DB, hidden from UI
        ai_explanation=ai_text[:2000],
        snapshot_path=snap.get("path", ""),
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
        "snapshot": bool(snap),
        "described": bool(ai_text),
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
