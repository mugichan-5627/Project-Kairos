# Project Kairos Current State Handover

Date: 2026-06-02

## Executive Summary

Project Kairos is now a working Streamlit/Python prototype for Indian mutual fund manager transition intelligence. It has moved from a scraper-first idea to a database-first architecture:

```text
curated truth database + public NAV/factor data + evidence audit trail + deterministic analytics + optional LLM explanations
```

The most recent build fixed several silent-output risks:

- broken value/HML factor math
- change detector reading stale legacy history
- fragile RBI-only risk-free-rate fallback
- lack of current-manager/lineage seed data
- weak scorecard behavior when peer counts are tiny

The app runs locally at:

```text
http://localhost:8501
```

Main project folder:

```text
C:\Users\Moosa\Downloads\Project Kairos\fund_manager_tracker
```

## Current Major Capabilities

### 1. Setup Wizard

The Streamlit Setup Wizard now supports:

- AMFI scheme master load
- NAV history load
- NSE factor load
- manual NSE factor CSV upload
- FBIL/CCIL/fallback risk-free-rate load
- starter manager-transition seed import
- current-manager seed import
- scheme-lineage seed import
- canonical-to-legacy sync
- change detection
- attribution
- scorecards
- DiD diagnostics
- Tavily evidence search
- evidence classification
- daily monitor trigger
- NVIDIA API key test

### 2. Canonical Truth Layer

The system now has canonical manager identity/tenure tables:

- `manager_identity`
- `manager_alias`
- `manager_tenure`

Legacy table still exists:

- `manager_scheme_history`

Important update: the change detector now reads `manager_tenure` first and only falls back to `manager_scheme_history` if canonical data is absent.

### 3. Starter Data

Seed files exist under:

```text
seed_data/
```

Important files:

- `starter_manager_transitions.csv`
- `manager_transition_seed_template.csv`
- `current_manager_seed.csv`
- `scheme_lineage_seed.csv`
- `rfr_monthly_fallback.csv`

The starter transition seed currently has 14 rows. A clean test import generated 8 change events.

Important caveat: the seed rows are useful demo/bootstrap fuel, but they are not yet investment-grade. Dates, scheme codes, roles, and successor/predecessor fields need human/source verification.

### 4. Evidence and Claim Workflow

Evidence pipeline:

```text
source_evidence -> manager_claims -> review -> promotion into manager_tenure
```

Implemented:

- source-weighted confidence
- Tavily evidence storage
- raw evidence classification
- NVIDIA strict JSON judge
- parse failure handling
- claim promotion
- accept/needs-review/reject controls in Evidence Review page

System confidence is not based on LLM self-confidence.

Current source weights:

| Source | Weight |
|---|---:|
| AMFI SID / SID PDF | 1.00 |
| SEBI circular | 0.90 |
| ValueResearch | 0.85 |
| ET / Mint / Business Standard | 0.70 |
| Tavily search result | 0.55 |
| LLM extraction only | 0.35 |

Promotion threshold:

```text
system_confidence >= 0.70
```

LLM confidence is advisory only.

### 5. LLM Layer

NVIDIA remains the only implemented LLM provider.

Current LLM uses:

- transition briefs
- portfolio briefs
- evidence/claim judging

Strict JSON schema is enforced for claim judging. On malformed JSON:

1. retry once with stricter prompt
2. log raw output
3. mark parse failure
4. set claim to `needs_review`
5. never auto-promote

### 6. Factor Model

Production regression is now Carhart-style 4-factor:

- market excess return
- size tilt
- value tilt
- momentum tilt

QMJ was removed from production regression.

Critical fix already implemented:

Old broken formula:

```text
value - (market - value) = 2 * value - market
```

New formula:

```text
Value Tilt Factor = NIFTY500_VALUE_50_return - NIFTY500_return
```

This is deliberately labelled **Value Tilt Factor**, not true Fama-French HML.

### 7. Risk-Free Rate

Risk-free-rate loader now tries:

1. FBIL benchmark page
2. CCIL T-Bill page
3. versioned fallback CSV: `seed_data/rfr_monthly_fallback.csv`

The fallback CSV is safer than a silent constant rate, but it still needs replacement with verified monthly 91-day T-Bill history before serious use.

### 8. Time-Series Alignment

Regression inputs are aligned to month-end before joining:

```python
resample("ME").last()
```

Incomplete months are dropped and logged in `data_quality_log`.

Regression fails safely with:

```text
insufficient_aligned_data
```

if fewer than 12 complete monthly observations remain.

### 9. DiD Diagnostics

`did_diagnostics` table exists.

The app now flags weak DiD assumptions when pre-change fund alpha and category median alpha trends diverge.

UI message:

```text
DiD result low confidence - pre-change trend divergence detected
```

### 10. Portfolio Scanner

Portfolio scanner now joins current-manager snapshots when available.

If current-manager data is missing, it warns users instead of pretending full current-risk coverage exists.

### 11. Daily Monitor

`monitor.py` exists.

It:

- runs Google News RSS across AMCs
- classifies new evidence
- triggers Tavily deeper search when transition claims are found
- can send desktop notification via `plyer`

It is not yet scheduled automatically.

### 12. DuckDB Sidecar

DuckDB has been added as a sidecar analytical helper, not a replacement for SQLite.

File:

```text
src/analytics/duckdb_analytics.py
```

Purpose:

- future bulk rolling regression
- factor coverage summary
- NAV month-count scans

SQLite remains the transactional store.

## Important Files for Review

Core:

```text
db_setup.py
admin_tasks.py
app.py
tests_smoke.py
```

Data:

