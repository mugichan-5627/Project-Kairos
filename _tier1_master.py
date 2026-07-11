"""
PROJECT KAIROS - MASTER PIPELINE EXECUTION
Complete Tier 1 manager analytics for all target managers.

Verified Scheme Codes (all Regular Plan Growth):
  112277 = Axis Large Cap Fund (was Axis Bluechip) - Jinesh Gopani
  105758 = HDFC Mid Cap Fund (was HDFC Mid-Cap Opportunities) - Chirag Setalvad
  100377 = Nippon India Growth Mid Cap Fund - Sunil Singhania
  104908 = Kotak Midcap Fund (was Kotak Emerging Equity) - Pankaj Tibrewal
  112090 = Kotak Flexicap Fund (was Kotak Standard Multicap) - Nilesh Shah/Harsha Upadhyaya
  103504 = SBI Large Cap Fund (was SBI Bluechip) - Sohini Andani

Manager Tenure Dates (from public AMFI/fund house records):
  Chirag Setalvad:   HDFC Mid Cap     2007-07-01 to 2023-06-15
  Sunil Singhania:   Nippon Growth    2003-12-01 to 2018-09-28
  Pankaj Tibrewal:   Kotak Midcap     2010-10-01 to 2024-12-31
  Nilesh Shah:       Kotak Flexicap   2009-09-17 to 2019-12-31  (Harsha Upadhyaya took over)
  Sohini Andani:     SBI Large Cap    2010-07-01 to 2023-09-30
  Jinesh Gopani:     Axis Large Cap   2012-01-01 to 2023-03-31
"""
import sys, os, time, sqlite3, traceback
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'fund_manager_tracker')

from src.data.amfi_loader import AMFILoader
from src.detection.change_detector import ManagerChangeDetector
from src.analytics.pipeline_runner import run_full_pipeline
from src.utils.db import get_connection

loader = AMFILoader()
detector = ManagerChangeDetector()

# ═══════════════════════════════════════════════════
# MANAGER DEFINITIONS
# ═══════════════════════════════════════════════════
# Each entry: (manager_name, scheme_code, start_date, end_date)
# end_date = when manager departed the fund
MANAGERS_TO_SEED = [
    ('Chirag Setalvad',    '105758', '2007-07-01', '2023-06-15'),
    ('Sunil Singhania',    '100377', '2003-12-01', '2018-09-28'),
    ('Pankaj Tibrewal',    '104908', '2010-10-01', '2024-12-31'),
    ('Nilesh Shah',        '112090', '2009-09-17', '2019-12-31'),
    ('Sohini Andani',      '103504', '2010-07-01', '2023-09-30'),
    ('Jinesh Gopani',      '112277', '2012-01-01', '2023-03-31'),
]

# Existing events that need NAV data + pipeline re-run
EVENTS_TO_RERUN = []  # Will be populated after checking

results = []

# ═══════════════════════════════════════════════════
# PHASE 1: Load NAV data for existing 0-obs events
# ═══════════════════════════════════════════════════
print('=' * 70)
print('PHASE 1: Load NAV for existing events with 0 observations')
print('=' * 70)

with get_connection() as conn:
    zero_obs = conn.execute('''
        SELECT DISTINCT ce.event_id, ce.scheme_code, ce.manager_name
        FROM change_events ce
        JOIN attribution_results ar ON ar.event_id = ce.event_id
        WHERE ar.observations = 0
    ''').fetchall()

for row in zero_obs:
    event_id, scheme_code, mgr = row['event_id'], row['scheme_code'], row['manager_name']
    print(f'\n  Loading NAV for event {event_id}: {mgr} (scheme {scheme_code})...')
    try:
        with get_connection() as conn:
            existing = conn.execute('SELECT COUNT(*) as c FROM nav_history WHERE scheme_code=?', (scheme_code,)).fetchone()['c']
        if existing == 0:
            count = loader.refresh_nav_history([scheme_code])
            print(f'    Loaded {count} NAV rows')
        else:
            print(f'    Already has {existing} NAV rows')
        
        with get_connection() as conn:
            stats = conn.execute('SELECT COUNT(*) as c, MIN(nav_date) as mn, MAX(nav_date) as mx FROM nav_history WHERE scheme_code=?', (scheme_code,)).fetchone()
            nav_count = stats['c']
            print(f'    DB: {nav_count} rows, {stats["mn"]} to {stats["mx"]}')
        
        if nav_count >= 50:
            EVENTS_TO_RERUN.append(event_id)
        else:
            print(f'    SKIPPING: Only {nav_count} NAV rows (need 50+)')
    except Exception as e:
        print(f'    ERROR: {e}')

# ═══════════════════════════════════════════════════
# PHASE 2: Seed new managers + load NAV + create events
# ═══════════════════════════════════════════════════
print()
print('=' * 70)
print('PHASE 2: Seed new managers, load NAV, create change events')
print('=' * 70)

new_event_ids = []

