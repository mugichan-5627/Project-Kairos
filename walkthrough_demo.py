import sys
import os
import sqlite3
from datetime import datetime, timedelta

# Ensure python paths are configured correctly
sys.path.insert(0, os.path.abspath('.'))
sys.path.insert(0, os.path.abspath('./fund_manager_tracker'))

from fund_manager_tracker.db_setup import initialize_database
from fund_manager_tracker.src.alerts.investor_alerts import register_portfolio_rows, run_investor_alert_scan
from fund_manager_tracker.src.utils.db import get_connection, read_sql

def run_live_walkthrough():
    print("=" * 60)
    print("   PROJECT KAIROS: LIVE SURVEILLANCE & ALERT WALKTHROUGH")
    print("=" * 60)

    # 1. Initialize Database
    print("\n[Step 1] Initializing SQLite database...")
    initialize_database()
    
    # Clean previous demo entries if they exist
    with get_connection() as conn:
        conn.execute("DELETE FROM investor_portfolios WHERE investor_email = 'live_demo@projectkairos.local'")
        conn.execute("DELETE FROM alert_log WHERE investor_email = 'live_demo@projectkairos.local'")
    print("[OK] Database verified & cleaned for live demonstration.")

    # 2. Check Database State
    print("\n[Step 2] Current Mapped Schemes and Exits:")
    schemes = read_sql("SELECT COUNT(*) FROM scheme_master").iloc[0, 0]
    managers = read_sql("SELECT COUNT(DISTINCT manager_name) FROM manager_scheme_history").iloc[0, 0]
    exits = read_sql("SELECT COUNT(*) FROM change_events").iloc[0, 0]
    print(f"  * Total Schemes: {schemes}")
    print(f"  * Mapped Managers: {managers}")
    print(f"  * Transition Events: {exits}")

    # 3. Register a Portfolio
    print("\n[Step 3] Simulating Investor Portfolio Registration...")
    email = "live_demo@projectkairos.local"
    holdings = [
        {"scheme_name": "HDFC Balanced Advantage Fund - Growth Plan", "amount": 500000},
        {"scheme_name": "SBI Bluechip Fund - Direct Plan - Growth", "amount": 350000}
    ]
    print(f"  Registering portfolio for: {email}")
    result = register_portfolio_rows(email, holdings)
    
    print("\n  Matched Holdings Stored:")
    for row in result["stored"]:
        print(f"    [OK] {row['query']} -> Code: {row['scheme_code']} (Score: {row['match_score']:.2f})")
    
    if result["unmatched"]:
        print("  Warnings (Unmatched):")
        for row in result["unmatched"]:
            print(f"    [WARN] {row['query']} - Status: {row['match_status']}")

    # 4. Trigger Surveillance Scan
    print("\n[Step 4] Running Live Surveillance Transition Scan...")
    # We pass days=1500 to capture historical demo exits (like Prashant Jain / Sohini Andani)
    scan_summary = run_investor_alert_scan(days=1500)
    print(f"  Surveillance Run Summary: {scan_summary}")

    # 5. Check Alert Log
    print("\n[Step 5] Auditing Generated Alert Logs:")
    alerts = read_sql("SELECT alert_id, recipient, subject, delivery_status, error_message FROM alert_log WHERE investor_email = ?", (email,))
    if alerts.empty:
        print("  [WARN] No alerts generated. Checking if events fell outside criteria.")
    else:
        for _, alert in alerts.iterrows():
            print(f"  * Alert {alert['alert_id']}: {alert['subject']}")
            print(f"    Status: {alert['delivery_status']} | Error: {alert['error_message']}")

    print("\n" + "=" * 60)
    print("                  WALKTHROUGH COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    run_live_walkthrough()
