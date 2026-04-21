"""
Read-only HTTP file server for Epstein PDFs on Azure7.

Serves files from BASE_DIR (/mnt/backup/epstein-files/extracted) over the
Tailscale network so Apollo9 Flask can stream them to users without copying
the 598GB archive. Bound to the Tailscale IP only — never public.

Requires Authorization: Bearer <EPSTEIN_FILE_SERVER_TOKEN> header.
Rejects anything outside BASE_DIR (path traversal defense) and anything
that isn't a .pdf.
"""
import os
from pathlib import Path

from flask import Flask, abort, request, send_from_directory

BASE_DIR = Path(os.environ.get("BASE_DIR", "/mnt/backup/epstein-files/extracted")).resolve()
TOKEN = os.environ.get("EPSTEIN_FILE_SERVER_TOKEN", "")
BIND_HOST = os.environ.get("BIND_HOST", "100.71.230.11")
BIND_PORT = int(os.environ.get("BIND_PORT", "8090"))

if not TOKEN:
    raise SystemExit("EPSTEIN_FILE_SERVER_TOKEN not set — refusing to start")
if not BASE_DIR.is_dir():
    raise SystemExit(f"BASE_DIR does not exist: {BASE_DIR}")

app = Flask(__name__)


def _authorized():
    header = request.headers.get("Authorization", "")
    return header == f"Bearer {TOKEN}"


@app.route("/health")
def health():
    return {"status": "ok", "base": str(BASE_DIR)}


@app.route("/files/<path:relpath>")
def serve_file(relpath):
    if not _authorized():
        abort(401)

    target = (BASE_DIR / relpath).resolve()
    try:
        target.relative_to(BASE_DIR)
    except ValueError:
        abort(403)

    if target.suffix.lower() != ".pdf":
        abort(403)
    if not target.is_file():
        abort(404)

    rel = target.relative_to(BASE_DIR)
    return send_from_directory(BASE_DIR, str(rel), mimetype="application/pdf")


if __name__ == "__main__":
    app.run(host=BIND_HOST, port=BIND_PORT, threaded=True)
