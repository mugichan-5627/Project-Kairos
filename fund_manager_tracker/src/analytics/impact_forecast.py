from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.db import get_connection, read_sql


# ── Successor values treated as "unknown" ──
_UNKNOWN_SUCCESSOR_LABELS = {
    None, "", "Pending Appointment", "pending", "Unknown", "unknown",
    "Under Review", "under review", "Not Announced", "TBD",
}


def _is_unknown_successor(value) -> bool:
    """Returns True if successor is NULL, empty, or a placeholder."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    return str(value).strip() in _UNKNOWN_SUCCESSOR_LABELS


def _category_average_alpha(scheme_code: str) -> float:
    """Query average pre-window alpha across same SEBI category.

    Uses attribution_results for all schemes in the same
    category/sub_category. Returns 0.0 if no data exists.
    """
    cat_df = read_sql(
        "SELECT COALESCE(sub_category, category) AS cat FROM scheme_master WHERE scheme_code=?",
        (str(scheme_code),),
    )
    if cat_df.empty:
        return 0.0
    category = cat_df.iloc[0]["cat"]
    if not category:
        return 0.0

    avg_df = read_sql(
        """
        SELECT AVG(ar.alpha_annualized) AS avg_alpha
        FROM attribution_results ar
        JOIN change_events ce ON ce.event_id = ar.event_id
        JOIN scheme_master sm ON sm.scheme_code = ce.scheme_code
        WHERE ar.window_type = 'pre'
          AND ar.model_status = 'ok'
          AND COALESCE(sm.sub_category, sm.category) = ?
          AND ce.scheme_code <> ?
        """,
        (category, str(scheme_code)),
    )
    if avg_df.empty or pd.isna(avg_df.iloc[0]["avg_alpha"]):
        return 0.0
    return float(avg_df.iloc[0]["avg_alpha"])


class TransitionImpactForecaster:
    def forecast_event(self, event_id: int) -> dict:
        event_df = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event_df.empty:
            return {"status": "missing_event", "event_id": event_id}
        event = event_df.iloc[0]

        successor = event.get("successor_manager")
        unknown_successor = _is_unknown_successor(successor)

        # ── Determine alpha change ──
        did = read_sql("SELECT * FROM factor_matched_did WHERE event_id=? ORDER BY created_at DESC LIMIT 1", (event_id,))
        alpha_change = np.nan
        pre_alpha = np.nan

        # Get the pre-window alpha for this event
        attr = read_sql(
            "SELECT alpha_annualized FROM attribution_results WHERE event_id=? AND window_type='pre' ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        )
        if not attr.empty and pd.notna(attr.iloc[0]["alpha_annualized"]):
            pre_alpha = float(attr.iloc[0]["alpha_annualized"])

        if unknown_successor:
            # ── Unknown successor: alpha AT RISK relative to category median ──
            category_avg = _category_average_alpha(str(event["scheme_code"]))
            if pd.notna(pre_alpha):
                # expected_alpha_change = how much alpha is at risk
                # relative to a median replacement manager
                alpha_change = -(pre_alpha - category_avg)
                # This is negative: the fund LOSES the departing manager's
                # excess alpha over category average
            uncertainty_flag = "unknown_successor"
        else:
            # ── Known successor: use DiD result or pre_alpha ──
            uncertainty_flag = None
            if not did.empty and pd.notna(did.iloc[0]["did_alpha"]):
                alpha_change = float(did.iloc[0]["did_alpha"])
            elif pd.notna(pre_alpha):
                alpha_change = -pre_alpha

        if pd.isna(alpha_change):
            return self._store(event_id, event, np.nan, "insufficient_analytics",
                               uncertainty_flag=uncertainty_flag)

        # ── Generate Monte Carlo distribution ──
        rng = np.random.default_rng(42)

        if unknown_successor:
            # Wider uncertainty for unknown successor
            vol = max(abs(alpha_change), 0.02)
            samples = rng.normal(alpha_change, vol, size=1000)
        else:
            samples = rng.normal(alpha_change, max(abs(alpha_change) / 2, 0.01), size=1000)

        p10, p50, p90 = np.percentile(samples, [10, 50, 90])

        # ── Action rating logic ──
        if unknown_successor:
            # Never HOLD for unknown successor
            if pd.notna(pre_alpha) and abs(pre_alpha) > 0.03:
                recommendation = "MONITOR"
            else:
                recommendation = "WATCH"
        else:
            if p10 < -0.02:
                recommendation = "REVIEW FOR EXIT"
            elif p50 < -0.01:
                recommendation = "MONITOR"
            else:
                recommendation = "HOLD"

        return self._store(
            event_id, event, alpha_change, "ok",
            p10, p50, p90, recommendation,
            uncertainty_flag=uncertainty_flag,
        )

    def _store(
        self,
        event_id: int,
        event: pd.Series,
        alpha_change: float,
        status: str,
        p10=np.nan,
        p50=np.nan,
        p90=np.nan,
        recommendation="MONITOR",
        uncertainty_flag: str | None = None,
    ) -> dict:
        with get_connection() as conn:
            conn.execute("DELETE FROM transition_impact_forecasts WHERE event_id=?", (event_id,))
            conn.execute(
                """
                INSERT INTO transition_impact_forecasts
                (event_id, scheme_code, departing_manager, incoming_manager, expected_alpha_change,
                 nav_impact_12m_p10, nav_impact_12m_p50, nav_impact_12m_p90,
                 nav_impact_24m_p10, nav_impact_24m_p50, nav_impact_24m_p90,
                 recommendation, status, uncertainty_flag)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event["scheme_code"],
                    event["manager_name"],
                    event.get("successor_manager"),
                    alpha_change,
                    p10,
                    p50,
                    p90,
                    p10 * 2 if pd.notna(p10) else np.nan,
                    p50 * 2 if pd.notna(p50) else np.nan,
                    p90 * 2 if pd.notna(p90) else np.nan,
                    recommendation,
                    status,
                    uncertainty_flag,
                ),
            )
        return {
            "event_id": event_id,
            "status": status,
            "expected_alpha_change": alpha_change,
            "recommendation": recommendation,
            "uncertainty_flag": uncertainty_flag,
        }

    def refresh_all(self) -> int:
        events = read_sql("SELECT event_id FROM change_events")
        count = 0
        for event_id in events["event_id"].tolist():
            if self.forecast_event(int(event_id)).get("status") == "ok":
                count += 1
        return count
