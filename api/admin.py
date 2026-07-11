from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import apply_cors, bootstrap, read_json_body, require_admin, send_error, send_json


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        apply_cors(self)
        self.end_headers()

    def do_POST(self):
        bootstrap()
        if not require_admin(self):
            return
        try:
            payload = read_json_body(self, max_bytes=8_000)
        except ValueError as exc:
            send_error(self, code="invalid_json", message=str(exc), status=400)
            return
        task = payload.get("task")

        try:
            if task == "run_pipeline":
                from monitor import run_daily_monitor, run_analytics_agent
                pipeline_res = run_daily_monitor()
                analytics_res = run_analytics_agent()
                send_json(self, {
                    "status": "success",
                    "message": "Incremental pipeline and analytics runner completed successfully.",
                    "pipeline": pipeline_res,
                    "analytics": analytics_res
                })
            elif task == "verify_integrity":
                from src.data.verification import validate_manager_tenures
                report = validate_manager_tenures(output_csv="data_quality_report.csv", head_check=False)
                send_json(self, {
                    "status": "success",
                    "message": f"Database integrity verified successfully. Audited {len(report)} validation cases.",
                    "checks_run": len(report)
                })
            else:
                send_json(self, {"status": "error", "message": f"Unknown administrative task: {task}"}, status=400)
        except Exception as e:
            # Log the traceback server-side; never leak it to the client.
            import logging
            import traceback
            logging.getLogger("kairos.admin").error("admin task failed: %s", traceback.format_exc())
            send_json(self, {"status": "error", "message": str(e)}, status=500)
