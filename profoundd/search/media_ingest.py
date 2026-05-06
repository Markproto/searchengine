"""
Media ingest helpers for the Audio→Bob pipeline.

Three entry paths so admins can paste any URL:
  1. Direct audio URL (.mp3 / .m4a / .wav etc.) — `fetch_audio_bytes`
  2. Podcast RSS feed URL — `parse_podcast_feed` returns an episode list
  3. YouTube / video URL — `extract_video_audio` shells out to yt-dlp
     (requires yt-dlp installed in the container)

All three feed into the existing audio_transcribe.transcribe_audio()
which uses Groq Whisper. Output is then stored as an AudioTranscript and
the existing /admin/bob/audio/<id> review page picks it up.
"""
import logging
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "ProfounddBot/1.0 (audio ingest; +https://profoundd.com/bot)"

# Mirror of audio_transcribe.MAX_FILE_BYTES so we reject early
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB Groq limit

# Common audio file extensions / Content-Type patterns
AUDIO_EXTENSIONS = (".mp3", ".m4a", ".mp4", ".wav", ".webm", ".ogg", ".flac", ".mpga", ".mpeg", ".aac")
AUDIO_CONTENT_TYPES = ("audio/", "video/mp4", "video/mpeg", "application/ogg")


def is_youtube_url(url):
    """True if URL looks like a YouTube watch / shorts / youtu.be link."""
    p = urlparse(url)
    host = (p.netloc or "").lower().replace("www.", "")
    return host in ("youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com")


def looks_like_podcast_feed(url, content_type=""):
    """Heuristic: RSS-shaped URL or response Content-Type."""
    u = url.lower()
    if u.endswith(".xml") or u.endswith(".rss"):
        return True
    if "/feed" in u or "/rss" in u or "feeds." in u:
        return True
    ct = (content_type or "").lower()
    if "rss" in ct or "atom" in ct or "xml" in ct:
        return True
    return False


def fetch_audio_bytes(url, max_bytes=MAX_AUDIO_BYTES, timeout=120):
    """GET an audio URL and return (bytes, content_type, suggested_filename).

    Raises ValueError if the URL doesn't look like audio or exceeds size.
    """
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        stream=True,
    )
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "")
    clen = resp.headers.get("Content-Length")
    if clen and int(clen) > max_bytes:
        resp.close()
        raise ValueError(
            f"Audio file too large: {int(clen)} bytes (max {max_bytes}). "
            "Re-encode at lower bitrate, trim, or split before re-trying."
        )

    # Read up to max_bytes
    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=131072):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            resp.close()
            raise ValueError(
                f"Audio file exceeded size cap mid-download (>{max_bytes} bytes)."
            )
        chunks.append(chunk)
    resp.close()
    data = b"".join(chunks)

    # Sanity-check we got audio
    if not (any(t in ctype.lower() for t in AUDIO_CONTENT_TYPES)
            or url.lower().endswith(AUDIO_EXTENSIONS)):
        raise ValueError(
            f"URL doesn't look like audio (content-type={ctype!r}). "
            "If this is a podcast page, paste the RSS feed URL or direct MP3 link."
        )

    # Guess a filename from the URL path
    parsed = urlparse(url)
    name = parsed.path.rsplit("/", 1)[-1] or "audio.mp3"
    if "." not in name:
        # Pick extension from content-type
        if "mpeg" in ctype or "mp3" in ctype:
            name += ".mp3"
        elif "m4a" in ctype or "mp4" in ctype:
            name += ".m4a"
        elif "wav" in ctype:
            name += ".wav"
        else:
            name += ".mp3"
    return data, ctype, name[:200]


