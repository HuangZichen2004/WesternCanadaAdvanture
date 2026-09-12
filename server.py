from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PHOTO_DIR = ROOT / "photos"
STATE_FILE = DATA_DIR / "trip.json"
MAX_PHOTO_BYTES = 40 * 1024 * 1024
PHOTO_RE = re.compile(r"^data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=\s]+)$")


def send_json(handler: SimpleHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: SimpleHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length > 80 * 1024 * 1024:
        raise ValueError("request too large")
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise ValueError("invalid id")
    return value


def photo_path(url: str) -> Path:
    prefix = "/photos/"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise ValueError("photo is not stored on this server")
    candidate = (ROOT / url.removeprefix("/")).resolve()
    candidate.relative_to(PHOTO_DIR.resolve())
    return candidate


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            if not STATE_FILE.exists():
                send_json(self, 404, {"error": "state not found"})
                return
            try:
                send_json(self, 200, json.loads(STATE_FILE.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                send_json(self, 500, {"error": "state unavailable"})
            return
        super().do_GET()

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/state":
            send_json(self, 404, {"error": "not found"})
            return
        try:
            state = read_json(self)
            DATA_DIR.mkdir(exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="trip-", suffix=".json", dir=DATA_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, STATE_FILE)
            send_json(self, 200, {"ok": True})
        except Exception as error:
            send_json(self, 400, {"error": str(error)})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/photo":
            send_json(self, 404, {"error": "not found"})
            return
        try:
            payload = read_json(self)
            day_id = safe_id(str(payload["dayId"]))
            step_id = safe_id(str(payload["stepId"]))
            match = PHOTO_RE.fullmatch(str(payload["data"]))
            if not match:
                raise ValueError("unsupported image")
            raw = base64.b64decode(match.group(2), validate=True)
            if len(raw) > MAX_PHOTO_BYTES:
                raise ValueError("image too large")
            extension = "jpg" if match.group(1) == "jpeg" else match.group(1)
            folder = PHOTO_DIR / day_id / step_id
            folder.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.{extension}"
            target = folder / filename
            target.write_bytes(raw)
            send_json(self, 201, {"url": f"/photos/{day_id}/{step_id}/{filename}"})
        except Exception as error:
            send_json(self, 400, {"error": str(error)})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/photo":
                target = photo_path(unquote(query["path"][0]))
                if target.exists():
                    target.unlink()
                send_json(self, 200, {"ok": True})
                return
            if parsed.path == "/api/step":
                day_id = safe_id(query["dayId"][0])
                step_id = safe_id(query["stepId"][0])
                folder = (PHOTO_DIR / day_id / step_id).resolve()
                folder.relative_to(PHOTO_DIR.resolve())
                if folder.exists():
                    shutil.rmtree(folder)
                send_json(self, 200, {"ok": True})
                return
        except Exception as error:
            send_json(self, 400, {"error": str(error)})
            return
        send_json(self, 404, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    DATA_DIR.mkdir(exist_ok=True)
    PHOTO_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Maple Canada Journal: http://localhost:8000")
    server.serve_forever()
