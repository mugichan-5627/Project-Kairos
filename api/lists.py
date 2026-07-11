from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, ensure_scheme_master_loaded, read_sql, send_json


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()
        # Check count without triggering blocking load
        try:
            count_res = read_sql("SELECT COUNT(*) AS n FROM scheme_master")
            scheme_master_rows = int(count_res.iloc[0]["n"]) if not count_res.empty else 0
        except Exception:
            scheme_master_rows = 0
        scheme_master_status = {"loaded": False, "rows": scheme_master_rows}

        # Query distinct managers in history — prioritise those with scorecards
        managers = read_sql("""
            SELECT DISTINCT msh.manager_key, msh.manager_name,
                   CASE WHEN ms.manager_key IS NOT NULL THEN 0 ELSE 1 END AS sort_order
            FROM manager_scheme_history msh
            LEFT JOIN (
                SELECT DISTINCT manager_key FROM manager_scorecards
            ) ms ON ms.manager_key = msh.manager_key
            ORDER BY sort_order, msh.manager_name
        """)

        # Only show schemes that actually have NAV history (avoids 37k dead fund entries)
        schemes = read_sql("""
            SELECT sm.scheme_code, sm.scheme_name, sm.amc_name
            FROM scheme_master sm
            INNER JOIN (
                SELECT DISTINCT scheme_code FROM nav_history
            ) nh ON nh.scheme_code = sm.scheme_code
            ORDER BY sm.amc_name, sm.scheme_name
        """)
        if schemes.empty:
            schemes = read_sql("""
                SELECT scheme_code, scheme_name, amc_name
                FROM scheme_master
                WHERE amc_name IS NOT NULL
                ORDER BY amc_name, scheme_name
            """)

        send_json(self, {
            "managers": managers.to_dict("records"),
            "schemes": schemes.to_dict("records"),
            "scheme_master_status": scheme_master_status,
            "warning": scheme_master_status.get("error")
        })
