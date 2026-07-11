from __future__ import annotations

import json
import numpy as np
import pandas as pd


class PerformanceMetrics:
    min_months = 12

    @staticmethod
    def max_drawdown(series: pd.Series) -> tuple[float, float]:
        wealth = (1 + series.dropna()).cumprod()
        if wealth.empty:
            return np.nan, np.nan
        drawdown = wealth / wealth.cummax() - 1
        duration = 0
        max_duration = 0
        for value in drawdown:
            duration = duration + 1 if value < 0 else 0
            max_duration = max(max_duration, duration)
        return float(drawdown.min()), float(max_duration / 21)

    @staticmethod
    def _cagr(daily_returns: pd.Series) -> float:
        if daily_returns.empty:
            return np.nan
        years = len(daily_returns) / 252
        if years <= 0:
            return np.nan
        return float((1 + daily_returns).prod() ** (1 / years) - 1)

    def compute(self, nav: pd.DataFrame, benchmark_returns: pd.Series | None = None, risk_free_rate: float = 0.065, beta: float | None = None) -> dict:
        if nav.empty or "nav" not in nav:
            return {"status": "insufficient_data"}
        df = nav.copy()
        df["nav_date"] = pd.to_datetime(df["nav_date"])
        daily = df.sort_values("nav_date").set_index("nav_date")["nav"].astype(float).pct_change().dropna()
        monthly = df.sort_values("nav_date").set_index("nav_date")["nav"].astype(float).resample("ME").last().pct_change().dropna()
        if len(monthly) < self.min_months:
            return {"status": "insufficient_data", "monthly_observations": int(len(monthly))}
        cagr = self._cagr(daily)
        vol = float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 1 else np.nan
        mdd, mdd_duration = self.max_drawdown(daily)
        var95 = float(daily.quantile(0.05))
        cvar95 = float(daily[daily <= var95].mean()) if not daily[daily <= var95].empty else np.nan
        downside = daily[daily < 0]
        downside_dev = float(downside.std(ddof=1) * np.sqrt(252)) if len(downside) > 1 else np.nan
        sharpe = (cagr - risk_free_rate) / vol if vol and vol > 0 else np.nan
        sortino = (cagr - risk_free_rate) / downside_dev if downside_dev and downside_dev > 0 else np.nan
        calmar = cagr / abs(mdd) if mdd and mdd < 0 else np.nan
        treynor = (cagr - risk_free_rate) / beta if beta not in (None, 0) else np.nan
        result = {
            "status": "ok",
            "absolute_return": float(df["nav"].iloc[-1] / df["nav"].iloc[0] - 1),
            "cagr": cagr,
            "rolling_1y_cagr_latest": float((1 + monthly.tail(12)).prod() - 1) if len(monthly) >= 12 else np.nan,
            "rolling_3y_cagr_latest": float((1 + monthly.tail(36)).prod() ** (1 / 3) - 1) if len(monthly) >= 36 else np.nan,
            "annualized_volatility": vol,
            "max_drawdown": mdd,
            "max_drawdown_duration_months": mdd_duration,
            "var_95": var95,
            "cvar_95": cvar95,
            "downside_deviation": downside_dev,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "treynor_ratio": treynor,
        }
        if benchmark_returns is not None and not benchmark_returns.empty:
            aligned = pd.concat([monthly.rename("fund"), benchmark_returns.rename("bench")], axis=1).dropna()
            active = aligned["fund"] - aligned["bench"]
            tracking_error = active.std(ddof=1) * np.sqrt(12)
            result.update(
                {
                    "benchmark_relative_return": float((1 + active).prod() - 1),
                    "information_ratio": float((active.mean() * 12) / tracking_error) if tracking_error and tracking_error > 0 else np.nan,
                    "batting_average": float((active.rolling(12).sum().dropna() > 0).mean()) if len(active) >= 12 else np.nan,
                    "up_capture_ratio": float(aligned.loc[aligned["bench"] > 0, "fund"].mean() / aligned.loc[aligned["bench"] > 0, "bench"].mean()) if (aligned["bench"] > 0).any() else np.nan,
                    "down_capture_ratio": float(aligned.loc[aligned["bench"] < 0, "fund"].mean() / aligned.loc[aligned["bench"] < 0, "bench"].mean()) if (aligned["bench"] < 0).any() else np.nan,
                    "win_loss_ratio": float(active[active > 0].mean() / abs(active[active < 0].mean())) if (active > 0).any() and (active < 0).any() else np.nan,
                }
            )
        return result

    @staticmethod
    def to_json(metrics: dict) -> str:
        return json.dumps({k: (None if pd.isna(v) else v) for k, v in metrics.items()}, default=str)
