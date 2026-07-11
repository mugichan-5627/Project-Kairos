from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler
from _kairos import bootstrap, read_sql, send_json


def _bg_load_scheme_master():
    try:
        from src.data.amfi_loader import AMFILoader
        AMFILoader().refresh_scheme_master()
    except Exception:
        pass


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        action = params.get("action")
        
        # Check current rows
        try:
            count_res = read_sql("SELECT COUNT(*) AS n FROM scheme_master")
            scheme_master_rows = int(count_res.iloc[0]["n"]) if not count_res.empty else 0
        except Exception:
            scheme_master_rows = 0

        if action == "load_scheme_master":
            if scheme_master_rows == 0:
                # Trigger in background thread
                thread = threading.Thread(target=_bg_load_scheme_master)
                thread.daemon = True
                thread.start()
                send_json(self, {
                    "status": "loading",
                    "message": "AMFI scheme master load initiated in background.",
                    "scheme_master_rows": scheme_master_rows
                })
                return
            else:
                send_json(self, {
                    "status": "already_loaded",
                    "message": "AMFI scheme master is already populated.",
                    "scheme_master_rows": scheme_master_rows
                })
                return

        send_json(self, {
            "scheme_master_rows": scheme_master_rows
        })
