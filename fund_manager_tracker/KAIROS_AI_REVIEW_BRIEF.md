# Project Kairos v2 AI Review Handoff

## 1. Purpose and Current Direction

Project Kairos is a Streamlit-based intelligence system for Indian mutual fund manager transitions, alpha attribution, and investor transition-risk scoring.

The intended product is not a generic fund dashboard. It is a fund-manager transition intelligence warehouse that answers:

1. Did a fund manager transition occur?
2. What evidence supports the transition?
3. Was the manager historically adding alpha beyond factor/category/house effects?
4. Is the transition risky for remaining investors?
5. Can the system explain the answer without silently hallucinating facts?

The current philosophy is:

```text
Curated database + public market data + evidence audit trail + deterministic analytics + LLM explanations
```

not:

```text
LLM searches the web and directly becomes the source of truth
```

The latest strengthening pass implemented the v2 plan: starter seed data, canonical manager identity/tenure tables, source-weighted confidence, claim promotion, NVIDIA strict JSON handling, Carhart 4-factor model, NSE/RBI loader scaffolds, month-end alignment, and DiD parallel-trends diagnostics.

Latest critical-fix pass after external review:

- fixed the mathematically incorrect HML formula by replacing it with a correctly labelled long-only `Value Tilt Factor`
- moved change detection to canonical `manager_tenure`
- added FBIL -> CCIL -> versioned CSV risk-free fallback
- added manual NSE factor CSV upload support
- added live daily monitor scaffold
- added current-manager and scheme-lineage seed files
- added scorecard peer-count guard
- added DuckDB sidecar analytics helper

## 2. Current Implementation Snapshot

Project root:

```text
C:\Users\Moosa\Downloads\Project Kairos\fund_manager_tracker
```

Important files:

```text
app.py
admin_tasks.py
data_pipeline.py
db_setup.py
tests_smoke.py
requirements.txt
.env.example
KAIROS_AI_REVIEW_BRIEF.md

seed_data/
  manager_transition_seed_template.csv
  starter_manager_transitions.csv

src/
  analytics/
    factor_model.py
    duckdb_analytics.py
    did_diagnostics.py
    metrics.py
    peer_attribution.py
    bhb_attribution.py
    pipeline_runner.py
  data/
    amfi_loader.py
    canonical_manager.py
    manager_history_importer.py
    news_monitor.py
    nse_factors.py
    rbi_risk_free.py
    seed_imports.py
    sebi_scraper.py
    sid_parser.py
    vro_scraper.py
  detection/
    change_detector.py
  intelligence/
    claim_promotion.py
    claim_store.py
    confidence.py
    evidence_extractor.py
    llm_judge.py
    tavily_search.py
    update_manager_database.py
  llm/
    nvidia_client.py
  pages/
    setup_wizard.py
    intelligence_review.py
    feed.py
    manager_profile.py
    scheme_history.py
    portfolio_scanner.py
    data_status.py
```

## 3. What Was Implemented in v2

### 3.1 Starter Seed Data

Two CSVs now exist:

- `seed_data/manager_transition_seed_template.csv`
- `seed_data/starter_manager_transitions.csv`

The starter seed currently has 14 rows and includes well-known Indian MF transition/reference candidates such as:

- Prashant Jain at HDFC Mutual Fund
- Chirag Setalvad as HDFC successor/reference row
- Kenneth Andrade at IDFC Mutual Fund
- Anoop Bhaskar at IDFC/Bandhan transition context
- Jinesh Gopani at Axis Mutual Fund
- Vetri Subramaniam at UTI Mutual Fund
- Ajay Tyagi successor/reference row
- Ashish Naik successor/reference row
- Sankaran Naren and Neelesh Surana long-tenure reference rows

Important caveat: the starter seed is explicitly for demo/bootstrap fuel. Some rows include approximate dates, successor assumptions, or scheme-role assumptions. They are marked via `source`, `source_type`, `source_url`, `confidence_score`, and `notes` so they can be reviewed before being treated as investment-grade truth.

Seed columns:

```csv
scheme_code,scheme_name,amc_name,manager_name,role,rank,start_date,end_date,source,source_type,source_url,confidence_score,predecessor_manager,successor_manager,event_type,notes,evidence_ids
```

The Setup Wizard has buttons to download the blank template, download the starter seed, and import the starter seed.

Clean-database test after detector rewrite:

```text
starter seed imported 14 rows
generated 8 change events
```

### 3.2 Canonical Manager Identity and Tenure Layer

