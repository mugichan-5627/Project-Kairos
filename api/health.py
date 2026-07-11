from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload: dict = {"status": "ok", "project": "kairos"}
        if "diag" in (self.path or ""):
            here = Path(__file__).resolve().parent
            root = here.parent
            payload["diag"] = {
                "here": str(here),
                "sys_path": sys.path[:6],
                "api_dir": sorted(p.name for p in here.glob("*"))[:30],
                "root_dir": sorted(p.name for p in root.glob("*"))[:30],
                "kairos_helper_exists": (here / "_kairos.py").exists(),
                "tracker_exists": (root / "fund_manager_tracker").is_dir(),
                "db_exists": (root / "fund_manager_tracker" / "fund_data.db").exists(),
                "cwd": os.getcwd(),
            }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
        self.wfile.flush()
