from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, query_params, read_sql, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        params = query_params(self)
        timeframe = params.get("timeframe", "All")

        query = """
            SELECT ce.event_id, ce.scheme_code, ce.manager_name, ce.manager_key,
                   ce.change_type, ce.change_date, ce.pre_tenure_months,
                   ce.predecessor_manager, ce.successor_manager, ce.amc_name,
                   ce.category, ce.confidence_score, ce.status, ce.created_at,
                   ms.composite_score, ms.investor_risk_score, ms.alert_text,
                   ms.score_warning, ms.label
            FROM change_events ce
            INNER JOIN (SELECT DISTINCT scheme_code FROM nav_history) nh ON ce.scheme_code = nh.scheme_code
            LEFT JOIN manager_scorecards ms ON ce.event_id=ms.event_id
        """
        
        # Apply timeframe filter matching feed.py logic
        if timeframe == "12 Months":
            query += " WHERE ce.change_date >= date('now', '-12 months')"
        elif timeframe == "36 Months":
            query += " WHERE ce.change_date >= date('now', '-36 months')"

        query += " ORDER BY ce.change_date DESC"

        feed = read_sql(query)
        send_json(self, {"feed": feed.to_dict("records")})
