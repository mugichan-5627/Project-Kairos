from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.db import get_connection, read_sql


class PortableAlphaEngine:
    def compute_manager_pas(self, manager_id: int) -> dict:
        tenures = read_sql(
            """
            SELECT mt.*, mi.canonical_name
            FROM manager_tenure mt
            JOIN manager_identity mi ON mi.manager_id=mt.manager_id
            WHERE mt.manager_id=?
            """,
            (manager_id,),
        )
        if tenures.empty:
            return {"status": "missing_manager", "manager_id": manager_id}
        alpha_rows = []
        for _, tenure in tenures.iterrows():
            attrs = read_sql(
                """
                SELECT alpha_annualized, alpha_tstat FROM attribution_results
                WHERE scheme_code=? AND window_type='pre' AND model_status='ok'
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(tenure["scheme_code"]),),
            )
            if attrs.empty:
                continue
            start = pd.to_datetime(tenure.get("start_date"), errors="coerce")
            end = pd.to_datetime(tenure.get("end_date"), errors="coerce")
            months = 12.0 if pd.isna(start) or pd.isna(end) else max(1.0, (end - start).days / 30.44)
            confidence = float(tenure.get("confidence_score") or 0.5)
            # Empirical-Bayes t-shrinkage: noisy alphas are pulled toward zero
            # (posterior mean = alpha * t^2 / (1 + t^2)); prevents short, lucky
            # windows from dominating the Portable Alpha Score.
            from src.analytics.manager_assessment import shrink_alpha

            raw_alpha = float(attrs.iloc[0]["alpha_annualized"])
            tstat = attrs.iloc[0]["alpha_tstat"]
            shrunk = shrink_alpha(raw_alpha, None if pd.isna(tstat) else float(tstat))
            alpha_rows.append({"alpha": shrunk, "months": months, "confidence": confidence})
        if not alpha_rows:
            return {"status": "insufficient_attribution", "manager_id": manager_id}
        df = pd.DataFrame(alpha_rows)
        weights = df["months"] * df["confidence"]
        tenure_weighted = float(np.average(df["alpha"], weights=weights))
        result = {
            "manager_id": manager_id,
            "manager_name": tenures.iloc[0]["canonical_name"],
            "portable_alpha": tenure_weighted,
            "peer_adjusted_alpha": tenure_weighted,
            "tenure_weighted_alpha": tenure_weighted,
            "tenure_months": float(df["months"].sum()),
            "confidence_weight": float(weights.sum()),
            "tenure_count": int(len(df)),
            "regime_adjustment": 0.0,
            "aum_adjustment": 0.0,
            "status": "ok",
        }
        with get_connection() as conn:
            conn.execute("DELETE FROM portable_alpha_scores WHERE manager_id=?", (manager_id,))
            conn.execute(
                """
                INSERT INTO portable_alpha_scores
                (manager_id, manager_name, portable_alpha, peer_adjusted_alpha, tenure_weighted_alpha,
                 tenure_months, confidence_weight, tenure_count, regime_adjustment, aum_adjustment, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["manager_id"], result["manager_name"], result["portable_alpha"], result["peer_adjusted_alpha"],
                    result["tenure_weighted_alpha"], result["tenure_months"], result["confidence_weight"],
                    result["tenure_count"], result["regime_adjustment"], result["aum_adjustment"], result["status"],
                ),
            )
        return result

    def refresh_all(self) -> int:
        managers = read_sql("SELECT manager_id FROM manager_identity")
        count = 0
        for manager_id in managers["manager_id"].tolist():
            if self.compute_manager_pas(int(manager_id)).get("status") == "ok":
                count += 1
        return count