New canonical tables were added:

- `manager_identity`
- `manager_alias`
- `manager_tenure`

The older `manager_scheme_history` table is still retained for backward compatibility because the existing detector and pages already consume it.

Update: the detector now reads canonical `manager_tenure` first and falls back to `manager_scheme_history` only when canonical data is absent. This resolves the earlier two-sources-of-truth risk for event detection, though the legacy table still exists for older UI compatibility.

The file `src/data/canonical_manager.py` handles:

- seed CSV validation
- manager identity creation
- alias insertion
- canonical tenure insertion
- canonical-to-legacy sync into `manager_scheme_history`
- legacy-to-canonical backfill

This means Kairos now has a migration path instead of permanently relying only on the legacy table.

### 3.3 Evidence, Claims, Confidence, and Promotion

The evidence pipeline now has these tables:

- `source_evidence`
- `manager_claims`
- `llm_audit_log`

The intended flow:

```text
Tavily/news/source result -> source_evidence -> manager_claims -> review -> accepted claim -> manager_tenure + manager_scheme_history
```

Implemented modules:

- `src/intelligence/confidence.py`
- `src/intelligence/claim_store.py`
- `src/intelligence/claim_promotion.py`
- `src/intelligence/evidence_extractor.py`
- `src/intelligence/update_manager_database.py`

System confidence is source-based, not LLM self-graded.

Base weights:

| Source type | Weight |
|---|---:|
| AMFI SID / SID PDF | 1.00 |
| SEBI circular | 0.90 |
| ValueResearch | 0.85 |
| ET / Mint / Business Standard | 0.70 |
| Tavily search result | 0.55 |
| LLM extraction only | 0.35 |

Corroboration multiplier:

- 1 source: `x1.00`
- 2 sources agree: `x1.15`
- 3+ sources agree: `x1.25`
- cap final confidence at `1.00`

Promotion rule:

- system confidence `>= 0.70`: promotion can proceed
- below `0.70`: status becomes `needs_review`
- LLM confidence is advisory only

The Evidence Review page now supports:

- viewing raw evidence
- viewing pending claims
- recomputing confidence
- promoting claims
- marking claims as needs-review
- rejecting claims
- running LLM judge for a claim

### 3.4 NVIDIA LLM JSON Hardening

NVIDIA remains the only LLM provider implemented.

The LLM is used only for:

- transition briefs
- portfolio briefs
- evidence/claim judging

It does not generate numeric analytics.

`src/intelligence/llm_judge.py` now enforces strict JSON parsing with this schema:

```json
{
  "verdict": "accept|reject|needs_review",
  "confidence": 0.0,
  "reasoning": "...",
  "extracted_manager_name": "...",
  "extracted_scheme": "...",
  "extracted_amc": "...",
  "extracted_date": "YYYY-MM-DD|null",
  "claim_type": "manager_exit|manager_join|amc_switch|manager_related"
}
```

Failure behavior:

1. Try normal strict prompt.
2. If JSON parse fails, retry once with: `Respond ONLY with valid JSON. No other text.`
3. If retry also fails:
   - log raw output in `llm_audit_log`
   - set `parse_status = failed`
   - set claim status to `needs_review`
   - never auto-promote
   - UI displays: `LLM parse failed - manual review required`

`llm_audit_log` now stores:

- model
- prompt hash
- input summary
- raw output
- parsed JSON
- parse status
- retry count
- error message
- source evidence IDs

### 3.5 Factor Model Changed to Carhart 4-Factor

The production regression now uses Carhart 4-factor only:

- `MKT_RF`
- `SMB`
- `HML`
- `WML`

QMJ was removed from production regression outputs because the prior proxy mixed quality and low-volatility exposure and was methodologically weak.

Important correction: the previous HML formula was wrong because it computed:

```text
NIFTY500_VALUE_50 - (NIFTY500 - NIFTY500_VALUE_50)
= 2 * value - market
```

This has now been replaced with:

```text
Value Tilt Factor = NIFTY500_VALUE_50_return - NIFTY500_return
```

It is intentionally labelled `Value Tilt Factor`, not true Fama-French HML. Future upgrade: use a cleaner long-short value/growth proxy such as NIFTY200 Value 30 minus a defensible growth/momentum proxy if reliable data is available.

`src/analytics/factor_model.py` now:

- excludes QMJ from `FACTOR_COLS`
- labels model output as `Carhart 4-factor`
- aligns dates to month-end
- drops incomplete months
- logs dropped observation counts in `data_quality_log`
- returns `insufficient_aligned_data` if fewer than 12 aligned monthly observations remain
- flags fallback factor data and fallback RFR exposure in regression results

