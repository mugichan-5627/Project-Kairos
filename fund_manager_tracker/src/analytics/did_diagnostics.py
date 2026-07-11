from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.factor_model import FactorModel
from src.utils.db import get_connection, read_sql


class DIDDiagnostics:
    def __init__(self, slope_threshold: float = 0.01) -> None:
        self.factor_model = FactorModel()
        self.slope_threshold = slope_threshold

    def rolling_alpha_series(self, scheme_code: str, end_date: str, months: int = 12) -> pd.Series:
        frame = self.factor_model.regression_frame(scheme_code, None, end_date)
        if len(frame) < months:
            return pd.Series(dtype=float)
        rows = []
        frame = frame.sort_values("factor_date").tail(months + 11)
        for i in range(12, len(frame) + 1):
            start = frame.iloc[i - 12]["factor_date"].strftime("%Y-%m-%d")
            end = frame.iloc[i - 1]["factor_date"].strftime("%Y-%m-%d")
            res = self.factor_model.run_regression(scheme_code, start, end)
            if res.get("model_status") == "ok":
                rows.append((pd.to_datetime(end), res["alpha_annualized"]))
        if not rows:
            return pd.Series(dtype=float)
        return pd.Series(dict(rows)).sort_index().tail(months)

    def category_median_series(self, category: str, exclude_scheme: str, end_date: str, months: int = 12, limit: int = 12) -> pd.Series:
        peers = read_sql(
            "SELECT scheme_code FROM scheme_master WHERE COALESCE(sub_category, category)=? AND scheme_code<>? LIMIT ?",
            (category, exclude_scheme, limit),
        )
        series = []
        for scheme_code in peers["scheme_code"].astype(str).tolist():
            s = self.rolling_alpha_series(scheme_code, end_date, months)
            if not s.empty:
                series.append(s.rename(scheme_code))
        if not series:
            return pd.Series(dtype=float)
        return pd.concat(series, axis=1).median(axis=1).dropna()

    def run_for_event(self, event_id: int) -> dict:
        event_df = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event_df.empty:
            return {"status": "missing_event", "event_id": event_id}
        event = event_df.iloc[0]
        fund = self.rolling_alpha_series(event["scheme_code"], event["change_date"], 12)
        category = self.category_median_series(event.get("category") or "", event["scheme_code"], event["change_date"], 12)
        aligned = pd.concat([fund.rename("fund"), category.rename("category")], axis=1).dropna()
        if len(aligned) < 6:
            result = {
                "fund_trend_slope": np.nan,
                "category_trend_slope": np.nan,
                "slope_difference": np.nan,
                "diagnostic_label": "insufficient_parallel_trends_data",
                "message": "Not enough aligned pre-change alpha observations for parallel trends check.",
            }
        else:
            x = np.arange(len(aligned))
            fund_slope = float(np.polyfit(x, aligned["fund"], 1)[0])
            cat_slope = float(np.polyfit(x, aligned["category"], 1)[0])
            diff = fund_slope - cat_slope
            failed = abs(diff) > self.slope_threshold
            result = {
                "fund_trend_slope": fund_slope,
                "category_trend_slope": cat_slope,
                "slope_difference": diff,
                "diagnostic_label": "low_confidence_parallel_trends_failed" if failed else "parallel_trends_ok",
                "message": "DiD result low confidence - pre-change trend divergence detected" if failed else "Parallel trends diagnostic passed.",
            }
        with get_connection() as conn:
            conn.execute("DELETE FROM did_diagnostics WHERE event_id=?", (event_id,))
            conn.execute(
                """
                INSERT INTO did_diagnostics
                (event_id, scheme_code, fund_trend_slope, category_trend_slope, slope_difference, diagnostic_label, message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event["scheme_code"],
                    result["fund_trend_slope"],
                    result["category_trend_slope"],
                    result["slope_difference"],
                    result["diagnostic_label"],
                    result["message"],
                ),
            )
        return {"status": "ok", "event_id": event_id, **result}

    def refresh_all(self) -> int:
        events = read_sql("SELECT event_id FROM change_events")
        count = 0
        for event_id in events["event_id"].tolist():
            if self.run_for_event(int(event_id)).get("status") == "ok":
                count += 1
        return count
