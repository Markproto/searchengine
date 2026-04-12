#!/usr/bin/env python3
"""
Re-index PDFs that processed but never made it into ES (bulk-insert failures).

Reads missing-dsN.txt files (one path per line), extracts text via pdftotext,
falls back to OCR via ocrmypdf if text is empty, and bulk-indexes to ES.

Usage on Azure7:
    /home/mark/epstein-venv/bin/python epstein_reindex_missing.py \\
        --es-url http://100.117.127.32:9201 \\
        --missing-files /mnt/backup/epstein-files/index-state/missing-ds9.txt,/mnt/backup/epstein-files/index-state/missing-ds10.txt \\
        --workers 8 --batch-size 100 \\
        --log /mnt/backup/epstein-files/reindex-missing.log
"""
import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Indexer constants — keep in sync with profoundd/search/engine.py
EPSTEIN_DOC_INDEX_NAME = "profoundd_epstein_docs"
MAX_TEXT_CHARS = 50000
DOJ_BASE = "https://www.justice.gov/epstein/files"
OCR_TIMEOUT_SEC = 180  # per file


def extract_text_pdftotext(pdf_path, max_chars=MAX_TEXT_CHARS):
    """Extract text via poppler's pdftotext. Fast and reliable for text PDFs."""
    try:
        r = subprocess.run(
            ["pdftotext", "-q", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=60,
        )
        return (r.stdout or "")[:max_chars]
    except Exception:
        return ""


def extract_text_ocr(pdf_path, max_chars=MAX_TEXT_CHARS):
    """OCR fallback via ocrmypdf → pdftotext. Slow but handles scan-only PDFs."""
    try:
        # ocrmypdf adds a text layer; output to stdout via /dev/stdout
        out_path = f"/tmp/ocr-{os.getpid()}-{int(time.time()*1000)}.pdf"
        try:
            subprocess.run(
                ["ocrmypdf", "--quiet", "--skip-text", "--optimize", "0",
                 "--output-type", "pdf", pdf_path, out_path],
                capture_output=True, timeout=OCR_TIMEOUT_SEC,
            )
            r = subprocess.run(
                ["pdftotext", "-q", out_path, "-"],
                capture_output=True, text=True, timeout=60,
            )
            return (r.stdout or "")[:max_chars]
        finally:
            try:
                os.remove(out_path)
            except Exception:
                pass
    except Exception:
        return ""


def extract_dataset_num(pdf_path):
    """Parse dataset number from path like .../DataSet 9/... or .../VOL00009/..."""
    m = re.search(r"DataSet\s*(\d+)", pdf_path, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"VOL0*(\d+)", pdf_path)
    if m:
        return int(m.group(1))
    return 0


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


def process_pdf(pdf_path):
    """Worker: extract text and build the doc dict, matching the original indexer's format."""
    try:
        text = extract_text_pdftotext(pdf_path)
        used_ocr = False
        if not text or len(text.strip()) < 50:
            ocr_text = extract_text_ocr(pdf_path)
            if len(ocr_text.strip()) > len(text.strip()):
                text = ocr_text
                used_ocr = True

        page_count = get_page_count(pdf_path)
        dataset_num = extract_dataset_num(pdf_path)
        bates_number = Path(pdf_path).stem

        # Title from first non-blank line, or filename
        title = ""
        if text:
            for line in text.split("\n"):
                line = line.strip()
                if len(line) > 10 and not line.startswith("["):
                    title = line[:200]
                    break
        if not title:
            title = f"Document {bates_number}"

        summary = ""
        if text:
            summary = " ".join(text[:1000].split())[:500]

        source_url = f"{DOJ_BASE}/DataSet%20{dataset_num}.zip"

        return {
            "title": title,
            "content": text,
            "summary": summary,
            "bates_number": bates_number,
            "dataset_number": dataset_num,
            "page_count": page_count,
            "source_name": f"DOJ Epstein Files (Dataset {dataset_num})",
            "source_url": source_url,
            "category": "epstein-files",
            "result_type": "document",
            "file_path": pdf_path,
            "custodian": "",
            "doc_date": None,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "tags": ["epstein", "doj", f"dataset-{dataset_num}",
                     "ocr" if used_ocr else "text"],
            "_used_ocr": used_ocr,
        }
    except Exception as e:
        return {"_error": str(e), "_path": pdf_path}


def bulk_index(es, docs, logger):
    """Bulk-index a batch with retries. Filters out per-doc failures and re-tries
    smaller batches if needed (so one bad doc doesn't tank a whole batch)."""
    if not docs:
        return 0, 0
    actions = []
    for doc in docs:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        doc_id = hashlib.md5(doc.get("file_path", "").encode()).hexdigest()
        actions.append({"index": {"_index": EPSTEIN_DOC_INDEX_NAME, "_id": doc_id}})
        actions.append(clean)

    for attempt in range(3):
        try:
            result = es.options(request_timeout=300).bulk(operations=actions, refresh=False)
            successes = 0
            errors = 0
            for item in result["items"]:
                op = item.get("index", item.get("create", {}))
                if op.get("status") in (200, 201):
                    successes += 1
                else:
                    errors += 1
                    if attempt == 2:  # log on final attempt only
                        logger.warning("ES rejected doc %s: %s",
                                       op.get("_id", "?"), op.get("error", {}).get("reason", "?"))
            return successes, errors
        except Exception as e:
            logger.warning("Bulk attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 ** attempt)

    # All attempts failed — try splitting in half
    if len(docs) > 1:
        logger.warning("Splitting batch of %d after 3 failures", len(docs))
        half = len(docs) // 2
        s1, e1 = bulk_index(es, docs[:half], logger)
        s2, e2 = bulk_index(es, docs[half:], logger)
        return s1 + s2, e1 + e2

    return 0, len(docs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--es-url", required=True)
    p.add_argument("--missing-files", required=True,
                   help="Comma-separated paths to missing-dsN.txt files")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--log", default="/mnt/backup/epstein-files/reindex-missing.log")
    p.add_argument("--state-file", default="/mnt/backup/epstein-files/index-state/reindex-progress.json")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    from elasticsearch import Elasticsearch
    es = Elasticsearch(args.es_url, request_timeout=300, max_retries=3, retry_on_timeout=True)

    # Load PDFs to process
    all_pdfs = []
    for fp in args.missing_files.split(","):
        with open(fp.strip()) as f:
            for line in f:
                line = line.strip()
                if line and Path(line).exists():
                    all_pdfs.append(line)
    logger.info("Loaded %d PDFs to re-index", len(all_pdfs))

    # Resume support
    state = {}
    if Path(args.state_file).exists():
        state = json.loads(Path(args.state_file).read_text())
    done_set = set(state.get("done", []))
    if done_set:
        logger.info("Skipping %d already-processed PDFs from previous run", len(done_set))
        all_pdfs = [p for p in all_pdfs if p not in done_set]

    logger.info("Processing %d PDFs with %d workers, batch=%d",
                len(all_pdfs), args.workers, args.batch_size)
    start = time.time()

    batch = []
    total_indexed = 0
    total_errors = 0
    total_processed = 0
    total_ocr = 0
    total_failed = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_pdf, p): p for p in all_pdfs}

        for future in as_completed(futures):
            pdf_path = futures[future]
            try:
                doc = future.result(timeout=OCR_TIMEOUT_SEC + 60)
            except Exception as e:
                logger.debug("Worker exception on %s: %s", pdf_path, e)
                total_failed += 1
                done_set.add(pdf_path)
                continue

            total_processed += 1

            if doc.get("_error"):
                logger.debug("Process error on %s: %s", pdf_path, doc["_error"])
                total_failed += 1
            else:
                if doc.get("_used_ocr"):
                    total_ocr += 1
                batch.append(doc)
                done_set.add(pdf_path)

                if len(batch) >= args.batch_size:
                    s, e = bulk_index(es, batch, logger)
                    total_indexed += s
                    total_errors += e
                    batch = []

                    # Save progress every batch
                    state["done"] = sorted(done_set)
                    state["total_indexed"] = total_indexed
                    Path(args.state_file).write_text(json.dumps(state))

                    elapsed = time.time() - start
                    rate = total_processed / elapsed if elapsed else 0
                    remaining = (len(all_pdfs) - total_processed) / rate if rate else 0
                    logger.info(
                        "Progress: %d/%d processed (%.1f%%), %d indexed, %d ocr, %d failed, %.1f/s, ETA %.0f min",
                        total_processed, len(all_pdfs),
                        100.0 * total_processed / len(all_pdfs),
                        total_indexed, total_ocr, total_failed, rate, remaining / 60,
                    )

    # Final batch
    if batch:
        s, e = bulk_index(es, batch, logger)
        total_indexed += s
        total_errors += e

    # Save final state
    state["done"] = sorted(done_set)
    state["total_indexed"] = total_indexed
    state["completed"] = True
    Path(args.state_file).write_text(json.dumps(state))

    # Final refresh
    try:
        es.indices.refresh(index=EPSTEIN_DOC_INDEX_NAME)
    except Exception:
        pass

    elapsed = time.time() - start
    logger.info(
        "=== Done === processed=%d indexed=%d ocr_used=%d failed=%d errors=%d in %.1f min",
        total_processed, total_indexed, total_ocr, total_failed, total_errors, elapsed / 60,
    )


if __name__ == "__main__":
    main()
