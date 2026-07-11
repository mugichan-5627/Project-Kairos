from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd

from src.analytics.factor_model import FactorModel
from src.utils.db import get_connection, read_sql


class FactorMatchedDID:
    def __init__(self, min_pre_months: int = 12, peer_count: int = 5) -> None:
        self.factor_model = FactorModel()
        self.min_pre_months = min_pre_months
        self.peer_count = peer_count

    def run_for_event(self, event_id: int) -> dict:
        event_df = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event_df.empty:
            return {"status": "missing_event", "event_id": event_id}
        event = event_df.iloc[0]
        change = pd.to_datetime(event["change_date"])
        pre_start = (change - timedelta(days=36 * 30)).strftime("%Y-%m-%d")
        pre_end = (change - timedelta(days=1)).strftime("%Y-%m-%d")
        post_start = change.strftime("%Y-%m-%d")
        post_end = (change + timedelta(days=365)).strftime("%Y-%m-%d")
        fund_pre = self.factor_model.run_regression(event["scheme_code"], pre_start, pre_end)
        fund_post = self.factor_model.run_regression(event["scheme_code"], post_start, post_end)
        if fund_pre.get("model_status") != "ok" or fund_post.get("model_status") != "ok":
            return self._store(event_id, event, [], np.nan, np.nan, np.nan, np.nan, np.nan, "insufficient_fund_data", "failed")
        peers = self._match_peers(event, pre_start, pre_end, fund_pre)
        if len(peers) < 3:
            return self._store(event_id, event, peers, fund_pre["alpha_annualized"], fund_post["alpha_annualized"], np.nan, np.nan, np.nan, "insufficient_matched_peers", "failed")
        pre_alphas = []
        post_alphas = []
        for peer in peers:
            pre = self.factor_model.run_regression(peer["scheme_code"], pre_start, pre_end)
            post = self.factor_model.run_regression(peer["scheme_code"], post_start, post_end)
            if pre.get("model_status") == "ok" and post.get("model_status") == "ok":
                pre_alphas.append(pre["alpha_annualized"])
                post_alphas.append(post["alpha_annualized"])
        if len(pre_alphas) < 3:
            return self._store(event_id, event, peers, fund_pre["alpha_annualized"], fund_post["alpha_annualized"], np.nan, np.nan, np.nan, "insufficient_peer_post_data", "failed")
        peer_pre = float(np.mean(pre_alphas))
        peer_post = float(np.mean(post_alphas))
        did = (fund_post["alpha_annualized"] - peer_post) - (fund_pre["alpha_annualized"] - peer_pre)
        label = "factor_matched_ok"
        return self._store(event_id, event, peers, fund_pre["alpha_annualized"], fund_post["alpha_annualized"], peer_pre, peer_post, did, label, "ok")

    def _match_peers(self, event: pd.Series, start: str, end: str, fund_reg: dict) -> list[dict]:
        # Look up scheme's category from scheme_master
        scheme_info = read_sql(
            "SELECT category, sub_category FROM scheme_master WHERE scheme_code=?",
            (str(event["scheme_code"]),),
        )
        category = ""
        if not scheme_info.empty:
            category = scheme_info.iloc[0]["sub_category"] or scheme_info.iloc[0]["category"] or ""
        
        # Fallback category if none is found
        if not category:
            category = "Open Ended Schemes(Equity Scheme - ELSS)"
            
        candidates = read_sql(
            "SELECT scheme_code FROM scheme_master WHERE COALESCE(sub_category, category)=? AND scheme_code<>? LIMIT 80",
            (category, str(event["scheme_code"])),
        )
        
        target = np.array([fund_reg.get("beta_mkt"), fund_reg.get("beta_smb"), fund_reg.get("beta_hml"), fund_reg.get("beta_wml")], dtype=float)
        
        from src.data.amfi_loader import AMFILoader
        loader = AMFILoader()
        
        peers = []
        for scheme_code in candidates["scheme_code"].astype(str).tolist():
            # Check if we have nav history, if not download it
            nav_check = read_sql("SELECT COUNT(*) as count FROM nav_history WHERE scheme_code=?", (scheme_code,))
            if nav_check.empty or nav_check.iloc[0]["count"] < 12:
                try:
                    loader.refresh_nav_history([scheme_code])
                except Exception:
                    continue # skip if download fails
            
            reg = self.factor_model.run_regression(scheme_code, start, end)
            if reg.get("model_status") != "ok":
                continue
            vec = np.array([reg.get("beta_mkt"), reg.get("beta_smb"), reg.get("beta_hml"), reg.get("beta_wml")], dtype=float)
            if np.isnan(vec).any() or np.isnan(target).any():
                continue
            peers.append({"scheme_code": scheme_code, "distance": float(np.linalg.norm(vec - target))})
            if len(peers) >= 8: # Stop scanning once we have 8 candidate peers to pick the best 5 from
                break
        return sorted(peers, key=lambda x: x["distance"])[: self.peer_count]

    def _store(self, event_id: int, event: pd.Series, peers: list[dict], fund_pre, fund_post, peer_pre, peer_post, did, label: str, status: str) -> dict:
        with get_connection() as conn:
            conn.execute("DELETE FROM factor_matched_did WHERE event_id=?", (event_id,))
            conn.execute(
                """
                INSERT INTO factor_matched_did
                (event_id, scheme_code, peer_scheme_codes, pre_alpha_fund, post_alpha_fund,
                 pre_alpha_peer, post_alpha_peer, did_alpha, peer_count, parallel_trends_label, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, event["scheme_code"], json.dumps(peers), fund_pre, fund_post, peer_pre, peer_post, did, len(peers), label, status),
            )
        return {"event_id": event_id, "status": status, "did_alpha": did, "peer_count": len(peers), "label": label}

    def refresh_all(self) -> int:
        events = read_sql("SELECT event_id FROM change_events")
        count = 0
        for event_id in events["event_id"].tolist():
            if self.run_for_event(int(event_id)).get("status") == "ok":
                count += 1
        return count
