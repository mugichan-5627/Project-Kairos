from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from src.utils.db import get_connection, read_sql


def percentile(value: float, population: pd.Series, inverse: bool = False) -> float:
    clean = pd.to_numeric(population, errors="coerce").dropna()
    if pd.isna(value) or clean.empty:
        return 50.0
    pct = float((clean <= value).mean() * 100)
    return 100 - pct if inverse else pct


def absolute_alpha_score(alpha: float) -> float:
    if pd.isna(alpha):
        return 50.0
    if alpha >= 0.04:
        return 90.0
    if alpha >= 0.02:
        return 75.0
    if alpha >= 0.00:
        return 55.0
    if alpha >= -0.02:
        return 35.0
    return 20.0


class ManagerScorecard:
    labels = [(80, "Elite"), (60, "Strong"), (40, "Market"), (0, "Below")]

    def _label(self, score: float) -> str:
        for threshold, label in self.labels:
            if score >= threshold:
                return label
        return "Below"

    def compute_score_for_event(self, event_id: int) -> dict:
        event = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event.empty:
            return {"status": "missing_event"}
        ev = event.iloc[0]
        attr = read_sql("SELECT * FROM attribution_results WHERE event_id=? AND window_type='pre' ORDER BY id DESC", (event_id,))
        all_attr = read_sql("SELECT * FROM attribution_results")
        peer_count = len(pd.to_numeric(all_attr.get("alpha_annualized", pd.Series(dtype=float)), errors="coerce").dropna())
        perf = read_sql("SELECT * FROM performance_metrics WHERE scheme_code=?", (ev["scheme_code"],))
        alpha = attr["alpha_annualized"].iloc[0] if not attr.empty else np.nan
        insufficient_peers = peer_count < 10
        alpha_score = absolute_alpha_score(alpha) if insufficient_peers else percentile(alpha, all_attr.get("alpha_annualized", pd.Series(dtype=float)))
        batting = np.nan
        sortino = np.nan
        max_dd = np.nan
        if not perf.empty:
            metrics = json.loads(perf.iloc[-1]["metrics_json"])
            batting = metrics.get("batting_average")
            sortino = metrics.get("sortino_ratio")
            max_dd = metrics.get("max_drawdown")
        consistency_score = 55 if insufficient_peers and pd.notna(batting) and batting >= 0.5 else (45 if insufficient_peers else (percentile(batting, pd.Series([batting])) if pd.notna(batting) else 50))
        sortino_score = 70 if insufficient_peers and pd.notna(sortino) and sortino >= 1 else (45 if insufficient_peers else (percentile(sortino, pd.Series([sortino])) if pd.notna(sortino) else 50))
        drawdown_score = 70 if insufficient_peers and pd.notna(max_dd) and abs(max_dd) <= 0.25 else (45 if insufficient_peers else (percentile(abs(max_dd), pd.Series([abs(max_dd)]), inverse=True) if pd.notna(max_dd) else 50))
        risk_score = (sortino_score + drawdown_score) / 2
        adj_r2 = attr["adj_r2"].iloc[0] if not attr.empty else np.nan
        factor_efficiency = 100 - max(0, min(100, float(adj_r2) * 100)) if pd.notna(adj_r2) else 50
        tenure = ev.get("pre_tenure_months") or 0
        tenure_score = max(0, min(100, float(tenure) / 60 * 100))
        composite = (
            alpha_score * 0.30
            + consistency_score * 0.25
            + risk_score * 0.20
            + factor_efficiency * 0.15
            + tenure_score * 0.10
        )
        label = self._label(composite)
        investor_risk = self.investor_risk_score(ev, composite, alpha)
        alert = self.alert_text(ev, alpha, investor_risk)
        return {
            "manager_key": ev["manager_key"],
            "manager_name": ev["manager_name"],
            "scheme_code": ev["scheme_code"],
            "event_id": event_id,
            "composite_score": composite,
            "label": label,
            "alpha_score": alpha_score,
            "consistency_score": consistency_score,
            "risk_score": risk_score,
            "factor_efficiency_score": factor_efficiency,
            "tenure_score": tenure_score,
            "investor_risk_score": investor_risk,
            "alert_text": alert,
            "score_method": "absolute_thresholds" if insufficient_peers else "peer_percentiles",
            "score_warning": "Scorecard uses absolute thresholds - insufficient peer data for percentile ranking." if insufficient_peers else None,
            "peer_count": peer_count,
        }

    def investor_risk_score(self, event: pd.Series, composite: float, alpha: float) -> float:
        if event["change_type"] not in ("Full Exit", "AMC Switch"):
            multiplier = 0.55
        else:
            multiplier = 1.0 if event["change_type"] == "Full Exit" else 1.15
        alpha_component = max(0, min(4, (alpha if pd.notna(alpha) else 0) * 50))
        tenure_component = max(0, min(2, float(event.get("pre_tenure_months") or 0) / 36))
        quality_component = max(0, min(2, (composite - 40) / 30))
        category = str(event.get("category") or "").lower()
        fragility_component = 1.0 if any(x in category for x in ["small", "sector", "thematic", "focused", "mid"]) else 0.4
        replacement_component = -0.8 if event.get("successor_manager") else 0.5
        score = (alpha_component + tenure_component + quality_component + fragility_component + replacement_component) * multiplier
        return round(max(0, min(10, score)), 1)

    def alert_text(self, event: pd.Series, alpha: float, risk: float) -> str:
        alpha_pct = 0 if pd.isna(alpha) else alpha * 100
        return (
            f"Fund manager {event['manager_name']} who generated {alpha_pct:.2f}% annual factor alpha "
            f"left scheme {event['scheme_code']} on {event['change_date']}. "
            f"Investor Risk Score: {risk}/10."
        )

    def refresh_all(self) -> int:
        events = read_sql("SELECT event_id FROM change_events")
        count = 0
        with get_connection() as conn:
            for event_id in events["event_id"].tolist():
                score = self.compute_score_for_event(int(event_id))
                if score.get("status"):
                    continue
                conn.execute("DELETE FROM manager_scorecards WHERE event_id=?", (event_id,))
                conn.execute(
                    """
                    INSERT INTO manager_scorecards
                    (manager_key, manager_name, scheme_code, event_id, composite_score, label, alpha_score,
                     consistency_score, risk_score, factor_efficiency_score, tenure_score, investor_risk_score,
                     alert_text, score_method, score_warning, peer_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        score["manager_key"],
                        score["manager_name"],
                        score["scheme_code"],
                        score["event_id"],
                        score["composite_score"],
                        score["label"],
                        score["alpha_score"],
                        score["consistency_score"],
                        score["risk_score"],
                        score["factor_efficiency_score"],
                        score["tenure_score"],
                        score["investor_risk_score"],
                        score["alert_text"],
                        score["score_method"],
                        score["score_warning"],
                        score["peer_count"],
                    ),
                )
                count += 1
        return count
