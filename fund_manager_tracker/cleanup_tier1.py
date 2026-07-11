"""One-shot DB cleanup for Project Kairos production hardening.

Run with: python fund_manager_tracker/cleanup_tier1.py

Operations:
1. Drop manager_tenure rows that point at the wrong AMC/plan (Sankaran Naren on
   Sundaram, Sohini Andani on Bank of India, Pankaj Tibrewal on IDFC FTP, etc.),
   plus orphan rows whose scheme_code has no scheme_master entry.
2. Cascade-clean the dependent rows in change_events, attribution_results,
   factor_matched_did, transition_impact_forecasts so a downstream JOIN cannot
   surface a ghost manager.
3. Reload factor_data.rfr_monthly from the committed RBI 91-day T-bill CSV.
   Recompute mkt_rf so the regression sees a time-varying RFR.
4. Relabel factor_is_fallback / factor_source per date: months before the
   NIFTY MOMENTUM 50 launch (Apr-2017) are flagged 'insufficient_momentum_history'
   so the analytics layer can choose 3-factor fallback transparently.
5. Repair the IR carryover bug: attribution_results.ir_practitioner /
   ir_classification should be NULL on a window whose observations < 24.

The script is idempotent: re-running it produces no further changes.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "fund_data.db"
RFR_CSV = ROOT / "seed_data" / "rfr_monthly_91d_tbill.csv"

BAD_TENURE_FILTERS: list[tuple[str, str]] = [
    ("Sankaran Naren", "119597"),
    ("Sankaran Naren", "112529"),
    ("Pankaj Tibrewal", "120175"),
    ("Pankaj Tibrewal", "120180"),
    ("Sohini Andani", "119364"),
    ("Vetri Subramaniam", "100668"),
    ("Vetri Subramaniam", "105756"),
    ("Chirag Setalvad", "101761"),
]

MOMENTUM_50_INCEPTION = "2017-04-30"  # NIFTY MOMENTUM 50 went live Apr 2017
VALUE_50_INCEPTION = "2014-08-31"     # NIFTY500 Value 50 base date Apr 2005, live ~Aug 2014


def cleanup_bad_tenures(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.cursor()
    removed = {"tenure": 0, "change_events": 0, "attribution": 0, "fm_did": 0, "forecasts": 0}
    for name, scheme in BAD_TENURE_FILTERS:
        manager_row = cur.execute(
            "SELECT manager_id FROM manager_identity WHERE canonical_name = ?",
            (name,),
        ).fetchone()
        if not manager_row:
            continue
        manager_id = manager_row[0]
        event_ids = [
            r[0]
            for r in cur.execute(
                "SELECT event_id FROM change_events WHERE manager_name = ? AND scheme_code = ?",
                (name, scheme),
            ).fetchall()
        ]
        if event_ids:
            placeholders = ",".join("?" * len(event_ids))
            removed["attribution"] += cur.execute(
                f"DELETE FROM attribution_results WHERE event_id IN ({placeholders})",
                event_ids,
            ).rowcount or 0
            removed["fm_did"] += cur.execute(
                f"DELETE FROM factor_matched_did WHERE event_id IN ({placeholders})",
                event_ids,
            ).rowcount or 0
            removed["forecasts"] += cur.execute(
                f"DELETE FROM transition_impact_forecasts WHERE event_id IN ({placeholders})",
                event_ids,
            ).rowcount or 0
            removed["change_events"] += cur.execute(
                f"DELETE FROM change_events WHERE event_id IN ({placeholders})",
                event_ids,
            ).rowcount or 0
        removed["tenure"] += cur.execute(
            "DELETE FROM manager_tenure WHERE manager_id = ? AND scheme_code = ?",
            (manager_id, scheme),
        ).rowcount or 0
    # Drop tenures whose scheme_code has no scheme_master row (true orphans)
    orphan_pairs = cur.execute(
        """
        SELECT mt.manager_id, mt.scheme_code
        FROM manager_tenure mt
        LEFT JOIN scheme_master sm ON sm.scheme_code = mt.scheme_code
        WHERE sm.scheme_code IS NULL
        """
    ).fetchall()
    for manager_id, scheme_code in orphan_pairs:
        removed["tenure"] += cur.execute(
            "DELETE FROM manager_tenure WHERE manager_id = ? AND scheme_code = ?",
            (manager_id, scheme_code),
        ).rowcount or 0
    return removed


def load_rfr_csv(conn: sqlite3.Connection) -> int:
    if not RFR_CSV.exists():
        raise FileNotFoundError(RFR_CSV)
    cur = conn.cursor()
    updated = 0
    with RFR_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cur.execute(
                """
                UPDATE factor_data
                SET rfr_monthly = ?, risk_free_monthly = ?, rfr_source = ?, rfr_is_fallback = 0
                WHERE factor_date = ?
                """,
                (
                    float(row["rfr_monthly"]),
                    float(row["rfr_monthly"]),
                    row["rfr_source"],
                    row["factor_date"],
                ),
            )
            updated += cur.rowcount or 0
    # Recompute mkt_rf using the loaded RFR
    cur.execute(
        """
        UPDATE factor_data
        SET mkt_rf = COALESCE(nifty500_return, 0) - COALESCE(rfr_monthly, 0)
        WHERE rfr_monthly IS NOT NULL
        """
    )
    return updated


def relabel_factor_fallbacks(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.cursor()
    counts = {}
    # Months before MOMENTUM-50 inception cannot anchor a true 4-factor model
    counts["momentum_pre_2017"] = cur.execute(
        """
        UPDATE factor_data
        SET factor_is_fallback = 1,
            factor_source = 'insufficient_momentum_history'
        WHERE factor_date < ?
          AND (wml IS NULL OR wml = 0)
        """,
        (MOMENTUM_50_INCEPTION,),
    ).rowcount or 0
    # Months with all factor legs flat zero (pre-loader-init state) are fallback
    counts["all_zero_factors"] = cur.execute(
        """
        UPDATE factor_data
        SET factor_is_fallback = 1,
            factor_source = COALESCE(factor_source, 'all_zero_factor_legs')
        WHERE COALESCE(mkt_rf,0)=0 AND COALESCE(smb,0)=0 AND COALESCE(hml,0)=0 AND COALESCE(wml,0)=0
        """
    ).rowcount or 0
    # Months with real momentum data and non-zero MKT-RF: confirm clean label
    counts["clean_marked"] = cur.execute(
        """
        UPDATE factor_data
        SET factor_is_fallback = 0,
            factor_source = 'yfinance_nse_indices'
        WHERE factor_date >= ?
          AND wml IS NOT NULL AND wml != 0
          AND mkt_rf IS NOT NULL AND mkt_rf != 0
        """,
        (MOMENTUM_50_INCEPTION,),
    ).rowcount or 0
    return counts


def fix_ir_carryover(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    rowcount = cur.execute(
        """
        UPDATE attribution_results
        SET ir_practitioner = NULL, ir_classification = NULL
        WHERE COALESCE(observations,0) < 24
          AND (ir_practitioner IS NOT NULL OR ir_classification IS NOT NULL)
        """
    ).rowcount or 0
    return rowcount


def report_state(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print()
    print("=== Post-cleanup state ===")
    for label, query in [
        ("manager_tenure", "SELECT COUNT(*) FROM manager_tenure"),
        ("change_events", "SELECT COUNT(*) FROM change_events"),
        ("attribution_results", "SELECT COUNT(*) FROM attribution_results"),
        ("attribution_ok", "SELECT COUNT(*) FROM attribution_results WHERE model_status='ok'"),
        ("factor_data total", "SELECT COUNT(*) FROM factor_data"),
        ("factor fallback", "SELECT COUNT(*) FROM factor_data WHERE factor_is_fallback=1"),
        ("factor clean", "SELECT COUNT(*) FROM factor_data WHERE factor_is_fallback=0"),
        ("rfr non-flat", "SELECT COUNT(DISTINCT rfr_monthly) FROM factor_data"),
    ]:
        (value,) = cur.execute(query).fetchone()
        print(f"  {label:24s}: {value}")


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        removed = cleanup_bad_tenures(conn)
        print(f"Bad-seed removal: {removed}")
        rfr_updates = load_rfr_csv(conn)
        print(f"RFR rows updated: {rfr_updates}")
        relabels = relabel_factor_fallbacks(conn)
        print(f"Factor fallback relabel: {relabels}")
        ir_fixes = fix_ir_carryover(conn)
        print(f"IR carryover NULLed: {ir_fixes}")
        conn.commit()
        report_state(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
