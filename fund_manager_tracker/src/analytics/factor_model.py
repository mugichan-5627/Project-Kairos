from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from src.utils.db import read_sql
from src.utils.db import get_connection


FACTOR_COLS = ["mkt_rf", "smb", "hml", "wml"]
FACTOR_LABELS = {
    "mkt_rf": "Market Excess Return",
    "smb": "Size Tilt Factor",
    "hml": "Value Tilt Factor",
    "wml": "Momentum Tilt Factor",
}


class FactorModel:
    min_months = 12

    def monthly_fund_returns(self, scheme_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        query = "SELECT nav_date, nav FROM nav_history WHERE scheme_code=?"
        params: list[str] = [scheme_code]
        if start_date:
            query += " AND nav_date>=?"
            params.append(start_date)
        if end_date:
            query += " AND nav_date<=?"
            params.append(end_date)
        df = read_sql(query + " ORDER BY nav_date", tuple(params))
        if df.empty:
            return pd.DataFrame()
        df["nav_date"] = pd.to_datetime(df["nav_date"])
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        monthly = df.set_index("nav_date")["nav"].resample("ME").last().pct_change().dropna()
        return monthly.rename("fund_return").reset_index().rename(columns={"nav_date": "factor_date"})

    def regression_frame(self, scheme_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        fund = self.monthly_fund_returns(scheme_code, start_date, end_date)
        factors = read_sql("SELECT * FROM factor_data ORDER BY factor_date")
        if fund.empty or factors.empty:
            return pd.DataFrame()
        fund["factor_date"] = pd.to_datetime(fund["factor_date"]).dt.to_period("M").dt.to_timestamp("M")
        factors["factor_date"] = pd.to_datetime(factors["factor_date"]).dt.to_period("M").dt.to_timestamp("M")
        factor_cols = [
            "factor_date",
            "risk_free_monthly",
            "rfr_monthly",
            "factor_is_fallback",
            "rfr_is_fallback",
            "nifty500_return",
            "value50_return",
            *FACTOR_COLS,
        ]
        for col in factor_cols:
            if col not in factors.columns:
                factors[col] = np.nan
        before = len(fund.merge(factors[factor_cols], on="factor_date", how="left"))
        frame = fund.merge(factors[factor_cols], on="factor_date", how="inner")
        risk_free = frame["rfr_monthly"].fillna(frame["risk_free_monthly"])
        frame["excess_fund_return"] = frame["fund_return"] - risk_free
        frame["risk_free_used"] = risk_free
        missing_rfr = int(risk_free.isna().sum())
        clean = frame.dropna(subset=["excess_fund_return", *FACTOR_COLS])
        clean.attrs["missing_rfr"] = missing_rfr
        dropped = before - len(clean)
        if dropped:
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO data_quality_log(check_name, status, details)
                    VALUES('factor_regression_alignment', 'dropped_observations', ?)
                    """,
                    (f"scheme_code={scheme_code}; dropped={dropped}; before={before}; after={len(clean)}",),
                )
        return clean

    def run_regression(self, scheme_code: str, start_date: str | None = None, end_date: str | None = None) -> dict:
        frame = self.regression_frame(scheme_code, start_date, end_date)
        if frame.attrs.get("missing_rfr", 0):
            return {"model_status": "insufficient_rfr", "observations": int(len(frame))}
        if len(frame) < self.min_months:
            return {"model_status": "insufficient_aligned_data", "observations": int(len(frame))}
        y = frame["excess_fund_return"].astype(float)
        # Auto-degrade: drop any factor leg with effectively zero variance over
        # the window (e.g. NIFTY MOMENTUM 50 before its Apr-2017 inception).
        # The spec mandates a transparent fallback to 3- or 1-factor in that
        # case rather than feeding zeros to OLS, which silently inflates alpha.
        active_factors: list[str] = []
        dropped_factors: list[str] = []
        for factor in FACTOR_COLS:
            series = pd.to_numeric(frame[factor], errors="coerce")
            if series.notna().sum() < self.min_months or float(series.std(ddof=1) or 0.0) < 1e-6:
                dropped_factors.append(factor)
            else:
                active_factors.append(factor)
        if not active_factors:
            return {"model_status": "insufficient_factor_variance", "observations": int(len(frame))}
        x = sm.add_constant(frame[active_factors].astype(float))
        try:
            model = sm.OLS(y, x).fit(cov_type="HAC", cov_kwds={"maxlags": 4})
        except Exception as exc:
            return {"model_status": f"failed: {exc}", "observations": int(len(frame))}
        residual_vol = float(np.std(model.resid, ddof=1) * np.sqrt(12))
        if len(active_factors) == 4:
            model_name = "Carhart 4-factor"
        elif "wml" not in active_factors and len(active_factors) == 3:
            model_name = "3-factor (no momentum)"
        elif active_factors == ["mkt_rf"]:
            model_name = "CAPM 1-factor"
        else:
            model_name = f"{len(active_factors)}-factor"
        result = {
            "alpha_annualized": float((1 + model.params.get("const", np.nan))**12 - 1),
            "alpha_tstat": float(model.tvalues.get("const", np.nan)),
            "adj_r2": float(model.rsquared_adj),
            "idiosyncratic_vol": residual_vol,
            "observations": int(len(frame)),
            "model_status": "ok",
            "model_name": model_name,
            "active_factors": ",".join(active_factors),
            "dropped_factors": ",".join(dropped_factors) or None,
            "value_factor_label": "Value Tilt Factor",
            "factor_is_fallback": bool(pd.to_numeric(frame.get("factor_is_fallback", pd.Series([0])), errors="coerce").fillna(0).max()) or bool(dropped_factors),
            "rfr_is_fallback": bool(pd.to_numeric(frame.get("rfr_is_fallback", pd.Series([0])), errors="coerce").fillna(0).max()),
        }
        for factor in FACTOR_COLS:
            key = factor.replace("mkt_rf", "mkt")
            if factor in active_factors:
                result[f"beta_{key}"] = float(model.params.get(factor, np.nan))
                result[f"beta_{key}_t"] = float(model.tvalues.get(factor, np.nan))
            else:
                result[f"beta_{key}"] = None
                result[f"beta_{key}_t"] = None
        return result
