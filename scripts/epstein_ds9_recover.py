#!/usr/bin/env python3
"""
Recover missing DS9 native files from DOJ.

Strategy:
1. Probe each Bates ID against common video/audio extensions via HEAD requests
2. For anything that returns >1KB, download via GET with age-verify cookie
3. Save into /mnt/backup/epstein-files/extracted/DataSet 9/DataSet 9/VOL00009/NATIVES/
4. Also try the 3 disappeared.csv files (known URLs)
5. Resumable: skip files that already exist on disk

Runs on Azure7. Uses the age-verify cookie discovered earlier (justiceGovAgeVerified=true).
"""
import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

COOKIE = "justiceGovAgeVerified=true"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
BASE = "https://www.justice.gov/epstein/files/DataSet%209"

# Ordered by frequency expected in the DS9 NATIVES corpus
EXTENSIONS = [
    "mp4", "wmv", "avi", "mov", "m4v", "mpg", "mpeg",
    "mkv", "ts", "3gp", "flv", "vob",
    "m4a", "mp3", "wav", "wma", "amr", "opus", "ogg",
    "jpg", "jpeg", "png", "gif",
    "docx", "doc", "xlsx", "xls", "pptx", "ppt", "csv", "txt",
]


def head_probe(url):
    """Reliable probe: ranged GET for bytes 0-1023. Returns (status, content_length).
    Akamai's HEAD is cached and lies about which files exist — a real ranged GET is
    the only way to be sure. We check for 206 Partial Content and a Content-Range header.
    """
    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "20",
             "-A", UA, "-b", COOKIE,
             "-r", "0-1023",
             "-o", "/dev/null",
             "-D", "-",  # dump headers to stdout
             url],
            capture_output=True, text=True, timeout=25,
        )
        status = None
        full_size = None
        for line in r.stdout.split("\n"):
            line = line.strip()
            if line.startswith("HTTP/"):
                parts = line.split()
                if len(parts) > 1:
                    status = parts[1]
            elif line.lower().startswith("content-range:"):
                # Content-Range: bytes 0-1023/4286578688
                try:
                    full_size = int(line.split("/")[-1].strip())
                except Exception:
                    pass
        # Only treat 206 with a Content-Range as a real hit
        if status == "206" and full_size:
            return status, full_size
        return None, None
    except Exception:
        return None, None


def find_extension(bates):
    """Probe DOJ for this Bates with each extension, return (ext, size) for first valid hit."""
    for ext in EXTENSIONS:
        url = f"{BASE}/{bates}.{ext}"
        status, cl = head_probe(url)
        if status == "200" and cl and cl > 1024:
            return ext, cl
    return None, 0


