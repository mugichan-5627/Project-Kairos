from __future__ import annotations

from http.server import BaseHTTPRequestHandler

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, read_sql, send_json
from src.config_checks import validate_environment


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        bootstrap()

        status = read_sql("SELECT * FROM source_status ORDER BY source_name")

        totals = read_sql(
            """
            SELECT
              (SELECT COUNT(*) FROM scheme_master) AS total_schemes,
              (SELECT COUNT(DISTINCT scheme_code) FROM manager_scheme_history) AS schemes_with_manager_history,
              (SELECT COUNT(DISTINCT manager_name) FROM manager_scheme_history) AS distinct_managers,
              (SELECT COUNT(*) FROM manager_tenure) AS canonical_tenures,
              (SELECT COUNT(*) FROM manager_claims WHERE status='pending') AS pending_claims,
              (SELECT COUNT(*) FROM did_diagnostics WHERE diagnostic_label='low_confidence_parallel_trends_failed') AS did_warnings,
              (SELECT COUNT(*) FROM failed_scrapes) AS failed_scrapes,
              (SELECT COUNT(*) FROM request_cache) AS cached_requests,
              (SELECT COUNT(*) FROM change_events) AS total_transitions,
              (SELECT COUNT(DISTINCT amc_name) FROM manager_scheme_history) AS tracked_amcs
            """
        )

        quality = read_sql("SELECT * FROM data_quality_log ORDER BY created_at DESC LIMIT 100")
        failed = read_sql("SELECT * FROM failed_scrapes ORDER BY created_at DESC LIMIT 100")
        heartbeat = read_sql("SELECT * FROM agent_heartbeat ORDER BY agent_name")

        send_json(self, {
            "environment": validate_environment().to_dict(),
            "agent_heartbeat": heartbeat.to_dict("records"),
            "source_status": status.to_dict("records"),
            "totals": totals.to_dict("records")[0] if not totals.empty else {},
            "data_quality_log": quality.to_dict("records"),
            "failed_scrapes": failed.to_dict("records")
        })
