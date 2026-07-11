from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.analytics.factor_model import FactorModel
from src.analytics.metrics import PerformanceMetrics
from src.analytics.did_diagnostics import DIDDiagnostics
from src.utils.db import get_connection, read_sql


class AttributionPipeline:
    def __init__(self) -> None:
        self.factor_model = FactorModel()
        self.metrics = PerformanceMetrics()
        self.did = DIDDiagnostics()

    def run_for_event(self, event_id: int) -> dict:
        event_df = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event_df.empty:
            return {"status": "missing_event"}
        event = event_df.iloc[0]
        change_date = pd.to_datetime(event["change_date"])
        pre_start = (change_date - timedelta(days=max(365, int((event.get("pre_tenure_months") or 24) * 30.44)))).strftime("%Y-%m-%d")
        pre_end = (change_date - timedelta(days=1)).strftime("%Y-%m-%d")
        post_start = change_date.strftime("%Y-%m-%d")
        post_end = datetime.utcnow().strftime("%Y-%m-%d")
        rows = []
        for window_type, start, end in [("pre", pre_start, pre_end), ("post", post_start, post_end)]:
            result = self.factor_model.run_regression(event["scheme_code"], start, end)
            result.update({"window_type": window_type, "start_date": start, "end_date": end})
            rows.append(result)

        # ── Compute rolling 36-month alpha series for this scheme ──
        rolling_df = pd.DataFrame()
        try:
            rolling_df = compute_rolling_alpha_series_for_scheme(
                str(event["scheme_code"]),
                self.factor_model,
                start_date=pre_start,
                end_date=pre_end,
            )
        except Exception as exc:
            print(f"[WARN] Rolling alpha computation failed: {exc}")

        # ── Compute Information Ratio from rolling alpha ──
        ir_results: dict = {}
        pre_row = next((row for row in rows if row.get("window_type") == "pre"), {})
        if pre_row.get("model_status") == "ok" and not rolling_df.empty and len(rolling_df) >= 6:
            residual_std = None
            if pre_row.get("idiosyncratic_vol"):
                residual_std = pre_row["idiosyncratic_vol"] / np.sqrt(12)
            ir_results = compute_information_ratio(
                rolling_df["alpha_annualised"],
                residual_std=residual_std,
            )

        with get_connection() as conn:
            conn.execute("DELETE FROM attribution_results WHERE event_id=?", (event_id,))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO attribution_results
                    (scheme_code,event_id,manager_key,window_type,start_date,end_date,alpha_annualized,alpha_tstat,adj_r2,
                     beta_mkt,beta_smb,beta_hml,beta_wml,beta_qmj,beta_mkt_t,beta_smb_t,beta_hml_t,beta_wml_t,beta_qmj_t,
                     idiosyncratic_vol,observations,model_status,value_factor_label,
                     ir_appraisal,ir_practitioner,ir_classification)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,  ?, ?, ?)
                    """,
                    (
                        event["scheme_code"],
                        event_id,
                        event["manager_key"],
                        row["window_type"],
                        row["start_date"],
                        row["end_date"],
                        row.get("alpha_annualized"),
                        row.get("alpha_tstat"),
                        row.get("adj_r2"),
                        row.get("beta_mkt"),
                        row.get("beta_smb"),
                        row.get("beta_hml"),
                        row.get("beta_wml"),
                        None,
                        row.get("beta_mkt_t"),
                        row.get("beta_smb_t"),
                        row.get("beta_hml_t"),
                        row.get("beta_wml_t"),
                        None,
                        row.get("idiosyncratic_vol"),
                        row.get("observations"),
                        row.get("model_status"),
                        row.get("value_factor_label"),
                        ir_results.get("ir_appraisal") if row["window_type"] == "pre" else None,
                        ir_results.get("ir_practitioner") if row["window_type"] == "pre" else None,
                        ir_results.get("ir_classification") if row["window_type"] == "pre" else None,
                    ),
                )

            # ── Persist rolling alpha series ──
            if not rolling_df.empty:
                conn.execute(
                    "DELETE FROM rolling_alpha_series WHERE scheme_code=? AND event_id=?",
                    (str(event["scheme_code"]), event_id),
                )
                for _, rrow in rolling_df.iterrows():
                    conn.execute(
                        """
                        INSERT INTO rolling_alpha_series
                        (event_id, scheme_code, window_end_date, window_start_date,
                         window_months, alpha_monthly, alpha_annualised, alpha_tstat,
                         alpha_pval, adj_r2, observations,
                         beta_mkt, beta_smb, beta_hml, beta_wml)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            str(event["scheme_code"]),
                            rrow.get("window_end_date").strftime("%Y-%m-%d") if hasattr(rrow.get("window_end_date"), "strftime") else str(rrow.get("window_end_date")),
                            rrow.get("window_start_date").strftime("%Y-%m-%d") if hasattr(rrow.get("window_start_date"), "strftime") else str(rrow.get("window_start_date")),
                            int(rrow.get("window_months", 36)),
                            rrow.get("alpha_monthly"),
                            rrow.get("alpha_annualised"),
                            rrow.get("alpha_tstat"),
                            rrow.get("alpha_pval"),
                            rrow.get("adj_r2"),
                            int(rrow.get("observations", 0)),
                            rrow.get("beta_mkt"),
                            rrow.get("beta_smb"),
                            rrow.get("beta_hml"),
                            rrow.get("beta_wml"),
                        ),
                    )

        self.did.run_for_event(event_id)
        return {"status": "ok", "rows": rows, "rolling_windows": len(rolling_df)}

    def refresh_all_events(self) -> int:
        events = read_sql("SELECT event_id FROM change_events")
        count = 0
        for event_id in events["event_id"].tolist():
            if self.run_for_event(int(event_id)).get("status") == "ok":
                count += 1
        return count


