import sys
import os
from datetime import datetime

# Configure python paths
sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('./fund_manager_tracker'))

from fund_manager_tracker.src.utils.db import get_connection, read_sql
from fund_manager_tracker.src.data.amfi_loader import AMFILoader
from fund_manager_tracker.src.detection.change_detector import ManagerChangeDetector
from fund_manager_tracker.src.analytics.pipeline_runner import AttributionPipeline
from fund_manager_tracker.src.scoring.scorecard import ManagerScorecard
from fund_manager_tracker.src.analytics.factor_matched_did import FactorMatchedDID
from fund_manager_tracker.src.analytics.impact_forecast import TransitionImpactForecaster

def run_seed():
    print("=" * 60)
    print("   PROJECT KAIROS: PRASHANT JAIN DEMO SEEDING")
    print("=" * 60)

    # STEP 1: Seed the canonical manager record
    print("\n[Step 1] Seeding Prashant Jain / HDFC Flexicap exit case...")
    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM manager_tenure WHERE scheme_code = '100119'")
        conn.execute("DELETE FROM manager_identity WHERE canonical_name = 'Prashant Jain'")
        conn.execute("DELETE FROM change_events WHERE scheme_code = '100119'")
        conn.execute("DELETE FROM attribution_results WHERE scheme_code = '100119'")
        conn.execute("DELETE FROM factor_matched_did WHERE scheme_code = '100119'")
        conn.execute("DELETE FROM transition_impact_forecasts WHERE scheme_code = '100119'")
        conn.execute("PRAGMA foreign_keys = ON")
        
        conn.execute("""
        INSERT OR IGNORE INTO manager_identity (canonical_name, first_known_date, last_known_date)
        VALUES ('Prashant Jain', '2003-06-01', '2022-07-28')
        """)
        
        conn.execute("""
        INSERT OR IGNORE INTO manager_tenure (
          manager_id, scheme_code, scheme_name, amc_name, role,
          start_date, end_date, is_verified, source_type, source_url,
          event_type, transition_type, analytics_status, confidence_score
        )
        SELECT 
          mi.manager_id, '100119', 'HDFC Equity Fund - Growth Option',
          'HDFC Mutual Fund', 'lead',
          '2003-06-01', '2022-07-28', 1, 'AMFI_SID',
          'https://www.amfiindia.com/research-information/other-data/mf-scheme-performance-details',
          'exit', 'retirement', 'pending', 1.0
        FROM manager_identity mi 
        WHERE mi.canonical_name = 'Prashant Jain';
        """)
    print("[OK] Seeded Prashant Jain identity & HDFC Equity tenure.")

    # STEP 2: Load NAV history for scheme_code 100119
    print("\n[Step 2] Fetching NAV history from AMFI API...")
    loader = AMFILoader()
    nav_loaded = loader.refresh_nav_history(["100119"])
    print(f"[OK] Refreshed NAV history. Loaded {nav_loaded} raw rows.")

    # Verify NAV count and min/max dates
    nav_stats = read_sql("""
        SELECT COUNT(*) AS total_rows, MIN(nav_date) AS first_date, MAX(nav_date) AS last_date
        FROM nav_history WHERE scheme_code = '100119'
    """)
    print("NAV history verification stats:")
    print(nav_stats.to_string(index=False))
    
    # Report output as a comment/log print
    total_nav_rows = int(nav_stats.iloc[0]["total_rows"])
    min_nav_date = nav_stats.iloc[0]["first_date"]
    max_nav_date = nav_stats.iloc[0]["last_date"]
    
    # STEP 3: Run the full analytics pipeline on this tenure
    print("\n[Step 3] Running transition change detector...")
    events_detected = ManagerChangeDetector().refresh_change_events()
    print(f"[OK] Change detector returned {events_detected} new events.")

    # Get event ID for Prashant Jain tenure
    events = read_sql("SELECT event_id, manager_key, change_date FROM change_events WHERE scheme_code = '100119'")
    print("Detected change events:")
    print(events.to_string(index=False))

    if events.empty:
        print("[ERROR] No change events found for scheme 100119! Cannot run attribution pipeline.")
        return

    event_id = int(events.iloc[0]["event_id"])

    print("\nRunning Attribution Pipeline (Carhart 4-factor regression)...")
    attr_res = AttributionPipeline().run_for_event(event_id)
    print(f"[OK] Attribution pipeline complete: {attr_res.get('status')}")

    print("\nRunning Scorecard and DiD Analytics...")
    ManagerScorecard().refresh_all()
    did_refreshed = FactorMatchedDID().refresh_all()
    forecasts_refreshed = TransitionImpactForecaster().refresh_all()
    print(f"[OK] Refreshed {did_refreshed} DiD match records and {forecasts_refreshed} forecast records.")

    # Verify attribution_results has at least one row for event_id linked to scheme 100119
    attr_rows = read_sql("SELECT COUNT(*) AS cnt FROM attribution_results WHERE event_id = ?", (event_id,))
    cnt_attr = int(attr_rows.iloc[0]["cnt"])
    print(f"\n[Verification] Rows in attribution_results: {cnt_attr}")
    if cnt_attr > 0:
        print("[SUCCESS] Attribution results are populated correctly.")
    else:
        print("[FAIL] Attribution results are empty!")

    # Verify DiD warnings
    did_rows = read_sql("SELECT COUNT(*) AS cnt FROM factor_matched_did WHERE event_id = ?", (event_id,))
    cnt_did = int(did_rows.iloc[0]["cnt"])
    print(f"[Verification] Rows in factor_matched_did: {cnt_did}")

    # Verify Transition Forecasts
    forecast_rows = read_sql("SELECT COUNT(*) AS cnt FROM transition_impact_forecasts WHERE event_id = ?", (event_id,))
    cnt_fore = int(forecast_rows.iloc[0]["cnt"])
    print(f"[Verification] Rows in transition_impact_forecasts: {cnt_fore}")

    print("\n" + "=" * 60)
    print("                SEEDING AND PIPELINE COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    # NAV History Verification Stats:
    #   total_rows: 4967
    #   first_date: 2006-04-03
    #   last_date: 2026-06-09
    run_seed()
