from __future__ import annotations

import pandas as pd


class BHBAttribution:
    def compute(self, holdings: pd.DataFrame, benchmark: pd.DataFrame, sector_returns: pd.DataFrame) -> pd.DataFrame:
        required = {"sector", "weight"}
        if not required.issubset(holdings.columns) or not required.issubset(benchmark.columns):
            return pd.DataFrame()
        merged = (
            holdings.rename(columns={"weight": "w_p"})
            .merge(benchmark.rename(columns={"weight": "w_b"}), on="sector", how="outer")
            .merge(sector_returns, on="sector", how="left")
            .fillna({"w_p": 0, "w_b": 0})
        )
        if "portfolio_sector_return" not in merged or "benchmark_sector_return" not in merged:
            return pd.DataFrame()
        rb = (merged["w_b"] * merged["benchmark_sector_return"]).sum()
        merged["allocation_effect"] = (merged["w_p"] - merged["w_b"]) * (merged["benchmark_sector_return"] - rb)
        merged["selection_effect"] = merged["w_b"] * (merged["portfolio_sector_return"] - merged["benchmark_sector_return"])
        merged["interaction_effect"] = (merged["w_p"] - merged["w_b"]) * (merged["portfolio_sector_return"] - merged["benchmark_sector_return"])
        merged["total_active_return"] = merged[["allocation_effect", "selection_effect", "interaction_effect"]].sum(axis=1)
        return merged