### 3.6 NSE Factor Loader

`src/data/nse_factors.py` was rebuilt.

Primary path:

- URL: `https://www.niftyindices.com/Backpage.aspx/downloadIndexHistory`
- Method: `POST`
- Body pattern:

```json
{
  "cinfo": "{\"name\":\"NIFTY 50\",\"startDate\":\"01-Jan-2010\",\"endDate\":\"31-Dec-2024\",\"indexName\":\"NIFTY 50\"}"
}
```

Indices attempted:

- `NIFTY 50`
- `NIFTY 500`
- `NIFTY SMALLCAP 250`
- `NIFTY500 VALUE 50`
- `NIFTY MOMENTUM 50`
- `NIFTY MIDCAP 150`

The loader parses `Date` and `Close`, uses only close prices, resamples with:

```python
resample("ME").last()
```

Then computes monthly returns and factors:

- `MKT_RF = NIFTY 500 return - RFR`
- `SMB = NIFTY SMALLCAP 250 - NIFTY 50`
- `Value Tilt Factor = NIFTY500 VALUE 50 - NIFTY 500`
- `WML = NIFTY MOMENTUM 50 - NIFTY 500`

Fallback:

- yfinance remains available
- fallback rows are flagged with `factor_is_fallback = 1`
- source status records `fallback_yfinance`

Reviewer should verify whether the niftyindices endpoint body/headers work consistently in the current environment, as this endpoint can be finicky.

### 3.7 Risk-Free Rate Loader

`src/data/rbi_risk_free.py` now avoids relying on RBI heuristic parsing as the only path.

Order of sources:

1. FBIL benchmark page: `https://www.fbil.org.in/benchmark.html`
2. CCIL T-Bill page: `https://www.ccilindia.com/web/ccil/ccil-tbill-index`
3. versioned fallback CSV: `seed_data/rfr_monthly_fallback.csv`

Current implementation:

- fetches/parses FBIL and CCIL tables heuristically
- averages weekly annualized yields into monthly rates
- stores monthly RFR in `factor_data.rfr_monthly`
- keeps compatibility with `factor_data.risk_free_monthly`
- uses the versioned fallback CSV if live sources fail
- marks fallback with `rfr_is_fallback = 1`

Reviewer should assess whether a more stable CCIL/FBIL downloadable Excel endpoint exists and whether `seed_data/rfr_monthly_fallback.csv` should be replaced with a fully verified monthly historical file.

### 3.8 Month-End Alignment

All regression inputs now align to month-end:

```python
pd.to_datetime(date).dt.to_period("M").dt.to_timestamp("M")
```

Fund NAV monthly returns use:

```python
resample("ME").last().pct_change()
```

Factor data and RFR are also treated as month-end series before joins.

The merged regression dataframe:

- joins on month-end date
- drops rows with missing fund/factor/RFR values
- logs dropped observations
- fails safely with `insufficient_aligned_data` if fewer than 12 complete months remain

### 3.9 DiD Parallel-Trends Diagnostics

`src/analytics/did_diagnostics.py` was added.

It computes a pre-change diagnostic by comparing:

- fund rolling alpha trend
- category median rolling alpha trend

over the pre-change period where data exists.

Outputs are stored in:

- `did_diagnostics`

Stored fields:

- `event_id`
- `scheme_code`
- `fund_trend_slope`
- `category_trend_slope`
- `slope_difference`
- `diagnostic_label`
- `message`

If the diagnostic fails, the Scheme Alpha History page still shows the DiD/diagnostic data but displays:

```text
DiD result low confidence - pre-change trend divergence detected
```

This avoids hiding the number while making the assumption failure explicit.

## 4. Streamlit App State

The app is available at:

```text
http://localhost:8501
```

Pages:

### Setup Wizard

Now supports:

- load AMFI scheme master
- load NAV history
- load NSE factors
- load RBI risk-free rate
- manual NSE factor CSV upload
- download blank seed template
- download starter seed CSV
- import starter seed
- import current-manager seed
- import scheme-lineage seed
- import custom manager-history CSV
- sync canonical history
- run change detector
- run Tavily evidence search
- classify raw evidence
- run daily monitor now
- run attribution
- compute scorecards
- run DiD diagnostics
- enter/test NVIDIA key

### Evidence Review

Now supports:

