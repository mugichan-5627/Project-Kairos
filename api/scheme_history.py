from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

from _kairos import bootstrap, ensure_scheme_master_loaded, query_params, read_sql, send_json

# Dynamically link fund_manager_tracker modules
ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "fund_manager_tracker"
if str(TRACKER) not in sys.path:
    sys.path.insert(0, str(TRACKER))
from src.analytics.factor_model import FactorModel


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            bootstrap()
            params = query_params(self)
            code = params.get("scheme_code")
            if not code:
                status = ensure_scheme_master_loaded()
                # Only return schemes with actual NAV data
                schemes = read_sql(
                    """
                    SELECT sm.scheme_code, sm.scheme_name, sm.amc_name,
                           COALESCE(sm.amc_name || ' - ' || sm.scheme_name, sm.scheme_name) AS display
                    FROM scheme_master sm
                    INNER JOIN (SELECT DISTINCT scheme_code FROM nav_history) nh
                        ON nh.scheme_code = sm.scheme_code
                    ORDER BY sm.amc_name, sm.scheme_name
                    """
                )
                send_json(
                    self,
                    {
                        "schemes": schemes.to_dict("records"),
                        "scheme_master_status": status,
                        "message": "Pass scheme_code to view alpha history.",
                    },
                )
                return

            try:
                fm = FactorModel()
                frame = fm.regression_frame(code).reset_index(drop=True)
                factor_cols = ["mkt_rf", "smb", "hml", "wml"]
                rolling = []
                if not frame.empty:
                    total = len(frame)
                    W = min(12, total)
                    step = max(1, (total - W) // 23)  # ~24 points max
                    indices = list(range(W, total + 1, step))
                    if total not in indices:
                        indices.append(total)
                    for i in indices:
                        window = frame.iloc[i - W : i]
                        end_date = window.iloc[-1]["factor_date"].strftime("%Y-%m-%d")
                        y = window["excess_fund_return"].astype(float)
                        
                        try:
                            if W >= 6:
                                x = sm.add_constant(window[factor_cols].astype(float))
                                model = sm.OLS(y, x).fit()
                                alpha_val = float((1 + model.params.get("const", np.nan)) ** 12 - 1)
                            elif W >= 3:
                                x = sm.add_constant(window["mkt_rf"].astype(float))
                                model = sm.OLS(y, x).fit()
                                alpha_val = float((1 + model.params.get("const", np.nan)) ** 12 - 1)
                            else:
                                alpha_val = float((1 + y.mean()) ** 12 - 1)
                                
                            if pd.notna(alpha_val):
                                rolling.append({"date": end_date, "alpha": alpha_val})
                        except Exception:
                            continue
            except Exception:
                rolling = []

            try:
                changes = read_sql(
                    "SELECT change_date, manager_name, change_type FROM change_events WHERE scheme_code=?",
                    (code,)
                ).to_dict("records")
            except Exception:
                changes = []

            try:
                attrs = read_sql(
                    "SELECT * FROM attribution_results WHERE scheme_code=? ORDER BY created_at DESC",
                    (code,)
                ).to_dict("records")
            except Exception:
                attrs = []

            try:
                diagnostics = read_sql(
                    """
                    SELECT 
                      dd.event_id,
                      dd.scheme_code,
                      dd.slope_difference        AS pre_slope_difference,
                      dd.diagnostic_label,
                      dd.message,
                      COALESCE(fmd.did_alpha, 0.0)    AS did_coefficient,
                      COALESCE(fmd.peer_count, 0)     AS peers_count,
                      COALESCE(fmd.peer_count, 0)     AS control_peers_count,
                      0.0                             AS did_tstat,
                      CASE 
                        WHEN dd.diagnostic_label = 'parallel_trends_ok' THEN 1 
                        ELSE 0 
                      END                             AS parallel_trend_passed,
                      dd.created_at
                    FROM did_diagnostics dd
                    LEFT JOIN factor_matched_did fmd 
                      ON fmd.event_id = dd.event_id
                    JOIN change_events ce 
                      ON ce.event_id = dd.event_id
                    WHERE ce.scheme_code = ?
                    ORDER BY dd.created_at DESC
                    """,
                    (code,),
                ).to_dict("records")
            except Exception:
                diagnostics = []

            try:
                score = read_sql(
                    "SELECT * FROM manager_scorecards WHERE scheme_code=? ORDER BY created_at DESC LIMIT 1",
                    (code,)
                ).to_dict("records")
            except Exception:
                score = []

            send_json(self, {
                "rolling_alpha": rolling,
                "changes": changes,
                "attribution": attrs,
                "diagnostics": diagnostics,
                "scorecard": score
            })

        except Exception as exc:
            send_json(self, {
                "rolling_alpha": [],
                "changes": [],
                "attribution": [],
                "diagnostics": [],
                "scorecard": [],
                "error": str(exc)
            })
