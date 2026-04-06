#!/usr/bin/env python3
"""
Epstein Files Indexer — Extract text from DOJ PDFs and index to Elasticsearch.

Runs on Azure7 (where the WD 16TB drive lives) and indexes remotely to
Apollo9's ES via Tailscale.

Usage:
    python scripts/epstein_indexer.py \
        --es-url http://100.117.127.32:9201 \
        --extracted-dir /mnt/backup/epstein-files/extracted \
        --state-dir /mnt/backup/epstein-files/index-state \
        --datasets 1,2,3,4,5,6,7,8 \
        --workers 12 --batch-size 200

    # Test with one dataset first:
    python scripts/epstein_indexer.py --es-url http://100.117.127.32:9201 \
        --extracted-dir /mnt/backup/epstein-files/extracted \
        --state-dir /mnt/backup/epstein-files/index-state \
        --datasets 1 --workers 4 --batch-size 50
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from elasticsearch import Elasticsearch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Max chars of extracted text to store per document
MAX_TEXT_CHARS = 50000

# DOJ base URL for source links
DOJ_BASE = "https://www.justice.gov/epstein/files"
ARCHIVE_ORG_BASE = "https://archive.org/download"


# -------------------------------------------------------------------
# Metadata parsing (.OPT and .DAT files from Concordance/Relativity)
# -------------------------------------------------------------------

def parse_opt_file(opt_path):
    """
    Parse a Concordance .OPT file (image load file).
    Maps Bates numbers to PDF file paths.
    Returns dict: {bates_number: {"file_path": str, "page_count": int}}
    """
    metadata = {}
    current_bates = None
    current_pages = 0

    try:
        with open(opt_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 4:
                    bates = parts[0].strip()
                    # .OPT format: BATES, VOLUME, FILE_PATH, DOC_BREAK, PAGE_COUNT
                    file_path = parts[2].strip().strip('"')
                    doc_break = parts[3].strip() if len(parts) > 3 else ""

                    if doc_break.upper() in ("Y", "D"):
                        # New document
                        if current_bates and current_pages > 0:
                            metadata[current_bates]["page_count"] = current_pages
                        current_bates = bates
                        current_pages = 1
                        metadata[bates] = {"file_path": file_path, "page_count": 1}
                    else:
                        current_pages += 1

            # Close last document
            if current_bates and current_pages > 0:
                metadata[current_bates]["page_count"] = current_pages

    except Exception as e:
        logger.warning("Failed to parse OPT file %s: %s", opt_path, e)

    return metadata


def parse_dat_file(dat_path):
    """
    Parse a Concordance .DAT file (metadata load file).
    Contains document descriptions, dates, custodians.
    Returns dict: {bates_number: {"title": str, "custodian": str, "date": str}}
    """
    metadata = {}
    try:
        # .DAT files use various delimiters — try common ones
        with open(dat_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Try to detect delimiter (þ is common in Concordance, also \x14 or comma)
        if "\xfe" in content or "þ" in content:
            delimiter = "þ" if "þ" in content else "\xfe"
        elif "\x14" in content:
            delimiter = "\x14"
        else:
            delimiter = ","

        reader = csv.reader(io.StringIO(content), delimiter=delimiter, quotechar='"')
        headers = None
        for row in reader:
            if headers is None:
                headers = [h.strip().upper() for h in row]
                continue
            if len(row) < len(headers):
                continue

            row_dict = dict(zip(headers, row))

            # Find Bates number (various column names)
            bates = ""
            for col in ("BEGBATES", "BEG_BATES", "BEGDOC", "DOCID", "BATES"):
                if col in row_dict and row_dict[col].strip():
                    bates = row_dict[col].strip()
                    break
            if not bates:
                continue

            # Extract metadata fields
            title = ""
            for col in ("SUBJECT", "TITLE", "DESCRIPTION", "RE", "DOCNAME"):
                if col in row_dict and row_dict[col].strip():
                    title = row_dict[col].strip()
                    break

            custodian = ""
            for col in ("CUSTODIAN", "FROM", "AUTHOR", "SENDER"):
                if col in row_dict and row_dict[col].strip():
                    custodian = row_dict[col].strip()
                    break

            date = ""
            for col in ("DATECREATED", "DATE", "DATESENT", "DATERECEIVED"):
                if col in row_dict and row_dict[col].strip():
                    date = row_dict[col].strip()
                    break

            metadata[bates] = {
                "title": title,
                "custodian": custodian,
                "date": date,
            }

    except Exception as e:
        logger.warning("Failed to parse DAT file %s: %s", dat_path, e)

    return metadata


def load_dataset_metadata(dataset_path):
    """Load all metadata from .OPT and .DAT files in a dataset directory."""
    opt_meta = {}
    dat_meta = {}

    dataset_path = Path(dataset_path)
    for f in dataset_path.rglob("*.OPT"):
        opt_meta.update(parse_opt_file(f))
    for f in dataset_path.rglob("*.opt"):
        opt_meta.update(parse_opt_file(f))
    for f in dataset_path.rglob("*.DAT"):
        dat_meta.update(parse_dat_file(f))
    for f in dataset_path.rglob("*.dat"):
        # Skip very large .dat files that might be media
        if f.stat().st_size < 50_000_000:
            dat_meta.update(parse_dat_file(f))

    logger.info("Loaded metadata: %d OPT entries, %d DAT entries from %s",
                len(opt_meta), len(dat_meta), dataset_path)
    return opt_meta, dat_meta


# -------------------------------------------------------------------
# PDF text extraction
# -------------------------------------------------------------------

def extract_pdf_text(pdf_path, max_chars=MAX_TEXT_CHARS):
    """
    Extract text from a PDF file. Returns (text, page_count).
    Tries PyPDF2, then pdfplumber.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        page_count = len(reader.pages)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
            if sum(len(t) for t in pages_text) > max_chars:
                break
        full_text = "\n".join(pages_text)
        if full_text.strip():
            return full_text[:max_chars], page_count
    except Exception:
        pass

    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages_text.append(text)
                if sum(len(t) for t in pages_text) > max_chars:
                    break
        full_text = "\n".join(pages_text)
        if full_text.strip():
            return full_text[:max_chars], page_count
    except Exception:
        pass

    # Return empty text — still index with metadata
    try:
        from PyPDF2 import PdfReader
        page_count = len(PdfReader(pdf_path).pages)
    except Exception:
        page_count = 0

    return "", page_count