# ─────────────────────────────────────────────────────────
# Rolling 36-Month Alpha Series (Morningstar standard)
# ─────────────────────────────────────────────────────────

def compute_rolling_alpha_series_for_scheme(
    scheme_code: str,
    factor_model: FactorModel | None = None,
    window_months: int = 36,
    min_obs: int = 24,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Computes rolling 36-month Carhart 4-factor alpha for a scheme.
    Uses the same aligned regression frame that the main model uses,
    ensuring consistency with the full-tenure result.

    Returns a pd.DataFrame with columns:
      window_end_date, window_start_date, alpha_monthly, alpha_annualised,
      alpha_tstat, alpha_pval, adj_r2, observations, beta_mkt, beta_smb,
      beta_hml, beta_wml, window_months

    Method follows Morningstar Medalist Rating Methodology (May 2024):
    rolling 3-year regressions compiled into a historical alpha series.
    """
    import statsmodels.api as sm

    if factor_model is None:
        factor_model = FactorModel()

    frame = factor_model.regression_frame(scheme_code, start_date=start_date, end_date=end_date)
    if frame.empty or len(frame) < min_obs:
        return pd.DataFrame()

    frame = frame.sort_values("factor_date").reset_index(drop=True)
    factor_cols = ["mkt_rf", "smb", "hml", "wml"]
    results = []

    for i in range(window_months, len(frame) + 1):
        window = frame.iloc[i - window_months : i]
        if len(window) < min_obs:
            continue

        window_end = window.iloc[-1]["factor_date"]
        window_start = window.iloc[0]["factor_date"]

        y = window["excess_fund_return"].astype(float)
        X = sm.add_constant(window[factor_cols].astype(float))

        try:
            model = sm.OLS(y, X).fit()
        except Exception:
            continue

        alpha_m = float(model.params.get("const", np.nan))
        alpha_ann = float((1 + alpha_m) ** 12 - 1)

        results.append({
            "window_end_date": window_end,
            "window_start_date": window_start,
            "alpha_monthly": alpha_m,
            "alpha_annualised": alpha_ann,
            "alpha_tstat": float(model.tvalues.get("const", np.nan)),
            "alpha_pval": float(model.pvalues.get("const", np.nan)),
            "adj_r2": float(model.rsquared_adj),
            "observations": int(model.nobs),
            "beta_mkt": float(model.params.get("mkt_rf", np.nan)),
            "beta_smb": float(model.params.get("smb", np.nan)),
            "beta_hml": float(model.params.get("hml", np.nan)),
            "beta_wml": float(model.params.get("wml", np.nan)),
            "window_months": window_months,
        })

    return pd.DataFrame(results)


# ─────────────────────────────────────────────────────────
# Information Ratio Calculation (BKM 8.5 / State Street)
# ─────────────────────────────────────────────────────────

def compute_information_ratio(
    alpha_series: pd.Series,
    residual_std: float | None = None,
) -> dict:
    """
    Computes the Information Ratio for a manager tenure.

    Two methods:
    1. Appraisal Ratio (BKM 8.5, factor-model version):
       IR = mean(alpha) / residual_std
       Uses residual_std from the full-tenure OLS model.
       Academically precise when factor model residuals are available.

    2. Practitioner IR:
       IR = mean(rolling_alpha_ann) / std(rolling_alpha_ann)
       Uses the series of rolling 36-month annualised alphas.
       More intuitive for non-quant audiences.

    Returns both. Display practitioner IR in the UI (easier to explain).
    Store both in DB.
    """
    ir_appraisal = None
    if residual_std is not None and residual_std > 0 and len(alpha_series) > 0:
        mean_alpha = float(alpha_series.mean())
        ir_appraisal = mean_alpha / residual_std

    # Practitioner IR from rolling alpha series
    ir_practitioner = None
    mean_roll = None
    std_roll = None
    if len(alpha_series) >= 3:
        mean_roll = float(alpha_series.mean())
        std_roll = float(alpha_series.std())
        if std_roll > 0:
            ir_practitioner = mean_roll / std_roll

    def classify_ir(ir):
        if ir is None:
            return "insufficient_data"
        if ir >= 1.00:
            return "exceptional"
        if ir >= 0.75:
            return "excellent"
        if ir >= 0.50:
            return "good"
        if ir >= 0.25:
            return "average"
        if ir >= 0.00:
            return "below_average"
        return "negative"

    return {
        "ir_appraisal": ir_appraisal,
        "ir_practitioner": ir_practitioner,
        "ir_classification": classify_ir(ir_practitioner),
        "mean_rolling_alpha_ann": mean_roll,
        "std_rolling_alpha_ann": std_roll,
    }


def run_full_pipeline(event_id: int) -> dict:
    """Convenience wrapper used from scripts and seed jobs."""
    from src.analytics.factor_matched_did import FactorMatchedDID
    from src.analytics.impact_forecast import TransitionImpactForecaster

    pipeline = AttributionPipeline()
    result = pipeline.run_for_event(event_id)
    FactorMatchedDID().run_for_event(event_id)
    TransitionImpactForecaster().forecast_event(event_id)
    return result
