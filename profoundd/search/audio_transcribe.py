"""
Audio transcription via Groq Whisper (free tier).

Groq's audio endpoint is OpenAI-compatible, so we call it with plain requests.
Docs: https://console.groq.com/docs/speech-to-text
"""
import logging

import requests

logger = logging.getLogger(__name__)

GROQ_AUDIO_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_MODEL = "whisper-large-v3-turbo"

SUPPORTED_EXTENSIONS = {
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac",
}

MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB per Groq's limit


def transcribe_audio(file_bytes, filename, api_key, model=DEFAULT_MODEL,
                     language=None, prompt=None):
    """Transcribe an audio file via Groq Whisper.

    Returns dict: {text, duration, language}
    Raises on failure.
    """
    if not api_key:
        raise ValueError("Groq API key is required")
    if not file_bytes:
        raise ValueError("No file bytes provided")
    if len(file_bytes) > MAX_FILE_BYTES:
        raise ValueError(f"File too large ({len(file_bytes)} bytes, max {MAX_FILE_BYTES})")

    files = {"file": (filename, file_bytes)}
    data = {
        "model": model,
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    logger.info("Transcribing %s (%d bytes) via Groq %s", filename, len(file_bytes), model)
    resp = requests.post(
        GROQ_AUDIO_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        files=files,
        data=data,
        timeout=600,  # allow up to 10 min for long files
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq returned {resp.status_code}: {resp.text[:300]}")
    result = resp.json()
    return {
        "text": (result.get("text") or "").strip(),
        "duration": result.get("duration"),
        "language": result.get("language"),
    }