for mgr_name, scheme_code, start_date, end_date in MANAGERS_TO_SEED:
    print(f'\n--- {mgr_name} on scheme {scheme_code} ---')
    
    # Check if this exact combination already has a change event
    with get_connection() as conn:
        existing = conn.execute(
            'SELECT event_id FROM change_events WHERE scheme_code=? AND manager_name=?',
            (scheme_code, mgr_name)
        ).fetchone()
    
    if existing:
        event_id = existing['event_id']
        print(f'  Already has event_id={event_id}, checking analytics...')
        with get_connection() as conn:
            ar = conn.execute(
                'SELECT window_type, alpha_annualized, observations FROM attribution_results WHERE event_id=?',
                (event_id,)
            ).fetchall()
        if ar:
            for r in ar:
                alpha = round(r['alpha_annualized']*100, 2) if r['alpha_annualized'] else 'N/A'
                print(f'    {r["window_type"]}: alpha={alpha}%, obs={r["observations"]}')
                if r['observations'] == 0:
                    EVENTS_TO_RERUN.append(event_id)
            if all(r['observations'] > 0 and r['alpha_annualized'] is not None for r in ar):
                print(f'  COMPLETE - skipping')
                results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': 'already_complete', 'event_id': event_id})
                continue
        else:
            EVENTS_TO_RERUN.append(event_id)
        results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': 'needs_rerun', 'event_id': event_id})
        continue
    
    # Step A: Seed manager identity + tenure
    print(f'  A. Seeding identity and tenure...')
    try:
        with get_connection() as conn:
            conn.execute('''
                INSERT OR IGNORE INTO manager_identity 
                (canonical_name, first_known_date, last_known_date)
                VALUES (?, ?, ?)
            ''', (mgr_name, start_date, end_date))
            
            manager_id = conn.execute(
                'SELECT manager_id FROM manager_identity WHERE canonical_name=?',
                (mgr_name,)
            ).fetchone()['manager_id']
            
            # Get scheme_name and amc_name from scheme_master if available
            sm = conn.execute('SELECT scheme_name, amc_name FROM scheme_master WHERE scheme_code=?', (scheme_code,)).fetchone()
            scheme_name = sm['scheme_name'] if sm else None
            amc_name = sm['amc_name'] if sm else None
            
            conn.execute('''
                INSERT OR IGNORE INTO manager_tenure (
                    manager_id, scheme_code, scheme_name, amc_name, role,
                    start_date, end_date, is_verified,
                    source_type, event_type, transition_type,
                    analytics_status, confidence_score
                ) VALUES (?, ?, ?, ?, 'lead', ?, ?, 1,
                    'AMFI_SID', 'exit', 'resignation',
                    'pending', 1.0)
            ''', (manager_id, scheme_code, scheme_name, amc_name, start_date, end_date))
        
        print(f'    manager_id={manager_id}')
    except Exception as e:
        print(f'    ERROR seeding: {e}')
        results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': f'seed_error: {e}'})
        continue
    
    # Step B: Load NAV history
    print(f'  B. Loading NAV history...')
    try:
        with get_connection() as conn:
            existing_navs = conn.execute('SELECT COUNT(*) as c FROM nav_history WHERE scheme_code=?', (scheme_code,)).fetchone()['c']
        
        if existing_navs < 50:
            count = loader.refresh_nav_history([scheme_code])
            print(f'    Loaded {count} NAV rows')
        else:
            print(f'    Already has {existing_navs} NAV rows')
        
        with get_connection() as conn:
            stats = conn.execute('SELECT COUNT(*) as c, MIN(nav_date) as mn, MAX(nav_date) as mx FROM nav_history WHERE scheme_code=?', (scheme_code,)).fetchone()
            nav_count = stats['c']
            print(f'    DB: {nav_count} rows, {stats["mn"]} to {stats["mx"]}')
        
        if nav_count < 50:
            print(f'    INSUFFICIENT DATA: Only {nav_count} NAV rows. Flagging as insufficient.')
            results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': 'insufficient_nav_data', 'nav_rows': nav_count})
            continue
    except Exception as e:
        print(f'    ERROR loading NAV: {e}')
        results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': f'nav_error: {e}'})
        continue
    
    # Step C: Run change detection to create change event
    print(f'  C. Running change detection...')
    try:
        new_events = detector.refresh_change_events()
        print(f'    Detected {new_events} new change events')
        
        with get_connection() as conn:
            event_row = conn.execute(
                'SELECT event_id FROM change_events WHERE scheme_code=? AND manager_name=?',
                (scheme_code, mgr_name)
            ).fetchone()
        
        if event_row:
            event_id = event_row['event_id']
            new_event_ids.append(event_id)
            print(f'    Event created: event_id={event_id}')
        else:
            print(f'    No event created by detector. Creating manually...')
            # Manually insert change event
            with get_connection() as conn:
                conn.execute('''
                    INSERT INTO change_events 
                    (scheme_code, manager_name, manager_key, change_type, change_date,
                     pre_tenure_months, predecessor_manager, successor_manager, 
                     amc_name, category, confidence_score)
                    VALUES (?, ?, ?, 'Full Exit', ?, ?, ?, NULL, ?, ?, 1.0)
                ''', (
                    scheme_code, mgr_name,
                    f'{mgr_name} | {amc_name or "Unknown AMC"}',
                    end_date,
                    None,  # pre_tenure_months will be computed
                    mgr_name,
                    amc_name, None
                ))
                event_id = conn.execute(
                    'SELECT event_id FROM change_events WHERE scheme_code=? AND manager_name=? ORDER BY event_id DESC LIMIT 1',
                    (scheme_code, mgr_name)
                ).fetchone()['event_id']
            new_event_ids.append(event_id)
            print(f'    Manual event created: event_id={event_id}')
        
        results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': 'event_created', 'event_id': event_id})
    except Exception as e:
        print(f'    ERROR in change detection: {e}')
        traceback.print_exc()
        results.append({'manager': mgr_name, 'scheme': scheme_code, 'status': f'detect_error: {e}'})

