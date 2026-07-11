import sqlite3
import os, glob
import re

db_path = r"c:\Users\Moosa\Downloads\Project Kairos\fund_manager_tracker\fund_data.db"
emails_path = r"c:\Users\Moosa\Downloads\Project Kairos\sent_emails\*.html"

print("--- QUERY 1 ---")
conn = sqlite3.connect(db_path)
rows = conn.execute('''
  SELECT ar.window_type, ar.alpha_annualized, ar.alpha_tstat,
         ar.adj_r2, ar.observations, ar.model_status,
         ar.beta_mkt, ar.beta_smb, ar.beta_hml, ar.beta_wml
  FROM attribution_results ar
  JOIN change_events ce ON ce.event_id = ar.event_id
  WHERE ce.scheme_code = '100119'
  ORDER BY ar.window_type, ar.created_at DESC
''').fetchall()
for r in rows: print(r)
conn.close()

print("--- QUERY 2 ---")
conn = sqlite3.connect(db_path)
rows = conn.execute('''
  SELECT tif.*
  FROM transition_impact_forecasts tif
  JOIN change_events ce ON ce.event_id = tif.event_id
  WHERE ce.scheme_code = '100119'
''').fetchall()
for r in rows: print(r)
conn.close()

print("--- QUERY 3 ---")
conn = sqlite3.connect(db_path)
rows = conn.execute('''
  SELECT fmd.*
  FROM factor_matched_did fmd
  JOIN change_events ce ON ce.event_id = fmd.event_id
  WHERE ce.scheme_code = '100119'
''').fetchall()
for r in rows: print(r)
conn.close()

print("--- QUERY 4 ---")
conn = sqlite3.connect(db_path)
print('Factor rows:', conn.execute('SELECT COUNT(*) FROM factor_data').fetchone())
print('Date range:', conn.execute('SELECT MIN(factor_date), MAX(factor_date) FROM factor_data').fetchone())
print('Fallback rows:', conn.execute("SELECT COUNT(*) FROM factor_data WHERE factor_is_fallback = 1 OR rfr_is_fallback = 1").fetchone())
print('RFR null rows:', conn.execute('SELECT COUNT(*) FROM factor_data WHERE rfr_monthly IS NULL').fetchone())
conn.close()

print("--- QUERY 5 ---")
files = sorted(glob.glob(emails_path), key=os.path.getmtime, reverse=True)
if files:
    print('Latest email file:', files[0])
    content = open(files[0], encoding='utf-8', errors='ignore').read()
    text = re.sub('<[^>]+>', ' ', content)
    text = re.sub(' +', ' ', text)
    print(text[:3000])
else:
    print("No emails found.")