- raw evidence view
- claims view
- parse-failure warnings
- recompute confidence
- promote claim
- mark needs-review
- reject claim
- run LLM judge for evidence or a specific claim

### Scheme Alpha History

Now warns when:

- no curated manager history exists
- aligned data is insufficient
- DiD parallel-trends diagnostic fails

Also labels the production model as Carhart 4-factor and notes QMJ exclusion.

### Data Status

Now includes:

- canonical tenure counts
- pending claim counts
- DiD warning counts
- data quality log
- failed scrape/cache metrics

### Portfolio Risk Scanner

Now joins `current_manager_snapshot` where available and warns when a portfolio holding has no current-manager coverage.

## 5. Admin CLI Tasks

`admin_tasks.py` now supports:

```text
schema
amfi-master
nav
factors
load-nse-factors
load-risk-free-rate
news
detect
attribution
scorecards
import-history
tavily-evidence
classify-evidence
download-seed-template
import-seed
import-starter-seed
sync-canonical-history
judge-claims
promote-claim
reject-claim
run-did-diagnostics
```

Useful commands:

```powershell
python admin_tasks.py download-seed-template
python admin_tasks.py import-starter-seed
python admin_tasks.py load-risk-free-rate
python admin_tasks.py load-nse-factors
python admin_tasks.py run-did-diagnostics
```

Claim-specific:

```powershell
python admin_tasks.py judge-claims --claim-id 123
python admin_tasks.py promote-claim --claim-id 123
python admin_tasks.py reject-claim --claim-id 123
```

## 6. Current Database Tables

Core/legacy:

- `scheme_master`
- `scheme_lineage`
- `nav_history`
- `factor_data`
- `manager_scheme_history`
- `manager_changes`
- `change_events`
- `attribution_results`
- `peer_attribution_results`
- `performance_metrics`
- `manager_scorecards`
- `source_status`
- `failed_scrapes`
- `request_cache`

Canonical truth layer:

- `manager_identity`
- `manager_alias`
- `manager_tenure`

Evidence/intelligence layer:

- `source_evidence`
- `manager_claims`
- `llm_audit_log`

Diagnostics/quality:

- `data_quality_log`
- `did_diagnostics`

Older compatibility tables still present:

- `manager_identity_map`
- `scheme_manager_tenures`

These were from the earlier architecture and may eventually be consolidated into the newer canonical `manager_identity` / `manager_alias` / `manager_tenure` design.

## 7. Verification Performed

The following passed after v2 implementation:

```powershell
python -m compileall fund_manager_tracker
python db_setup.py
python tests_smoke.py
python admin_tasks.py schema
python admin_tasks.py download-seed-template
```

Additional temp-db seed import check:

```text
starter seed imported 14 rows
generated 3 change events
```

Local Streamlit endpoint:

```text
http://localhost:8501 -> HTTP 200
```

Smoke test now covers:

- schema initialization
- synthetic NAV/factor data
- Carhart 4-factor regression
- QMJ absence from factor output
- performance metrics
- manager history persistence
- canonical seed import
- source-weighted confidence formula
- strict LLM JSON parse success
- malformed LLM parse failure path
- claim promotion into truth layer
- cleanup with no demo rows left behind

## 8. Important Remaining Limitations

### 8.1 Starter Seed Is Not Yet Investment-Grade

The starter CSV exists and imports, but its rows still need human/source review.

Reviewer should evaluate:

- exact dates
- exact scheme roles
- successor/predecessor correctness
- scheme-code lineage after renames/mergers
- whether certain rows should be reference tenures rather than transition events

### 8.2 NSE Direct Loader May Need Hardening

The loader uses the requested niftyindices endpoint, but this endpoint may require:

- specific cookies
- anti-CSRF headers
- initial homepage request
- exact payload shape changes

Reviewer should test direct NSE loads in the target environment.

Manual factor CSV upload now exists and should be treated as the most reliable path when niftyindices POST fails.

### 8.3 Risk-Free Rate Fallback Still Needs Better Historical Data

The loader now tries FBIL and CCIL before the curated fallback CSV. The fallback CSV is versioned, but its values should be reviewed/replaced with a fully verified 2010-present monthly 91-day T-Bill series before investment-grade use.

### 8.4 Canonical Detector Migration Mostly Done

The detector now reads canonical `manager_tenure` first. Legacy `manager_scheme_history` still exists for compatibility and should eventually become a view or be removed from user-facing logic.

### 8.5 Live Monitoring Exists But Is Lightweight

