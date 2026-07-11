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
        try:
            bootstrap()
            params = query_params(self)
            manager_key = params.get("manager_key")
            if not manager_key:
                send_error(
                    self,
                    code="missing_parameter",
                    message="manager_key is required",
                    status=400,
                    hint="Pass ?manager_key=<canonical-name> (see /api/lists for valid keys).",
                )
                return

            try:
                history = read_sql(
                    """
                    SELECT h.manager_name, h.amc_name, COALESCE(sm.scheme_name, h.amc_name || ' Scheme ' || h.scheme_code) AS scheme_name, h.start_date, COALESCE(h.end_date, date('now')) AS end_date,
                           h.confidence_score, h.source
                    FROM manager_scheme_history h
                    LEFT JOIN scheme_master sm ON sm.scheme_code=h.scheme_code
                    WHERE h.manager_key=?
                    ORDER BY h.start_date
                    """,
                    (manager_key,),
                ).to_dict("records")
            except Exception:
                history = []

            try:
                score = read_sql(
                    "SELECT * FROM manager_scorecards WHERE manager_key=? ORDER BY created_at DESC",
                    (manager_key,)
                ).to_dict("records")
            except Exception:
                score = []

            try:
                betas = read_sql(
                    """
                    SELECT ar.window_type, 
                           AVG(ar.beta_mkt) AS beta_mkt, 
                           AVG(ar.beta_smb) AS beta_smb, 
                           AVG(ar.beta_hml) AS beta_hml, 
                           AVG(ar.beta_wml) AS beta_wml, 
                           AVG(ar.beta_qmj) AS beta_qmj
                    FROM attribution_results ar 
                    WHERE ar.manager_key=? AND ar.beta_mkt IS NOT NULL
                    GROUP BY ar.window_type
                    """,
                    (manager_key,),
                ).to_dict("records")
            except Exception:
                betas = []

            try:
                attribution = read_sql(
                    """
                    SELECT ar.event_id, ar.scheme_code, ar.window_type, ar.alpha_annualized, ar.alpha_tstat,
                           ar.adj_r2, ar.observations, ar.model_status, ar.created_at,
                           ar.ir_practitioner, ar.ir_classification,
                           did.did_alpha
                    FROM attribution_results ar
                    LEFT JOIN factor_matched_did did ON ar.event_id = did.event_id
                    WHERE ar.manager_key=?
                      AND ar.model_status = 'ok'
                      AND ar.alpha_annualized IS NOT NULL
                      AND ar.observations >= 12
                    ORDER BY ar.created_at DESC
                    """,
                    (manager_key,),
                ).to_dict("records")
            except Exception:
                attribution = []

            send_json(self, {
                "history": history,
                "score": score,
                "betas": betas,
                "attribution": attribution
            })

        except Exception as exc:
            send_json(self, {
                "history": [],
                "score": [],
                "betas": [],
                "attribution": [],
                "error": str(exc)
            })