```text
src/data/canonical_manager.py
src/data/nse_factors.py
src/data/rbi_risk_free.py
src/data/seed_imports.py
src/data/manager_history_importer.py
```

Analytics:

```text
src/analytics/factor_model.py
src/analytics/did_diagnostics.py
src/analytics/pipeline_runner.py
src/analytics/duckdb_analytics.py
```

Detection/scoring:

```text
src/detection/change_detector.py
src/scoring/scorecard.py
```

Evidence/LLM:

```text
src/intelligence/confidence.py
src/intelligence/claim_promotion.py
src/intelligence/llm_judge.py
src/intelligence/update_manager_database.py
src/llm/nvidia_client.py
```

UI:

```text
src/pages/setup_wizard.py
src/pages/intelligence_review.py
src/pages/scheme_history.py
src/pages/portfolio_scanner.py
src/pages/data_status.py
```

Seed files:

```text
seed_data/starter_manager_transitions.csv
seed_data/current_manager_seed.csv
seed_data/scheme_lineage_seed.csv
seed_data/rfr_monthly_fallback.csv
```

## Verification Already Run

These commands passed after the latest fixes:

```powershell
python -m compileall fund_manager_tracker
python db_setup.py
python tests_smoke.py
python admin_tasks.py schema
```

Additional clean seed test:

```text
starter seed imported 14 rows
generated 8 change events
```

## Remaining Weaknesses

### 1. Seed Data Needs Human Verification

The seed data is useful but not yet authoritative. Review exact:

- scheme codes
- scheme lineage
- tenure start/end dates
- successor/predecessor fields
- lead vs co-manager roles
- source URLs

### 2. Factor Data Needs Reliability Work

Niftyindices POST endpoint may be brittle. Manual CSV upload exists and may be more reliable for now.

The reviewer should decide whether the production path should be:

1. manual CSV first, automated POST second, or
2. automated POST first, manual CSV fallback.

### 3. RFR Fallback CSV Needs Replacement

`rfr_monthly_fallback.csv` is a versioned backstop, not a verified benchmark-quality dataset.

Best next step: build a verified 2010-present monthly 91-day T-Bill series from FBIL/CCIL/RBI Handbook.

### 4. Current Manager Coverage Is Thin

`current_manager_seed.csv` only has a small starting set.

Portfolio Scanner will be much better after adding 50-100 current manager rows.

### 5. Scheme Lineage Coverage Is Very Small

`scheme_lineage_seed.csv` only has a few starter mappings.

Historical attribution across SEBI recategorization and mergers still needs much more lineage data.

### 6. Scorecard Calibration Is Still Early

A peer-count guard exists. If peer count is under 10, Kairos uses absolute thresholds and shows a warning.

But scorecard weights still need backtesting.

### 7. Peer Attribution and DiD Need Better Controls

Current DiD control is category median. A stronger next step is factor-matched peers:

```text
choose 5-10 peers with most similar pre-change MKT/SMB/value/momentum betas
```

This is more defensible than broad SEBI category median.

### 8. Monitor Needs Scheduling and Deduplication

`monitor.py` exists but is manually triggered.

Needs:

- daily scheduling
- URL/content deduplication
- better severity scoring
- notification settings

## My Recommended Next Moves

### Priority 1: Data Truth

Build a verified seed database:

- 50 manager transitions
- 50 current-manager records
- 30 scheme lineage events
- all source URLs filled
- all rows reviewed

This will unlock the rest of Kairos more than any modeling improvement.

### Priority 2: Verified RFR and Factor Data

Add:

- verified monthly RFR CSV
- manual factor CSV import validation
- factor coverage dashboard
- checksum/date coverage reports

### Priority 3: Factor-Matched Peer DiD

Replace category-median DiD with:

```text
factor-matched peer control
```

Use pre-change factor betas to find nearest peers, then compare post-change alpha drift.

### Priority 4: Current Manager Risk Layer

Portfolio Scanner should show:

- current manager
- tenure length
- current manager historical score
- previous manager transition history
- whether current manager is newly assigned

### Priority 5: Evidence Review Maturity

Add:

- edit forms for claims before promotion
- grouped corroboration view
- duplicate source detection
- source trust badges
- accepted-claim provenance card

### Priority 6: Better Demo Dataset

Make a clean demo bundle:

- 10 verified flagship transitions
- NAVs for those schemes
- factor data covering those windows
- screenshots/report output

Prashant Jain / HDFC should be the showcase case.

## Questions for the Next Reviewing Model

1. Is the current canonical schema enough, or should `manager_tenure` become a richer event table with explicit successor/predecessor relationships?
2. Should `manager_scheme_history` be replaced with a SQL view over canonical tables?
3. Should manual NSE factor CSV upload be the default production path?
4. What is the best verified source for monthly 91-day T-Bill rates in India?
5. How should current-manager data be collected reliably without scraping AMC sites?
6. What 50 manager transitions should be curated first?
7. What 30 scheme lineage events matter most for equity/hybrid attribution?
8. Is the Value Tilt Factor acceptable, or should a different free value/growth proxy be used?
9. How should factor-matched peer groups be built and validated?
10. What should the Investor Risk Score formula be after backtesting?
11. Should DuckDB be used for all rolling regressions now?
12. What tests are still missing before this can be called production-grade?

## Bottom Line

Kairos is now structurally much stronger than the initial scaffold. The biggest remaining bottleneck is no longer architecture; it is verified data coverage.

The next high-leverage work is:

```text
verified truth data + verified factor/RFR inputs + factor-matched peer DiD
```

Once those are in place, Kairos can produce genuinely credible manager transition intelligence rather than just a technically impressive prototype.

