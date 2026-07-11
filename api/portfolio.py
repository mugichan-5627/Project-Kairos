from __future__ import annotations

from http.server import BaseHTTPRequestHandler

import pandas as pd

# Vercel's Python runtime puts /var/task on sys.path but not /var/task/api,
# so the shared _kairos helper needs the handler's own directory added.
import sys as _sys
from pathlib import Path as _Path

_HERE = str(_Path(__file__).resolve().parent)
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)

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
            # Manager style + expert-style impact readout (quant + qualitative)
            manager_style = None
            manager_impact = None
            if current.get("manager_name"):
                try:
                    from src.analytics.manager_assessment import get_assessment, transition_impact_text

                    assessment = get_assessment(manager_name=str(current["manager_name"]))
                    if assessment:
                        manager_style = {
                            "style_label": assessment.get("style_label"),
                            "aggression": assessment.get("aggression"),
                            "curated": assessment.get("curated"),
                            "summary": assessment.get("style_summary"),
                        }
                        manager_impact = transition_impact_text(
                            str(current["manager_name"]),
                            direction="departing",
                            scheme_name=str(match.scheme_name),
                        )["text"]
                except Exception:
                    pass
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
                "manager_style": manager_style,
                "manager_impact": manager_impact,
                "match_status": "matched",
                "match_score": match.score,
            })
        df = pd.DataFrame(rows)
        matched = df[df.get("match_status", "") == "matched"] if not df.empty else df
        # Weighted risk averages ONLY holdings that actually have a score —
        # treating "no coverage" as zero risk would understate portfolio risk.
        weighted = None
        scored_amount = 0.0
        matched_amount = 0.0
        if not matched.empty:
            scores = pd.to_numeric(matched["investor_risk_score"], errors="coerce")
            scored = matched[scores.notna()]
            matched_amount = float(matched["amount"].sum())
            scored_amount = float(scored["amount"].sum())
            if scored_amount > 0:
                weighted = float(
                    (scored["amount"] * scores[scores.notna()]).sum() / scored_amount
                )
        coverage = (scored_amount / matched_amount) if matched_amount > 0 else 0.0
        send_json(
            self,
            {
                "holdings": rows,
                "weighted_manager_risk": weighted,
                "risk_coverage": round(coverage, 4),
                "scored_amount": scored_amount,
                "matched_amount": matched_amount,
                "registration": registration,
                "scheme_master_status": scheme_master_status,
            },
        )
