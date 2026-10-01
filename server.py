#!/usr/bin/env python3
"""addon.unstuck-coach local-service entry (http-json on 127.0.0.1:4893).

ResonantOS add-on contract: protocol http-json, healthCommand unstuckcoach.status.
Wraps the FROZEN vendored Unstuck Coach method (vendor/, hash-pinned via
method-pins.json) through coach_router.py — pure in-process Python: no
process spawning, no shell, no network egress, no secrets on argv.

Turn transcripts are persisted under var/ and are home-path-redacted before
acknowledgement (the user's stuck point may legitimately contain absolute
paths; redaction, not failure).

Validation: bounded bodies (<=64KB, 413+close), no unknown fields anywhere,
control characters rejected in string fields except tab and newline (multi-
line brain dumps are canonical input for this method), lying Content-Length
cannot pin a thread (socket timeout 30s -> 408).

Exit codes: 0 normal stop; 78 port bind failure.
"""

import json
import os
import re
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ADDON_ROOT)

import coach_router  # noqa: E402  (deterministic method router, in-process)

PORT = int(os.environ.get("UNSTUCK_PORT", "4893"))  # dev override; manifest port 4893 is the contract
MAX_BODY = 64 * 1024
MAX_STR = 4096  # stuck_point upper bound (multi-line dumps welcome up to here)
METHOD_VERSION = "0.1.0"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_SESSIONS = 64
_MAX_TURNS_PER_SESSION = 50

_state = {
    "sessions": {},     # session_id -> turn count
    "turns_served": 0,
    "last_session_id": None,
}
_lock = threading.Lock()
_router = coach_router.CoachRouter()


def _validate_coach_params(params):
    if not isinstance(params, dict):
        return None, "params must be an object"
    stuck_point = params.get("stuck_point")
    if not isinstance(stuck_point, str) or not (0 < len(stuck_point) <= MAX_STR):
        return None, f"stuck_point must be a string of 1..{MAX_STR} characters"
    bad = [ch for ch in stuck_point if ord(ch) < 0x20 and ch not in ("\n", "\t") or ord(ch) == 0x7F]
    if bad:
        return None, "stuck_point contains control characters (tab and newline are allowed)"
    session_id = params.get("session_id")
    if session_id is None:
        session_id = "sess-" + uuid.uuid4().hex[:12]
    if not isinstance(session_id, str) or len(session_id) > 64 or not SESSION_ID_RE.match(session_id) or session_id.startswith("."):
        return None, "session_id may only contain ASCII letters, digits, dot, underscore, hyphen"
    for key in params:
        if key not in ("stuck_point", "session_id"):
            return None, f"unknown field: {key}"
    return {"stuck_point": stuck_point, "session_id": session_id}, None


def _redact_text(text):
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "~" else text


def _redact_obj(obj):
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, list):
        return [_redact_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact_obj(value) for key, value in obj.items()}
    return obj


def _persist_turn(session_id, turn, record):
    """Write the redacted turn record under var/<session_id>/; best-effort."""
    try:
        out_dir = os.path.join(ADDON_ROOT, "var", session_id)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{turn:04d}-turn.json")
        with open(path, "w") as f:
            json.dump(_redact_obj(record), f, indent=1)
        return os.path.relpath(path, ADDON_ROOT)
    except OSError:
        return None


def _method_commit():
    pins_path = os.path.join(ADDON_ROOT, "method-pins.json")
    try:
        with open(pins_path) as f:
            return json.load(f).get("upstream_commit", "unknown")
    except (OSError, ValueError):
        return "unknown"


def _status(params=None):
    if params is not None:
        if not isinstance(params, dict) or params:
            return None, "status takes no params"
    with _lock:
        return {
            "ok": True,
            "version": METHOD_VERSION,
            "method_commit": _method_commit(),
            "sessions": len(_state["sessions"]),
            "turns_served": _state["turns_served"],
        }, None


def _coach(params):
    parsed, err = _validate_coach_params(params)
    if err:
        return 400, {"error": err}
    stuck_point, session_id = parsed["stuck_point"], parsed["session_id"]
    try:
        route = _router.route(stuck_point)
    except Exception as exc:  # a routing bug must degrade honestly, never crash the service
        return 500, {"error": _redact_text("routing failed: " + str(exc)[:200])}
    with _lock:
        turns = _state["sessions"].get(session_id, 0)
        if session_id not in _state["sessions"] and len(_state["sessions"]) >= _MAX_SESSIONS:
            return 429, {"error": "session table full; restart the service or reuse an existing session_id"}
        if turns >= _MAX_TURNS_PER_SESSION:
            return 429, {"error": "session turn limit reached; start a new session_id"}
        turn = turns + 1
        _state["sessions"][session_id] = turn
        _state["turns_served"] += 1
        _state["last_session_id"] = session_id
    record = {
        "session_id": session_id,
        "turn": turn,
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        **route,
    }
    record["turn_path"] = _persist_turn(session_id, turn, record)  # stored redacted or not at all
    return 200, _redact_obj(record)  # the acknowledgement itself is redacted too


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30  # a lying Content-Length must not pin a thread forever

    def _reply(self, code, payload, close=False):
        if close:
            self.close_connection = True  # never leave undrained bodies on a keep-alive connection
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")  # announce the close; do not leave clients guessing
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            status, _ = _status()
            self._reply(200, status)
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/":
            self._reply(404, {"error": "not found"}, close=True)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"error": "bad content-length"}, close=True)
            return
        if length <= 0 or length > MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400, {"error": "body must be 1..65536 bytes"}, close=True)
            return
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (TimeoutError, socket.timeout, OSError):
            self._reply(408, {"error": "request body incomplete (timeout)"}, close=True)
            return
        except (ValueError, UnicodeDecodeError):
            self._reply(400, {"error": "body must be valid JSON"}, close=True)
            return
        if not isinstance(req, dict):
            self._reply(400, {"error": "body must be a JSON object"}, close=True)
            return
        method = req.get("method")
        params = req.get("params", {})
        for key in req:
            if key not in ("method", "params"):
                self._reply(400, {"error": f"unknown field: {key}"}, close=True)
                return
        if method == "unstuckcoach.status":
            status, err = _status(params)
            if err:
                self._reply(400, {"error": err}, close=True)
            else:
                self._reply(200, status)
        elif method == "unstuckcoach.coach":
            code, payload = _coach(params)
            self._reply(code, payload, close=(code == 500))
        else:
            self._reply(404, {"error": f"unknown method: {method}"})

    def log_message(self, fmt, *args):  # keep service logs quiet and content-free
        sys.stderr.write("unstuck-coach-service: " + (fmt % args) + "\n")


def main():
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        sys.stderr.write(f"unstuck-coach-service: cannot bind 127.0.0.1:{PORT} ({exc}); manifest entrypoint expects this port\n")
        return 78
    sys.stderr.write(f"unstuck-coach-service: listening on http://127.0.0.1:{PORT}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
