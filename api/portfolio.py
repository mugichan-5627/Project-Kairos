from __future__ import annotations

from http.server import BaseHTTPRequestHandler

import pandas as pd

from _kairos import apply_cors, bootstrap, ensure_scheme_master_loaded, read_json_body, read_sql, send_error, send_json
from src.data.current_manager_resolver import CurrentManagerResolver
from src.alerts.investor_alerts import fuzzy_match_scheme, register_portfolio_rows


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        apply_cors(self)
        self.end_headers()

    def do_POST(self):
        bootstrap()
        try:
            payload = read_json_body(self)
        except ValueError as exc:
            send_error(self, code="invalid_json", message=str(exc), status=400)
            return
        holdings = payload.get("holdings", [])
        scheme_master_status = ensure_scheme_master_loaded()
        if payload.get("investor_email") or payload.get("save_portfolio"):
            registration = register_portfolio_rows(
                payload.get("investor_email") or payload.get("email"),
                holdings,
                payload.get("whatsapp_number"),
            )
        else:
            registration = None
        resolver = CurrentManagerResolver()
        rows = []
        for holding in holdings:
            name = str(holding.get("scheme_name") or holding.get("query") or "")
            amount = float(holding.get("amount", 0) or 0)
            match = fuzzy_match_scheme(name)
            if match.status != "matched":
                rows.append({
                    "query": name,
                    "amount": amount,
                    "match_status": match.status,
                    "match_score": match.score,
                    "investor_risk_score": None,
                    "current_manager": None,
                    "scheme_code": None,
                    "scheme_name": name,
                })
                continue
            risk = read_sql("SELECT * FROM manager_scorecards WHERE scheme_code=? ORDER BY created_at DESC LIMIT 1", (match.scheme_code,))
            current = resolver.resolve(
                scheme_code=str(match.scheme_code),
                scheme_name=str(match.scheme_name),
                amc_name=match.amc_name,
            )
            rows.append({
                "query": name,
                "amount": amount,
                "scheme_code": match.scheme_code,
                "scheme_name": match.scheme_name,
                "investor_risk_score": None if risk.empty else risk.iloc[0]["investor_risk_score"],
                "current_manager": current.get("manager_name"),
                "current_manager_confirmed": current.get("confirmed_date"),
                "current_manager_source": current.get("source"),
                "current_manager_source_url": current.get("source_url"),
                "current_manager_confidence": current.get("confidence_score"),
                "current_manager_resolution": current.get("resolution_status"),
                "match_status": "matched",
                "match_score": match.score,
            })
        df = pd.DataFrame(rows)
        matched = df[df.get("match_status", "") == "matched"] if not df.empty else df
        weighted = None
        if not matched.empty and matched["amount"].sum() > 0:
            weighted = float((matched["amount"] * pd.to_numeric(matched["investor_risk_score"], errors="coerce").fillna(0)).sum() / matched["amount"].sum())
        send_json(
            self,
            {
                "holdings": rows,
                "weighted_manager_risk": weighted,
                "registration": registration,
                "scheme_master_status": scheme_master_status,
            },
        )
