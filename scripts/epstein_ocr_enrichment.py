#!/usr/bin/env python3
"""
Epstein Files OCR Enrichment — re-extract text from PDFs with thin/empty content.

Queries ES for docs where content is thin relative to page count, re-processes
them with pdftotext (fast) and falls back to ocrmypdf+tesseract (slow but handles
scanned images). Re-indexes with enriched content.

Runs on Azure7 (where WD drive + OCR tools live), indexes to Apollo9 ES via Tailscale.

Usage:
    /home/mark/epstein-venv/bin/python scripts/epstein_ocr_enrichment.py \
        --es-url http://100.117.127.32:9201 \
        --workers 12 \
        --batch-size 50 \
        --log /mnt/backup/epstein-files/ocr-enrichment.log \
        --state-file /mnt/backup/epstein-files/index-state/ocr-enrichment-state.json
"""
import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ES_INDEX = "profoundd_epstein_docs"
OCR_TIMEOUT = 300  # 5 min per file max
# Docs with fewer than this many chars per page get OCR'd
THIN_THRESHOLD = 50  # chars per page


def extract_text_pdftotext(pdf_path, max_chars=50000):
    """Fast text extraction via poppler's pdftotext."""
    try:
        r = subprocess.run(
            ["pdftotext", "-q", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=60,
        )
        return (r.stdout or "")[:max_chars]
    except Exception:
        return ""


def extract_text_ocr(pdf_path, max_chars=50000):
    """OCR via ocrmypdf → pdftotext. Slow but handles scan-only PDFs."""
    out_path = f"/tmp/ocr-{os.getpid()}-{int(time.time() * 1000)}.pdf"
    try:
        subprocess.run(
            ["ocrmypdf", "--quiet", "--skip-text", "--optimize", "0",
             "--output-type", "pdf", "--tesseract-timeout", "120",
             pdf_path, out_path],
            capture_output=True, timeout=OCR_TIMEOUT,
        )
        r = subprocess.run(
            ["pdftotext", "-q", "-layout", out_path, "-"],
            capture_output=True, text=True, timeout=60,
        )
        return (r.stdout or "")[:max_chars]
    except Exception:
        return ""
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass


def get_page_count(pdf_path):
    try:
        r = subprocess.run(
            ["pdfinfo", pdf_path], capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.split("\n"):
            if line.startswith("Pages:"):
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return 0


def process_pdf(args):
    """Worker: re-extract text from a thin-content PDF."""
    pdf_path, doc_id, bates, dataset_num, old_content_len = args
    try:
        if not Path(pdf_path).exists():
            return None

        # Try fast pdftotext first
        text = extract_text_pdftotext(pdf_path)
        used_ocr = False

        # If pdftotext didn't do much better, try OCR
        if len(text.strip()) < max(old_content_len * 1.5, 100):
            ocr_text = extract_text_ocr(pdf_path)
            if len(ocr_text.strip()) > len(text.strip()):
                text = ocr_text
                used_ocr = True

        if not text or len(text.strip()) <= old_content_len:
            return None  # No improvement

        page_count = get_page_count(pdf_path)

        # Build title from first non-blank line
        title = ""
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 10 and not line.startswith("["):
                title = line[:200]
                break
        if not title:
            title = f"Document {bates}"

        summary = " ".join(text[:1000].split())[:500]

        return {
            "doc_id": doc_id,
            "bates_number": bates,
            "dataset_number": dataset_num,
            "content": text,
            "title": title,
            "summary": summary,
            "page_count": page_count,
            "file_path": pdf_path,
            "used_ocr": used_ocr,
            "new_content_len": len(text),
            "old_content_len": old_content_len,
        }
    except Exception as e:
        return None


def main():
    p = argparse.ArgumentParser(description="OCR enrichment for thin-content Epstein docs")
    p.add_argument("--es-url", required=True)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--log", default="/mnt/backup/epstein-files/ocr-enrichment.log")
    p.add_argument("--state-file", default="/mnt/backup/epstein-files/index-state/ocr-enrichment-state.json")
    p.add_argument("--max-docs", type=int, default=0, help="Limit docs to process (0=all)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    from elasticsearch import Elasticsearch
    es = Elasticsearch(args.es_url, request_timeout=300, max_retries=3, retry_on_timeout=True)

    # Load state
    state = {}
    if Path(args.state_file).exists():
        state = json.loads(Path(args.state_file).read_text())
    processed_ids = set(state.get("processed", []))

    logger.info("=== Epstein OCR Enrichment Starting ===")
    logger.info("ES: %s | Workers: %d | Already processed: %d",
                args.es_url, args.workers, len(processed_ids))

    # Phase 1: Find thin-content docs via scroll
    logger.info("Scanning ES for thin-content documents...")
    thin_docs = []

    scroll_body = {
        "size": 500,
        "_source": ["bates_number", "dataset_number", "page_count", "content", "file_path"],
        "query": {"match_all": {}},
    }

    try:
        result = es.search(index=ES_INDEX, body=scroll_body, scroll="5m")
    except Exception as e:
        logger.error("Initial scroll failed: %s", e)
        return

    scroll_id = result.get("_scroll_id")
    scanned = 0

    while True:
        hits = result["hits"]["hits"]
        if not hits:
            break

        for hit in hits:
            scanned += 1
            doc_id = hit["_id"]
            if doc_id in processed_ids:
                continue

            src = hit["_source"]
            content = src.get("content", "") or ""
            content_len = len(content)
            pages = max(src.get("page_count", 1) or 1, 1)
            ratio = content_len / pages

            if content_len < 20 or ratio < THIN_THRESHOLD:
                file_path = src.get("file_path", "")
                if file_path:
                    thin_docs.append({
                        "doc_id": doc_id,
                        "bates": src.get("bates_number", ""),
                        "dataset_num": src.get("dataset_number", 0),
                        "file_path": file_path,
                        "content_len": content_len,
                        "pages": pages,
                        "ratio": ratio,
                    })

        if scanned % 50000 == 0:
            logger.info("Scanned %d docs, found %d thin so far...", scanned, len(thin_docs))

        if args.max_docs and len(thin_docs) >= args.max_docs:
            break

        try:
            result = es.scroll(scroll_id=scroll_id, scroll="5m")
        except Exception:
            break

    try:
        es.clear_scroll(scroll_id=scroll_id)
    except Exception:
        pass

    logger.info("Scan complete: %d docs scanned, %d thin-content docs to OCR", scanned, len(thin_docs))

    if not thin_docs:
        logger.info("No thin docs found. Done!")
        return

    # Phase 2: Process with OCR and re-index
    start = time.time()
    enriched = 0
    no_improvement = 0
    failed = 0
    ocr_used = 0

    work_items = [
        (d["file_path"], d["doc_id"], d["bates"], d["dataset_num"], d["content_len"])
        for d in thin_docs
    ]

    if args.max_docs:
        work_items = work_items[:args.max_docs]

    logger.info("Processing %d docs with %d workers...", len(work_items), args.workers)

    batch = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_pdf, item): item for item in work_items}

        for future in as_completed(futures):
            item = futures[future]
            doc_id = item[1]

            try:
                result = future.result(timeout=OCR_TIMEOUT + 60)
            except Exception:
                failed += 1
                processed_ids.add(doc_id)
                continue

            processed_ids.add(doc_id)

            if result is None:
                no_improvement += 1
            else:
                if result.get("used_ocr"):
                    ocr_used += 1
                batch.append(result)

                if len(batch) >= args.batch_size:
                    # Bulk update ES
                    actions = []
                    for doc in batch:
                        actions.append({"update": {"_index": ES_INDEX, "_id": doc["doc_id"]}})
                        actions.append({"doc": {
                            "content": doc["content"],
                            "title": doc["title"],
                            "summary": doc["summary"],
                            "page_count": doc["page_count"],
                            "tags": ["ocr-enriched"] if doc["used_ocr"] else [],
                        }})

                    try:
                        es.options(request_timeout=300).bulk(operations=actions, refresh=False)
                        enriched += len(batch)
                    except Exception as e:
                        logger.warning("Bulk update failed: %s", e)
                        failed += len(batch)

                    batch = []

                    # Save progress
                    state["processed"] = sorted(processed_ids)
                    state["enriched"] = enriched
                    state["ocr_used"] = ocr_used
                    Path(args.state_file).write_text(json.dumps(state))

                    elapsed = time.time() - start
                    total_done = enriched + no_improvement + failed
                    rate = total_done / elapsed * 60 if elapsed else 0
                    remaining = (len(work_items) - total_done) / rate if rate else 0
                    logger.info(
                        "Progress: %d/%d done (%.1f%%), %d enriched, %d no-improve, "
                        "%d failed, %d OCR, %.1f docs/min, ETA %.0f min",
                        total_done, len(work_items),
                        100 * total_done / len(work_items),
                        enriched, no_improvement, failed, ocr_used, rate, remaining,
                    )

    # Final batch
    if batch:
        actions = []
        for doc in batch:
            actions.append({"update": {"_index": ES_INDEX, "_id": doc["doc_id"]}})
            actions.append({"doc": {
                "content": doc["content"],
                "title": doc["title"],
                "summary": doc["summary"],
                "page_count": doc["page_count"],
                "tags": ["ocr-enriched"] if doc["used_ocr"] else [],
            }})
        try:
            es.options(request_timeout=300).bulk(operations=actions, refresh=False)
            enriched += len(batch)
        except Exception as e:
            logger.warning("Final bulk update failed: %s", e)
            failed += len(batch)

    # Refresh index
    try:
        es.indices.refresh(index=ES_INDEX)
    except Exception:
        pass

    # Save final state
    state["processed"] = sorted(processed_ids)
    state["enriched"] = enriched
    state["ocr_used"] = ocr_used
    state["completed"] = True
    Path(args.state_file).write_text(json.dumps(state))

    elapsed = time.time() - start
    logger.info(
        "=== OCR Enrichment Done === enriched=%d no_improve=%d failed=%d ocr_used=%d time=%.1f hrs",
        enriched, no_improvement, failed, ocr_used, elapsed / 3600,
    )


if __name__ == "__main__":
    main()
