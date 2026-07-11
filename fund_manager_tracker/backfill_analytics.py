"""One-shot analytics backfill: widen PAS coverage from event-linked schemes
to every canonical manager tenure.

Steps:
 1. Fetch NAV history (mfapi.in) for tenure schemes missing from nav_history.
 2. Run a tenure-window Carhart regression for each (manager, scheme) tenure
    whose scheme is not already covered by an event-linked attribution row,
    and store it as a 'pre' attribution result (event_id NULL).
 3. Refresh Portable Alpha Scores for all managers.

Run from the repo's fund_manager_tracker directory:
    python backfill_analytics.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from src.analytics.factor_model import FactorModel
from src.analytics.portable_alpha import PortableAlphaEngine
from src.data.amfi_loader import AMFILoader
from src.utils.db import get_connection, read_sql


def fetch_missing_nav() -> int:
    missing = read_sql(
        """
        SELECT DISTINCT mt.scheme_code
        FROM manager_tenure mt
        WHERE mt.scheme_code IS NOT NULL
          AND mt.scheme_code NOT IN (SELECT DISTINCT scheme_code FROM nav_history)
        """
    )
    codes = [str(c) for c in missing["scheme_code"].tolist()]
    if not codes:
        print("NAV: nothing missing")
        return 0
    print(f"NAV: fetching history for {len(codes)} tenure schemes: {codes}")
    loader = AMFILoader()
    rows = 0
    for code in codes:
        try:
            rows += loader.refresh_nav_history([code])
            print(f"  {code}: ok")
        except Exception as exc:
            print(f"  {code}: FAILED ({exc})")
    return rows


def backfill_attribution() -> dict:
    tenures = read_sql(
        """
        SELECT mt.manager_id, mi.canonical_name, mt.scheme_code, mt.start_date, mt.end_date
        FROM manager_tenure mt
        JOIN manager_identity mi ON mi.manager_id = mt.manager_id
        WHERE mt.scheme_code IN (SELECT DISTINCT scheme_code FROM nav_history)
        """
    )
    # Schemes already covered by an OK pre-window row keep their existing
    # (event-derived) attribution — do not overwrite.
    covered = set(
        read_sql(
            "SELECT DISTINCT scheme_code FROM attribution_results WHERE window_type='pre' AND model_status='ok'"
        )["scheme_code"].astype(str)
    )
    fm = FactorModel()
    stats = {"tenures": len(tenures), "skipped_covered": 0, "ok": 0, "insufficient": 0}
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for _, t in tenures.iterrows():
        code = str(t["scheme_code"])
        if code in covered:
            stats["skipped_covered"] += 1
            continue
        start = str(t["start_date"] or "")[:10] or None
        end = str(t["end_date"] or "")[:10] or today
        # run_regression opens its own DB connection for data-quality logging,
        # so the insert below must use a separate short-lived connection.
        result = fm.run_regression(code, start, end)
        status = result.get("model_status", "unknown")
        if status == "ok":
            stats["ok"] += 1
            covered.add(code)  # one row per scheme is enough for PAS
        else:
            stats["insufficient"] += 1
            continue
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO attribution_results
                (scheme_code, event_id, manager_key, window_type, start_date, end_date,
                 alpha_annualized, alpha_tstat, adj_r2,
                 beta_mkt, beta_smb, beta_hml, beta_wml,
                 beta_mkt_t, beta_smb_t, beta_hml_t, beta_wml_t,
                 idiosyncratic_vol, observations, model_status, value_factor_label)
                VALUES (?, NULL, ?, 'pre', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    t["canonical_name"],
                    start,
                    end,
                    result.get("alpha_annualized"),
                    result.get("alpha_tstat"),
                    result.get("adj_r2"),
                    result.get("beta_mkt"),
                    result.get("beta_smb"),
                    result.get("beta_hml"),
                    result.get("beta_wml"),
                    result.get("beta_mkt_t"),
                    result.get("beta_smb_t"),
                    result.get("beta_hml_t"),
                    result.get("beta_wml_t"),
                    result.get("idiosyncratic_vol"),
                    result.get("observations"),
                    status,
                    result.get("value_factor_label"),
                ),
            )
            print(f"  attribution {code} ({t['canonical_name']}): {status}, obs={result.get('observations')}")
    return stats


def main() -> None:
    print("== Step 1: NAV backfill ==")
    nav_rows = fetch_missing_nav()
    print(f"NAV rows loaded: {nav_rows:,}\n")

    print("== Step 2: tenure attribution backfill ==")
    stats = backfill_attribution()
    print(f"attribution: {stats}\n")

    print("== Step 3: Portable Alpha refresh ==")
    pas = PortableAlphaEngine().refresh_all()
    print(f"managers with PAS: {pas}")


if __name__ == "__main__":
    main()