def parse_podcast_feed(url, timeout=60):
    """Fetch and parse a podcast RSS feed. Returns list of episodes:
        [{title, pub_date, duration_seconds, audio_url, description, guid}, ...]

    Raises ValueError if the URL isn't a parseable RSS feed.
    """
    resp = requests.get(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml"},
        timeout=timeout,
    )
    resp.raise_for_status()
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise ValueError(f"Could not parse RSS XML: {e}")

    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    episodes = []
    for item in root.findall(".//item"):
        enc = item.find("enclosure")
        audio_url = enc.get("url") if enc is not None else ""
        if not audio_url:
            # Some feeds put audio under media:content
            mc = item.find(".//{http://search.yahoo.com/mrss/}content")
            if mc is not None:
                audio_url = mc.get("url", "")
        if not audio_url:
            continue
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        duration = (item.findtext("itunes:duration", namespaces=ns) or "").strip()
        description = (item.findtext("description") or "").strip()
        # Strip HTML for clean preview
        description = re.sub(r"<[^>]+>", " ", description)
        description = re.sub(r"\s+", " ", description).strip()
        guid = (item.findtext("guid") or "").strip()
        # Convert duration to seconds (HH:MM:SS or MM:SS or raw seconds)
        dur_seconds = None
        if duration:
            parts = duration.split(":")
            try:
                if len(parts) == 3:
                    dur_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    dur_seconds = int(parts[0]) * 60 + int(parts[1])
                else:
                    dur_seconds = int(duration)
            except ValueError:
                pass
        episodes.append({
            "title": title[:300],
            "pub_date": pub_date,
            "duration_seconds": dur_seconds,
            "audio_url": audio_url,
            "description": description[:600],
            "guid": guid[:200],
        })
    if not episodes:
        raise ValueError(
            "RSS parsed but no <enclosure> audio URLs found. "
            "Is this an actual podcast feed?"
        )
    feed_title = (root.findtext(".//channel/title") or "").strip()
    feed_link = (root.findtext(".//channel/link") or "").strip()
    return {
        "feed_title": feed_title[:300],
        "feed_link": feed_link[:500],
        "episodes": episodes,
    }


def yt_dlp_available():
    """Is yt-dlp installed in PATH?"""
    return shutil.which("yt-dlp") is not None


def extract_video_audio(url, max_bytes=MAX_AUDIO_BYTES):
    """Use yt-dlp to extract the audio track from a YouTube / video URL.

    Returns (bytes, content_type, filename). Raises if yt-dlp is missing
    or download fails. Output format is m4a (cheap, Groq-supported).
    """
    if not yt_dlp_available():
        raise RuntimeError(
            "yt-dlp is not installed in this container. Add it to "
            "requirements.txt or the Dockerfile to enable video ingest."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out_template = f"{tmp}/audio.%(ext)s"
        cmd = [
            "yt-dlp",
            "-f", "bestaudio[filesize<25M]/bestaudio",
            "-x", "--audio-format", "m4a",
            "--audio-quality", "5",
            "-o", out_template,
            "--no-playlist",
            "--quiet",
            "--no-warnings",
            url,
        ]
        try:
            subprocess.run(cmd, check=True, timeout=600,
                           capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"yt-dlp failed: {e.stderr[:300]}")
        # Find the produced file
        import os
        candidates = [f for f in os.listdir(tmp) if f.startswith("audio.")]
        if not candidates:
            raise RuntimeError("yt-dlp completed but no output file found.")
        path = os.path.join(tmp, candidates[0])
        size = os.path.getsize(path)
        if size > max_bytes:
            raise ValueError(
                f"Extracted audio too large: {size} bytes (max {max_bytes}). "
                "Try a shorter video."
            )
        with open(path, "rb") as f:
            data = f.read()
        return data, "audio/mp4", f"yt_{candidates[0]}"


def ingest_url(url):
    """Single-shot ingest: classify URL and return audio bytes + filename.
    Returns (bytes, content_type, filename, source_kind) where source_kind
    is one of: 'audio' | 'youtube'. Raises for podcast-feed URLs (caller
    should redirect to the episode picker)."""
    if is_youtube_url(url):
        data, ctype, name = extract_video_audio(url)
        return data, ctype, name, "youtube"

    # Probe with HEAD first to detect podcast feeds vs direct audio
    try:
        head = requests.head(
            url, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=30,
        )
        head_ct = head.headers.get("Content-Type", "")
    except Exception:
        head_ct = ""

    if looks_like_podcast_feed(url, head_ct):
        raise ValueError(
            "This URL looks like a podcast RSS feed. Use the "
            "podcast feed form to pick an episode instead."
        )

    data, ctype, name = fetch_audio_bytes(url)
    return data, ctype, name, "audio"