def _process_single_pdf(args):
    """Worker function for parallel PDF processing. Returns doc dict or None."""
    pdf_path, dataset_num, bates_number, opt_meta, dat_meta = args

    try:
        text, page_count = extract_pdf_text(pdf_path)

        # Build title from metadata or PDF content
        title = ""
        custodian = ""
        doc_date = ""

        if bates_number in dat_meta:
            dm = dat_meta[bates_number]
            title = dm.get("title", "")
            custodian = dm.get("custodian", "")
            doc_date = dm.get("date", "")

        if not title and text:
            # Use first non-blank line as title
            for line in text.split("\n"):
                line = line.strip()
                if len(line) > 10 and not line.startswith("["):
                    title = line[:200]
                    break

        if not title:
            # Clean filename as title
            name = Path(pdf_path).stem
            title = f"Document {name}"

        # Build summary from first ~500 chars of text
        summary = ""
        if text:
            clean = " ".join(text[:1000].split())
            summary = clean[:500]

        # Normalize date
        normalized_date = None
        if doc_date:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%y"):
                try:
                    normalized_date = datetime.strptime(doc_date.strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue

        # DOJ source URL
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
            "file_path": str(pdf_path),
            "custodian": custodian,
            "doc_date": normalized_date,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "tags": ["epstein", "doj", f"dataset-{dataset_num}"],
        }
    except Exception as e:
        logger.debug("Failed to process %s: %s", pdf_path, e)
        return None


# -------------------------------------------------------------------
# Indexer
# -------------------------------------------------------------------

