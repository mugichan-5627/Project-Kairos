from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from _kairos import bootstrap, require_cron_auth, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        if not require_cron_auth(self):
            return
        from monitor import run_daily_monitor, safe_job_wrapper

        result = safe_job_wrapper(run_daily_monitor, "daily_monitor")
        send_json(self, {"status": "ok", "schedule": "daily_safe", "result": result})
