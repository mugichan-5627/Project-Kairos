from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, require_cron_auth, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        if not require_cron_auth(self):
            return
        from monitor import run_daily_monitor, safe_job_wrapper

        result = safe_job_wrapper(run_daily_monitor, "daily_monitor")
        send_json(self, {"status": "ok", "schedule": "daily_safe", "result": result})