# ═══════════════════════════════════════════════════
# PHASE 3: Run full pipeline for all pending events
# ═══════════════════════════════════════════════════
print()
print('=' * 70)
print('PHASE 3: Running full analytics pipeline')
print('=' * 70)

all_events_to_run = list(set(EVENTS_TO_RERUN + new_event_ids))
print(f'Events to process: {all_events_to_run}')

for event_id in all_events_to_run:
    with get_connection() as conn:
        ev = conn.execute('SELECT scheme_code, manager_name FROM change_events WHERE event_id=?', (event_id,)).fetchone()
    
    if not ev:
        print(f'\n  Event {event_id}: NOT FOUND in change_events, skipping')
        continue
    
    mgr_name = ev['manager_name']
    scheme_code = ev['scheme_code']
    print(f'\n  Event {event_id}: {mgr_name} (scheme {scheme_code})')
    
    try:
        # Clear old attribution results for this event to get fresh ones
        with get_connection() as conn:
            conn.execute('DELETE FROM attribution_results WHERE event_id=?', (event_id,))
            conn.execute('DELETE FROM rolling_alpha_series WHERE scheme_code=?', (scheme_code,))
        
        result = run_full_pipeline(event_id)
        
        if result.get('status') == 'ok':
            for row in result.get('rows', []):
                alpha = round(row.get('alpha_annualized', 0) * 100, 2)
                obs = row.get('observations', 0)
                window = row.get('window_type', '?')
                print(f'    {window}: alpha={alpha}%, obs={obs}, R2={round(row.get("adj_r2", 0), 4)}')
            rolling = result.get('rolling_windows', 0)
            print(f'    Rolling windows: {rolling}')
        else:
            print(f'    Pipeline status: {result.get("status", "unknown")}')
            for row in result.get('rows', []):
                print(f'    {row.get("window_type","?")}: status={row.get("model_status","?")}, obs={row.get("observations",0)}')
    except Exception as e:
        print(f'    ERROR: {e}')
        traceback.print_exc()

# ═══════════════════════════════════════════════════
# PHASE 4: Final verification
# ═══════════════════════════════════════════════════
print()
print('=' * 70)
print('PHASE 4: Final verification - all analytics results')
print('=' * 70)

with get_connection() as conn:
    rows = conn.execute('''
        SELECT DISTINCT ce.manager_name, ce.scheme_code, ce.event_id,
               ar.window_type, ar.alpha_annualized, ar.alpha_tstat,
               ar.observations, ar.adj_r2, ar.ir_practitioner, ar.ir_classification
        FROM change_events ce
        JOIN attribution_results ar ON ar.event_id = ce.event_id
        ORDER BY ce.manager_name, ce.scheme_code, ar.window_type
    ''').fetchall()

print(f'\n{"Manager":25s} {"Scheme":8s} {"Evt":4s} {"Win":5s} {"Alpha%":8s} {"t-stat":7s} {"Obs":5s} {"R2":7s} {"IR":6s} {"IR Class":12s}')
print('-' * 100)
for r in rows:
    alpha = f"{r['alpha_annualized']*100:.2f}" if r['alpha_annualized'] else 'N/A'
    tstat = f"{r['alpha_tstat']:.2f}" if r['alpha_tstat'] else 'N/A'
    r2 = f"{r['adj_r2']:.4f}" if r['adj_r2'] else 'N/A'
    ir = f"{r['ir_practitioner']:.2f}" if r['ir_practitioner'] else 'N/A'
    ir_class = r['ir_classification'] or 'N/A'
    print(f"{r['manager_name']:25s} {r['scheme_code']:8s} {r['event_id']:4d} {r['window_type']:5s} {alpha:>8s} {tstat:>7s} {r['observations']:5d} {r2:>7s} {ir:>6s} {ir_class:12s}")

# Rolling alpha summary
with get_connection() as conn:
    rolling = conn.execute('''
        SELECT scheme_code, COUNT(*) as windows,
               MIN(window_end_date) as first_window,
               MAX(window_end_date) as last_window
        FROM rolling_alpha_series
        GROUP BY scheme_code
    ''').fetchall()

print(f'\n\n{"Scheme":10s} {"Windows":8s} {"First":12s} {"Last":12s}')
print('-' * 50)
for r in rolling:
    print(f"{r['scheme_code']:10s} {r['windows']:8d} {r['first_window']:12s} {r['last_window']:12s}")

print('\n\nDone.')