`monitor.py` runs Google News RSS, auto-classifies new evidence, triggers Tavily deep search when transition claims are found, and can send desktop notifications. It still needs scheduling and stronger deduplication.

### 8.6 Peer Attribution and DiD Need Productionization

Peer attribution still needs:

- category normalization
- sufficient peer counts
- efficient bulk regression caching
- robust rolling alpha storage
- better treatment of style drift and mandate changes

### 8.7 Scorecard Calibration Is Still Early

The scorecard now has a minimum peer guard: when peer count is below 10, it uses absolute thresholds and stores/displays a warning instead of pretending percentile ranks are robust. The formula still needs backtesting/calibration against real historical transitions.

Potential questions:

- Are weights defensible?
- Should tenure length increase confidence rather than score?
- Should replacement quality be separately modeled?
- How should co-manager exits be discounted?

### 8.8 ValueResearch and SID Automation Are Deprioritized but Still Needed

ValueResearch current-manager scraping and SID parsing still exist, but the current phase prioritizes curated seed + evidence review. Longer term, these should enrich current manager coverage.

## 9. Recommended Review Questions

Please review the current v2 system and advise on:

1. Is the canonical schema sufficient, or should `manager_tenure` include explicit `event_type`, `predecessor_manager_id`, and `successor_manager_id`?
2. Should `manager_scheme_history` be retired soon, or kept as a compatibility table fed by canonical sync?
3. Is the source-weighted confidence formula adequate, or should it separate evidence reliability from extraction certainty?
4. Should the starter seed be promoted only after a manual review queue, even when source type is reputable news?
5. What exact RBI source should be used for a stable 91-day T-Bill monthly series?
6. Is the niftyindices direct POST implementation likely robust enough, or should manual CSV upload be the primary production path?
7. Should QMJ remain fully removed, or be kept as an optional experimental style index outside the production alpha model?
8. What is the right implementation for co-manager attribution?
9. How should scheme mergers/renames be represented in attribution windows?
10. Should DiD use category median alpha, matched style peers, or synthetic controls?
11. What is the minimum viable backtest to validate Investor Risk Score?
12. Should DuckDB be added now for analytics acceleration, or is SQLite enough until data volume grows?
13. What additional tests are highest priority before calling this production-grade?
14. What should be the next 10 starter transitions to curate manually?
15. What should the public/demo narrative for Kairos focus on: transition alerts, manager scorecards, or alpha attribution?

## 10. Suggested Next Roadmap

### Phase A: Data Truth Hardening

- Manually verify and improve the 14 starter seed rows.
- Add 30-50 more verified transitions.
- Add scheme lineage for renamed/merged schemes.
- Move detector to canonical `manager_tenure` or formalize sync as permanent.

### Phase B: Factor/RFR Reliability

- Test and harden niftyindices downloads.
- Improve manual CSV upload UX for index histories.
- Replace the current FBIL/CCIL heuristic table parsing with stable downloadable series if available.
- Replace the starter RFR fallback CSV with verified monthly history.
- Add factor coverage dashboards.

### Phase C: Analytics Scale

- Use `src/analytics/duckdb_analytics.py` for bulk analytical scans.
- Store rolling alpha results instead of recomputing on each page.
- Implement peer/category normalization.
- Improve DiD with matched peers or synthetic controls.
- Backtest transition cases.

### Phase D: Evidence Review Maturity

- Add claim edit forms with all canonical fields.
- Add claim merge/corroboration grouping.
- Add source deduplication.
- Add evidence provenance cards on manager/scheme pages.

### Phase E: Product Polish

- Improve Setup Wizard into an onboarding flow.
- Add demo dataset mode.
- Add exportable transition report.
- Add portfolio-level narrative reports grounded only in accepted facts.

## 11. Current Best Way to Use Kairos

1. Open Streamlit:

```text
http://localhost:8501
```

2. In Setup Wizard:

- load AMFI scheme master
- load NAV history for target schemes
- load RBI risk-free rate
- load NSE factors
- import starter seed
- run change detector
- run attribution
- compute scorecards
- run DiD diagnostics

3. Review:

- Data Status for quality warnings
- Evidence Review for pending claims
- Scheme Alpha History for attribution readiness
- Manager Profile for manager timeline/scorecard

4. Before using any output seriously:

- verify starter seed rows
- validate factor/RFR sources
- inspect DiD diagnostic warnings
- confirm enough aligned monthly observations exist
monitor.py
seed_data/current_manager_seed.csv
seed_data/scheme_lineage_seed.csv
seed_data/rfr_monthly_fallback.csv