def download_file(url, dest, logger):
    """Download a file via curl with retries. Returns (success, size)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "1800",
                 "-A", UA, "-b", COOKIE,
                 "-o", str(tmp), url],
                capture_output=True, text=True, timeout=1830,
            )
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 1024:
                tmp.rename(dest)
                return True, dest.stat().st_size
            if tmp.exists():
                tmp.unlink()
        except Exception as e:
            logger.warning("Download attempt %d failed for %s: %s", attempt + 1, url, e)
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    return False, 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bates-list", required=True, help="File with Bates IDs (one per line)")
    p.add_argument("--disappeared-csv", default="",
                   help="disappeared.csv with full URLs to also try")
    p.add_argument("--not-found-cache", default="",
                   help="File with Bates IDs known to 404 (skip these)")
    p.add_argument("--out-dir", required=True, help="Destination NATIVES dir")
    p.add_argument("--probe-workers", type=int, default=16)
    p.add_argument("--dl-workers", type=int, default=4)
    p.add_argument("--state-file", required=True)
    p.add_argument("--log", required=True)
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
    )
    logger = logging.getLogger(__name__)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load state
    state = {}
    if Path(args.state_file).exists():
        state = json.loads(Path(args.state_file).read_text())
    probed = state.get("probed", {})  # bates -> {ext, size, url} or {}
    downloaded = set(state.get("downloaded", []))

    def save_state():
        state["probed"] = probed
        state["downloaded"] = sorted(downloaded)
        Path(args.state_file).write_text(json.dumps(state, indent=2))

    # Bates to probe
    bates_list = [
        b.strip() for b in Path(args.bates_list).read_text().split("\n") if b.strip()
    ]
    skip_set = set()
    if args.not_found_cache and Path(args.not_found_cache).exists():
        skip_set = set(
            l.strip() for l in Path(args.not_found_cache).read_text().split("\n") if l.strip()
        )

    to_probe = [b for b in bates_list if b not in skip_set and b not in probed]
    logger.info(
        "Probing %d Bates IDs (%d in not-found cache, %d already probed)",
        len(to_probe), len(bates_list) - len(to_probe) - len(probed), len(probed),
    )

    # Phase 1: probe
    found_count = 0
    with ThreadPoolExecutor(max_workers=args.probe_workers) as pool:
        futures = {pool.submit(find_extension, b): b for b in to_probe}
        for i, future in enumerate(as_completed(futures)):
            bates = futures[future]
            try:
                ext, size = future.result()
            except Exception as e:
                ext, size = None, 0
                logger.debug("Probe error for %s: %s", bates, e)
            if ext:
                probed[bates] = {"ext": ext, "size": size}
                found_count += 1
            else:
                probed[bates] = {}
            if (i + 1) % 25 == 0:
                logger.info(
                    "Probe progress: %d/%d scanned, %d found so far",
                    i + 1, len(to_probe), found_count,
                )
                save_state()

    save_state()
    logger.info("Probe complete. Found %d/%d available on DOJ.",
                found_count, len(bates_list))

    # Build download list
    download_queue = []
    total_bytes = 0
    for bates, info in probed.items():
        if not info:
            continue
        ext = info["ext"]
        url = f"{BASE}/{bates}.{ext}"
        dest = out_dir / f"{bates}.{ext}"
        if dest.exists() and dest.stat().st_size >= info.get("size", 0) * 0.99:
            downloaded.add(bates)
            continue
        download_queue.append((bates, url, dest, info.get("size", 0)))
        total_bytes += info.get("size", 0)

    # Phase 1b: disappeared.csv (full URLs already known)
    if args.disappeared_csv and Path(args.disappeared_csv).exists():
        with open(args.disappeared_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row["url"]
                # Extract filename from URL
                fname = url.rsplit("/", 1)[-1].replace("%20", " ")
                fname = fname.replace(" ", "_")
                dest = out_dir / fname
                bates = fname.rsplit(".", 1)[0]
                size = int(row.get("content_length", 0))
                if dest.exists() and dest.stat().st_size >= size * 0.99:
                    downloaded.add(bates)
                    continue
                download_queue.append((bates, url, dest, size))
                total_bytes += size

    logger.info(
        "Download queue: %d files, total ~%.2f GB",
        len(download_queue), total_bytes / 1e9,
    )

    # Phase 2: download (parallel but limited)
    start = time.time()
    completed = 0
    failed = 0
    bytes_got = 0

    with ThreadPoolExecutor(max_workers=args.dl_workers) as pool:
        futures = {
            pool.submit(download_file, url, dest, logger): (bates, url, dest, size)
            for (bates, url, dest, size) in download_queue
        }
        for future in as_completed(futures):
            bates, url, dest, expected = futures[future]
            try:
                ok, sz = future.result()
            except Exception as e:
                ok, sz = False, 0
                logger.warning("Download exception for %s: %s", url, e)
            if ok:
                downloaded.add(bates)
                bytes_got += sz
                completed += 1
                logger.info("OK (%.1f MB): %s", sz / 1e6, dest.name)
            else:
                failed += 1
                logger.warning("FAIL: %s", url)

            if (completed + failed) % 10 == 0:
                save_state()
                elapsed = time.time() - start
                rate = bytes_got / elapsed / 1e6 if elapsed else 0
                logger.info(
                    "DL progress: %d ok, %d fail, %.2f GB @ %.1f MB/s",
                    completed, failed, bytes_got / 1e9, rate,
                )

    save_state()
    logger.info(
        "=== DONE === probed=%d available=%d downloaded=%d failed=%d total=%.2f GB in %.1f min",
        len(probed), sum(1 for v in probed.values() if v),
        completed, failed, bytes_got / 1e9, (time.time() - start) / 60,
    )


if __name__ == "__main__":
    main()