class EpsteinIndexer:
    def __init__(self, es_url, extracted_dir, state_dir, workers=12, batch_size=200):
        self.es = Elasticsearch(es_url, request_timeout=60)
        self.extracted_dir = Path(extracted_dir)
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "progress.json"
        self.workers = workers
        self.batch_size = batch_size
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def ensure_index(self):
        """Create the Epstein docs index if needed."""
        from profoundd.search.engine import EPSTEIN_DOC_INDEX_NAME, EPSTEIN_DOC_MAPPING

        if not self.es.indices.exists(index=EPSTEIN_DOC_INDEX_NAME):
            self.es.indices.create(
                index=EPSTEIN_DOC_INDEX_NAME,
                mappings=EPSTEIN_DOC_MAPPING["mappings"],
                settings=EPSTEIN_DOC_MAPPING["settings"],
            )
            logger.info("Created index: %s", EPSTEIN_DOC_INDEX_NAME)
        else:
            logger.info("Index exists: %s", EPSTEIN_DOC_INDEX_NAME)

    def load_progress(self):
        """Load indexing progress for resumability."""
        if self.state_file.exists():
            return json.loads(self.state_file.read_text())
        return {}

    def save_progress(self, progress):
        """Save indexing progress."""
        self.state_file.write_text(json.dumps(progress, indent=2))

    def find_pdfs(self, dataset_path):
        """Find all PDF files in a dataset directory."""
        pdfs = []
        for f in sorted(Path(dataset_path).rglob("*.pdf")):
            pdfs.append(f)
        for f in sorted(Path(dataset_path).rglob("*.PDF")):
            if f not in pdfs:
                pdfs.append(f)
        return pdfs

    def bates_from_filename(self, pdf_path):
        """Extract Bates number from filename."""
        name = Path(pdf_path).stem
        # Common patterns: EFTA00123456, VOL00001_00123
        return name

    def bulk_index(self, docs):
        """Bulk-index documents to ES with retry."""
        from profoundd.search.engine import EPSTEIN_DOC_INDEX_NAME

        actions = []
        for doc in docs:
            doc_id = hashlib.md5(
                doc.get("bates_number", doc.get("file_path", "")).encode()
            ).hexdigest()
            clean = {k: v for k, v in doc.items() if not k.startswith("_")}
            actions.append({"index": {"_index": EPSTEIN_DOC_INDEX_NAME, "_id": doc_id}})
            actions.append(clean)

        for attempt in range(3):
            try:
                result = self.es.bulk(operations=actions, refresh=False)
                indexed = sum(
                    1 for item in result["items"]
                    if item["index"]["status"] in (200, 201)
                )
                return indexed
            except Exception as e:
                logger.warning("Bulk index attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return 0

    def index_dataset(self, dataset_num):
        """Process and index all PDFs in a dataset."""
        # Find the dataset directory (try common naming patterns)
        candidates = [
            self.extracted_dir / f"dataset-{dataset_num:02d}",
            self.extracted_dir / f"dataset-{dataset_num}",
            self.extracted_dir / f"DataSet {dataset_num}",
            self.extracted_dir / f"DataSet{dataset_num}",
            self.extracted_dir / f"DS{dataset_num}",
            self.extracted_dir / f"VOL{dataset_num:05d}",
        ]
        dataset_path = None
        for c in candidates:
            if c.exists():
                dataset_path = c
                break

        if not dataset_path:
            # Maybe the zip was extracted directly into extracted/
            # Look for any directory with dataset number
            for d in self.extracted_dir.iterdir():
                if d.is_dir() and str(dataset_num) in d.name:
                    dataset_path = d
                    break

        if not dataset_path:
            logger.error("Dataset %d directory not found in %s", dataset_num, self.extracted_dir)
            return 0

        logger.info("Processing Dataset %d from %s", dataset_num, dataset_path)

        # Load metadata
        opt_meta, dat_meta = load_dataset_metadata(dataset_path)

        # Find all PDFs
        pdfs = self.find_pdfs(dataset_path)
        logger.info("Found %d PDFs in Dataset %d", len(pdfs), dataset_num)

        if not pdfs:
            return 0

        # Load progress for resumability
        progress = self.load_progress()
        ds_key = f"dataset_{dataset_num}"
        indexed_files = set(progress.get(ds_key, {}).get("indexed_files", []))
        total_indexed = progress.get(ds_key, {}).get("total_indexed", 0)

        # Filter out already-indexed files
        remaining = [p for p in pdfs if str(p) not in indexed_files]
        logger.info("Dataset %d: %d remaining (%d already indexed)",
                     dataset_num, len(remaining), len(indexed_files))

        # Process in parallel, index in batches
        batch = []
        batch_count = 0
        failed = 0
        no_text = 0

        # Build work items
        work_items = []
        for pdf_path in remaining:
            bates = self.bates_from_filename(pdf_path)
            work_items.append((str(pdf_path), dataset_num, bates, opt_meta, dat_meta))

        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(_process_single_pdf, item): item[0] for item in work_items}

            for future in as_completed(futures):
                pdf_path = futures[future]
                try:
                    doc = future.result(timeout=60)
                    if doc is None:
                        failed += 1
                        continue
                    if not doc.get("content"):
                        no_text += 1
                    batch.append(doc)
                    indexed_files.add(pdf_path)

                    if len(batch) >= self.batch_size:
                        n = self.bulk_index(batch)
                        total_indexed += n
                        batch_count += 1
                        batch = []

                        # Save progress periodically
                        if batch_count % 5 == 0:
                            progress[ds_key] = {
                                "indexed_files": list(indexed_files),
                                "total_indexed": total_indexed,
                            }
                            self.save_progress(progress)
                            logger.info(
                                "Dataset %d progress: %d indexed, %d failed, %d no-text",
                                dataset_num, total_indexed, failed, no_text,
                            )
                except Exception as e:
                    failed += 1
                    logger.debug("PDF processing error for %s: %s", pdf_path, e)

        # Index remaining batch
        if batch:
            n = self.bulk_index(batch)
            total_indexed += n

        # Save final progress
        progress[ds_key] = {
            "indexed_files": list(indexed_files),
            "total_indexed": total_indexed,
            "completed": True,
        }
        self.save_progress(progress)

        logger.info(
            "Dataset %d complete: %d indexed, %d failed, %d no-text-extracted",
            dataset_num, total_indexed, failed, no_text,
        )
        return total_indexed

    def run(self, datasets):
        """Index specified datasets."""
        start = time.time()
        logger.info("=== Epstein Indexer Starting (datasets: %s) ===", datasets)

        self.ensure_index()

        grand_total = 0
        for ds_num in datasets:
            total = self.index_dataset(ds_num)
            grand_total += total

        # Final refresh
        from profoundd.search.engine import EPSTEIN_DOC_INDEX_NAME
        try:
            self.es.indices.refresh(index=EPSTEIN_DOC_INDEX_NAME)
        except Exception:
            pass

        elapsed = time.time() - start
        logger.info(
            "=== Epstein Indexer Done === %d total docs indexed in %.1f minutes",
            grand_total, elapsed / 60,
        )
        return grand_total


def main():
    parser = argparse.ArgumentParser(description="Index Epstein Files PDFs to Elasticsearch")
    parser.add_argument("--es-url", required=True, help="Elasticsearch URL (e.g., http://100.117.127.32:9201)")
    parser.add_argument("--extracted-dir", required=True, help="Path to extracted datasets")
    parser.add_argument("--state-dir", required=True, help="Path to store indexing progress")
    parser.add_argument("--datasets", required=True, help="Comma-separated dataset numbers (e.g., 1,2,3)")
    parser.add_argument("--workers", type=int, default=12, help="Parallel PDF extraction workers")
    parser.add_argument("--batch-size", type=int, default=200, help="ES bulk index batch size")
    args = parser.parse_args()

    datasets = [int(d.strip()) for d in args.datasets.split(",")]

    indexer = EpsteinIndexer(
        es_url=args.es_url,
        extracted_dir=args.extracted_dir,
        state_dir=args.state_dir,
        workers=args.workers,
        batch_size=args.batch_size,
    )
    indexer.run(datasets)


if __name__ == "__main__":
    main()
