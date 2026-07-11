from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, query_params, read_sql, send_error, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        code = query_params(self).get("scheme_code")
        if not code:
            send_error(
                self,
                code="missing_parameter",
                message="scheme_code is required",
                status=400,
                hint="Pass ?scheme_code=<AMFI code>. Use /api/lists to discover valid codes.",
            )
            return
        scheme = read_sql("SELECT * FROM scheme_master WHERE scheme_code=?", (code,))
        tenures = read_sql(
            """
            SELECT mt.*, mi.canonical_name
            FROM manager_tenure mt JOIN manager_identity mi ON mi.manager_id=mt.manager_id
            WHERE mt.scheme_code=? ORDER BY COALESCE(mt.start_date, mt.end_date)
            """,
            (code,),
        )
        events = read_sql("SELECT * FROM change_events WHERE scheme_code=? ORDER BY change_date DESC", (code,))
        current = read_sql("SELECT * FROM current_manager_snapshot WHERE scheme_code=? ORDER BY confirmed_date DESC", (code,))
        send_json(self, {"scheme": scheme.to_dict("records"), "tenures": tenures.to_dict("records"), "events": events.to_dict("records"), "current_manager": current.to_dict("records")})
