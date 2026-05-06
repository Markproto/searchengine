#!/usr/bin/env python3
"""
NIH RePORTER pandemic-era grants indexer.

NIH's RePORTER API exposes ~250K+ grants matching pandemic-relevant
search terms. This indexer pulls metadata + abstract for each project
and lands it in `profoundd_archive_docs` with collection="nih-grants".

We focus on FY 2019-2024 grants matching a curated set of search terms
that capture pandemic-relevant funding flows: gain-of-function,
coronavirus, EcoHealth, Wuhan, mRNA, vaccine, spike protein,
bioweapons, biosafety, etc.

Each grant becomes one ES doc; abstract + project_title is the
searchable body. No PDFs (NIH doesn't release them).

API ref: https://api.reporter.nih.gov/documentation
Polite rate: NIH allows ~10 req/sec but we default to 1 to be safe.

Usage:
    python scripts/nih_grants_indexer.py --es-url http://127.0.0.1:9201 \\
        --state-dir /home/mark/nih-grants-state --rate 0.5
"""
import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _archive_indexer_common import (  # noqa: E402
    ensure_archive_index, bulk_index, init_state, make_doc_id, USER_AGENT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("nih_grants")

COLLECTION = "nih-grants"
API_URL = "https://api.reporter.nih.gov/v2/projects/search"

# Curated pandemic-era search terms. Each term hits the API separately and
# results are deduped by application_id. Limited to FY 2019-2024 to keep
# the corpus focused on pandemic-relevant grants.
SEARCH_TERMS = [
    "gain-of-function",
    "coronavirus",
    "SARS-CoV-2",
    "spike protein",
    "EcoHealth",
    "Wuhan Institute",
    "mRNA vaccine",
    "lipid nanoparticle",
    "bioweapons",
    "biosafety level 4",
    "DARPA pandemic",
    "lab leak",
    "bat coronavirus",
    "furin cleavage",
    "ACE2 receptor",
    "myocarditis vaccine",
]
FISCAL_YEARS = [2019, 2020, 2021, 2022, 2023, 2024]
PAGE_SIZE = 100  # API limit per request


def query_api(term, offset=0):
    body = {
        "criteria": {
            "fiscal_years": FISCAL_YEARS,
            "advanced_text_search": {
                "operator": "and",
                "search_field": "projecttitle,abstracttext,terms",
                "search_text": term,
            },
        },
        "limit": PAGE_SIZE,
        "offset": offset,
        "sort_field": "fiscal_year",
        "sort_order": "desc",
    }
    try:
        r = requests.post(
            API_URL, json=body,
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        if r.status_code == 429:
            logger.warning("NIH 429; sleeping 30s")
            time.sleep(30)
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("api %s offset=%s: %s", term, offset, e)
        return None


def iter_projects(rate=0.5, max_per_term=2000):
    """Yield project records from the API, deduped by application_id."""
    seen_ids = set()
    for term in SEARCH_TERMS:
        offset = 0
        per_term = 0
        while True:
            time.sleep(rate)
            data = query_api(term, offset)
            if not data:
                break
            results = data.get("results", []) or []
            if not results:
                break
            for proj in results:
                pid = proj.get("appl_id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                proj["_match_term"] = term
                yield proj
                per_term += 1
                if per_term >= max_per_term:
                    break
            if per_term >= max_per_term:
                break
            total = data.get("meta", {}).get("total", 0)
            offset += PAGE_SIZE
            if offset >= total or offset >= 5000:
                # NIH limits offset to 5000; for super-broad terms we just
                # take the top 5K most recent
                break
        logger.info("term '%s': %d projects yielded so far (total seen=%d)",
                    term, per_term, len(seen_ids))


def build_doc(proj):
    appl_id = str(proj.get("appl_id"))
    title = proj.get("project_title", "") or ""
    abstract = proj.get("abstract_text", "") or ""
    terms = proj.get("terms", "") or ""
    pub_org = proj.get("organization", {}).get("org_name", "") or ""
    pi = ""
    pis = proj.get("principal_investigators", []) or []
    if pis:
        pi = pis[0].get("full_name", "") or ""
    fy = proj.get("fiscal_year")
    award = proj.get("award_amount")
    funding = proj.get("agency_ic_admin", {}).get("name", "") or ""

    # Header lines + abstract make the body searchable
    body_lines = []
    if pi:
        body_lines.append(f"Principal Investigator: {pi}")
    if pub_org:
        body_lines.append(f"Organization: {pub_org}")
    if fy:
        body_lines.append(f"Fiscal Year: {fy}")
    if award:
        body_lines.append(f"Award: ${award:,}")
    if funding:
        body_lines.append(f"Funding agency: {funding}")
    body_lines.append("")
    if abstract:
        body_lines.append(abstract)
    if terms:
        body_lines.append("")
        body_lines.append(f"Terms: {terms}")
    content = "\n".join(body_lines)

    summary_parts = [pi, pub_org, str(fy) if fy else ""]
    summary = " · ".join(p for p in summary_parts if p)
    if award:
        summary += f" · ${award:,}"
    if abstract:
        snippet = abstract.replace("\n", " ").strip()[:300]
        summary = f"{summary} — {snippet}…" if summary else snippet

    project_url = f"https://reporter.nih.gov/project-details/{appl_id}"

    doc_date = ""
    proj_start = proj.get("project_start_date") or ""
    if proj_start and len(proj_start) >= 10:
        doc_date = proj_start[:10]

    return {
        "doc_id": make_doc_id(COLLECTION, appl_id),
        "collection": COLLECTION,
        "title": title[:400] or f"NIH project {appl_id}",
        "summary": summary[:600],
        "content": content,
        "case": "pandemic-era-grants",
        "sub_case": str(fy) if fy else "",
        "pdf_url": "",
        "source_url": project_url,
        "doc_date": doc_date,
        "tags": [
            "nih", "grant",
            f"fy{fy}" if fy else "",
            proj.get("_match_term", "").replace(" ", "-").lower(),
        ],
        "page_count": 0,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "category": "archive",
        "result_type": "document",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", default="http://127.0.0.1:9201")
    p.add_argument("--state-dir", default="/home/mark/nih-grants-state")
    p.add_argument("--rate", type=float, default=0.5)
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--max-per-term", type=int, default=2000)
    p.add_argument("--batch-size", type=int, default=50)
    args = p.parse_args()

    conn = init_state(args.state_dir, "nih_grants_state.db", key_column="appl_id")
    ensure_archive_index(args.es_url)

    indexed = skipped = errors = 0
    batch = []
    start = time.time()

    for proj in iter_projects(rate=args.rate, max_per_term=args.max_per_term):
        appl_id = str(proj.get("appl_id"))
        row = conn.execute("SELECT status FROM items WHERE appl_id=?", (appl_id,)).fetchone()
        if row and row[0] == "indexed":
            skipped += 1
            continue

        try:
            doc = build_doc(proj)
        except Exception as e:
            errors += 1
            logger.warning("build %s: %s", appl_id, e)
            continue
        batch.append(doc)
        conn.execute(
            "INSERT OR REPLACE INTO items "
            "(appl_id,status,indexed_at,page_count,text_chars,error) VALUES (?,?,?,?,?,?)",
            (appl_id, "queued", datetime.now(timezone.utc).isoformat(),
             0, len(doc["content"]), None),
        )
        conn.commit()

        if len(batch) >= args.batch_size:
            n = bulk_index(args.es_url, batch)
            indexed += n
            for d in batch:
                aid = d["source_url"].rsplit("/", 1)[-1]
                conn.execute("UPDATE items SET status='indexed' WHERE appl_id=?",
                             (aid,))
            conn.commit()
            batch = []
            logger.info("Progress: indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                        indexed, skipped, errors, time.time() - start)
        if args.max_items and indexed >= args.max_items:
            break

    if batch:
        n = bulk_index(args.es_url, batch)
        indexed += n
        for d in batch:
            aid = d["source_url"].rsplit("/", 1)[-1]
            conn.execute("UPDATE items SET status='indexed' WHERE appl_id=?", (aid,))
        conn.commit()

    logger.info("Done. indexed=%d skipped=%d errors=%d elapsed=%.0fs",
                indexed, skipped, errors, time.time() - start)
    conn.close()


if __name__ == "__main__":
    main()
