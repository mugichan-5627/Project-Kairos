from __future__ import annotations

import json
import math
import numbers
import os
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "fund_manager_tracker"
if str(TRACKER) not in sys.path:
    sys.path.insert(0, str(TRACKER))

from db_setup import initialize_database
from src.utils.db import read_sql

try:
    import pandas as pd
except ImportError:
    pd = None


def query_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    parsed = urlparse(handler.path)
    raw = parse_qs(parsed.query)
    return {k: v[0] for k, v in raw.items() if v}


def _allowed_origin(handler: BaseHTTPRequestHandler) -> str | None:
    origin = handler.headers.get("Origin")
    if not origin:
        return None
    configured = [
        item.strip().rstrip("/")
        for item in os.getenv("KAIROS_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    ]
    parsed = urlparse(origin)
    is_local = parsed.hostname in {"localhost", "127.0.0.1"} and parsed.scheme in {"http", "https"}
    if origin.rstrip("/") in configured or (not configured and is_local):
        return origin
    return None


def apply_cors(handler: BaseHTTPRequestHandler) -> None:
    allowed = _allowed_origin(handler)
    if not allowed:
        return
    handler.send_header("Access-Control-Allow-Origin", allowed)
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def _bearer_token(handler: BaseHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return handler.headers.get("X-Kairos-Admin-Token", "").strip()


def require_admin(handler: BaseHTTPRequestHandler) -> bool:
    """Gate privileged actions (pipeline runs, outbound email) behind a shared secret.

    Fail-closed: if KAIROS_ADMIN_TOKEN is unset the action is only allowed for
    local development requests (localhost Host header), never in production.
    Returns True when authorized; otherwise sends a 401 and returns False.
    """
    import hmac

    token = os.getenv("KAIROS_ADMIN_TOKEN", "")
    supplied = _bearer_token(handler)
    if token and hmac.compare_digest(supplied, token):
        return True
    host = (handler.headers.get("Host") or "").split(":")[0].lower()
    if not token and host in {"localhost", "127.0.0.1"}:
        return True
    send_error(
        handler,
        code="unauthorized",
        message="This action requires an admin token.",
        status=401,
        hint="Send 'Authorization: Bearer <KAIROS_ADMIN_TOKEN>' or set the header X-Kairos-Admin-Token.",
    )
    return False


def require_cron_auth(handler: BaseHTTPRequestHandler) -> bool:
    """Verify Vercel cron invocations. When CRON_SECRET is set, Vercel sends it
    as 'Authorization: Bearer <CRON_SECRET>'. The admin token also passes so the
    endpoint stays manually triggerable by the owner."""
    import hmac

    secret = os.getenv("CRON_SECRET", "")
    supplied = _bearer_token(handler)
    if not secret:
        return require_admin(handler)
    if hmac.compare_digest(supplied, secret):
        return True
    admin = os.getenv("KAIROS_ADMIN_TOKEN", "")
    if admin and hmac.compare_digest(supplied, admin):
        return True
    send_error(handler, code="unauthorized", message="Invalid or missing cron secret.", status=401)
    return False


def read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int = 64_000) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length > max_bytes:
        raise ValueError(f"Request body too large. Limit is {max_bytes} bytes.")
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw or b"{}")
    except Exception as exc:
        raise ValueError("Invalid JSON body.") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object.")
    return payload


def _sanitize_for_json(obj):
    """Recursively coerce pandas/numpy values into strict JSON-safe values."""
    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if pd is not None:
        try:
            missing = pd.isna(obj)
            if isinstance(missing, (bool, numbers.Integral)) and bool(missing):
                return None
        except (TypeError, ValueError):
            pass
    try:
        if math.isnan(obj) or math.isinf(obj):
            return None
    except (TypeError, ValueError):
        pass
    try:
        if obj.item is not None:
            return _sanitize_for_json(obj.item())
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        if obj.isoformat is not None and isinstance(obj, (date, datetime)):
            return obj.isoformat()
    except (AttributeError, TypeError, ValueError):
        pass
    if isinstance(obj, numbers.Integral):
        return int(obj)
    if isinstance(obj, numbers.Real):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    try:
        if obj != obj:
            return None
    except Exception:
        pass
    return obj


def send_json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Cache-Control", "no-store")
    apply_cors(handler)
    handler.end_headers()
    clean = _sanitize_for_json(payload)
    handler.wfile.write(json.dumps(clean, default=str, allow_nan=False).encode("utf-8"))
    handler.wfile.flush()


def send_error(handler: BaseHTTPRequestHandler, code: str, message: str, status: int = 400, hint: str | None = None) -> None:
    """Uniform error envelope. Every Kairos API endpoint should use this."""
    body: dict = {"error": {"code": code, "message": message}}
    if hint:
        body["error"]["hint"] = hint
    send_json(handler, body, status)

_initialized = False


def bootstrap() -> None:
    global _initialized
    if not _initialized:
        initialize_database()
        _initialized = True


def ensure_scheme_master_loaded() -> dict:
    """Populate scheme_master on first use so scheme selectors are not empty."""
    count = read_sql("SELECT COUNT(*) AS n FROM scheme_master")
    rows = int(count.iloc[0]["n"]) if not count.empty else 0
    if rows:
        return {"loaded": False, "rows": rows}
    try:
        from src.data.amfi_loader import AMFILoader

        loaded = AMFILoader().refresh_scheme_master()
        return {"loaded": True, "rows": int(loaded)}
    except Exception as exc:
        return {"loaded": False, "rows": 0, "error": str(exc)}
