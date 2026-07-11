from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import apply_cors, bootstrap, read_json_body, read_sql, require_admin, send_error, send_json
from src.alerts.investor_alerts import smtp_status


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        apply_cors(self)
        self.end_headers()

    def do_GET(self):
        bootstrap()
        smtp = smtp_status()
        # Deliberately excludes body (full email HTML) and raw recipient PII.
        alerts = read_sql(
            """
            SELECT alert_id, event_id, channel, severity, subject, delivery_status,
                   alert_type, scheme_code, created_at, sent_at
            FROM alert_log ORDER BY created_at DESC LIMIT 50
            """
        )
        forecasts = read_sql("SELECT * FROM transition_impact_forecasts ORDER BY created_at DESC LIMIT 50")
        send_json(self, {
            "alerts": alerts.to_dict("records"),
            "recent_forecasts": forecasts.to_dict("records"),
            "smtp_configured": smtp["configured"],
            "smtp_status": smtp,
        })

    def do_POST(self):
        bootstrap()
        if not require_admin(self):
            return
        try:
            data = read_json_body(self, max_bytes=8_000)
        except ValueError as exc:
            send_error(self, code="invalid_request", message=str(exc), status=400)
            return
            
        email = data.get("email", "demo@projectkairos.local")
        event_id = data.get("event_id")
        
        if not event_id:
            df = read_sql("SELECT event_id FROM change_events ORDER BY change_date DESC LIMIT 1")
            if not df.empty:
                event_id = int(df.iloc[0]["event_id"])
            else:
                event_id = 11
                
        from src.alerts.alert_agent import AlertAgent
        agent = AlertAgent()
        result = agent.send_email_alert(email, int(event_id))
        send_json(self, result)
