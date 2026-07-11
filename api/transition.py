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
        event_id = query_params(self).get("event_id")
        if not event_id:
            send_error(
                self,
                code="missing_parameter",
                message="event_id is required",
                status=400,
                hint="Pass ?event_id=<int>. Use /api/feed to discover event_ids.",
            )
            return
        event = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        did = read_sql("SELECT * FROM factor_matched_did WHERE event_id=? ORDER BY created_at DESC LIMIT 1", (event_id,))
        forecast = read_sql("SELECT * FROM transition_impact_forecasts WHERE event_id=? ORDER BY created_at DESC LIMIT 1", (event_id,))
        attr = read_sql("SELECT * FROM attribution_results WHERE event_id=? ORDER BY created_at DESC", (event_id,))
        send_json(self, {"event": event.to_dict("records"), "factor_matched_did": did.to_dict("records"), "forecast": forecast.to_dict("records"), "attribution": attr.to_dict("records")})
