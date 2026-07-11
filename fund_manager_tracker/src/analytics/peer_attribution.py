from __future__ import annotations

import numpy as np
import pandas as pd

from src.analytics.factor_model import FactorModel
from src.utils.db import read_sql


class PeerAttribution:
    def __init__(self) -> None:
        self.factor_model = FactorModel()

    def category_peer_alphas(self, category: str, start_date: str, end_date: str, exclude_scheme: str | None = None, limit: int = 50) -> list[float]:
        peers = read_sql(
            "SELECT scheme_code FROM scheme_master WHERE COALESCE(sub_category, category)=? AND scheme_code<>? LIMIT ?",
            (category, exclude_scheme or "", limit),
        )
        alphas = []
        for scheme_code in peers["scheme_code"].astype(str).tolist():
            res = self.factor_model.run_regression(scheme_code, start_date, end_date)
            if res.get("model_status") == "ok":
                alphas.append(res["alpha_annualized"])
        return alphas

    def bootstrap_did(self, pre: list[float], post: list[float], fund_pre: float, fund_post: float, n: int = 1000) -> tuple[float, float]:
        if not pre or not post:
            return np.nan, np.nan
        estimates = []
        rng = np.random.default_rng(42)
        for _ in range(n):
            pre_med = np.median(rng.choice(pre, size=len(pre), replace=True))
            post_med = np.median(rng.choice(post, size=len(post), replace=True))
            estimates.append((fund_pre - pre_med) - (fund_post - post_med))
        return float(np.percentile(estimates, 2.5)), float(np.percentile(estimates, 97.5))
