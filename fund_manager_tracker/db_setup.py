from __future__ import annotations

import sqlite3

from src.config import DB_PATH, ensure_dirs


SCHEMA = """
CREATE TABLE IF NOT EXISTS request_cache (
    cache_key TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    response_text TEXT,
    response_blob BLOB,
    status_code INTEGER,
    content_type TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS scheme_master (
    scheme_code TEXT PRIMARY KEY,
    isin_growth TEXT,
    isin_div_reinvestment TEXT,
    scheme_name TEXT NOT NULL,
    amc_name TEXT,
    category TEXT,
    sub_category TEXT,
    scheme_type TEXT,
    nav_name TEXT,
    benchmark TEXT,
    status TEXT DEFAULT 'active',
    source TEXT,
    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheme_lineage (
    old_scheme_code TEXT,
    new_scheme_code TEXT,
    event_date TEXT,
    event_type TEXT,
    notes TEXT,
    PRIMARY KEY (old_scheme_code, new_scheme_code, event_date)
);

CREATE TABLE IF NOT EXISTS nav_history (
    scheme_code TEXT NOT NULL,
    nav_date TEXT NOT NULL,
    nav REAL,
    repurchase_price REAL,
    sale_price REAL,
    source TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scheme_code, nav_date)
);

CREATE TABLE IF NOT EXISTS factor_data (
    factor_date TEXT PRIMARY KEY,
    nifty500_return REAL,
    nifty50_return REAL,
    smallcap250_return REAL,
    value50_return REAL,
    momentum50_return REAL,
    quality_lowvol30_return REAL,
    midcap150_return REAL,
    india_vix REAL,
    repo_rate REAL,
    rfr_monthly REAL,
    rfr_source TEXT,
    rfr_is_fallback INTEGER DEFAULT 0,
    risk_free_monthly REAL,
    mkt_rf REAL,
    smb REAL,
    hml REAL,
    wml REAL,
    qmj REAL,
    source TEXT,
    factor_source TEXT,
    factor_is_fallback INTEGER DEFAULT 0,
    dropped_observations INTEGER DEFAULT 0,
    last_updated TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manager_scheme_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    scheme_name TEXT,
    amc_name TEXT,
    manager_name TEXT,
    manager_key TEXT,
    start_date TEXT,
    end_date TEXT,
    source TEXT,
    confidence_score REAL,
    is_lead_manager INTEGER DEFAULT 0,
    raw_evidence TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_msh_scheme ON manager_scheme_history(scheme_code);
CREATE INDEX IF NOT EXISTS idx_msh_manager ON manager_scheme_history(manager_key);

CREATE TABLE IF NOT EXISTS manager_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    manager_name TEXT,
    manager_key TEXT,
    change_date TEXT,
    source TEXT,
    confidence_score REAL,
    evidence_url TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS change_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    manager_name TEXT,
    manager_key TEXT,
    change_type TEXT,
    change_date TEXT,
    pre_tenure_months REAL,
    predecessor_manager TEXT,
    successor_manager TEXT,
    amc_name TEXT,
    category TEXT,
    confidence_score REAL,
    status TEXT DEFAULT 'confirmed',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attribution_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    event_id INTEGER,
    manager_key TEXT,
    window_type TEXT,
    start_date TEXT,
    end_date TEXT,
    alpha_annualized REAL,
    alpha_tstat REAL,
    adj_r2 REAL,
    beta_mkt REAL,
    beta_smb REAL,
    beta_hml REAL,
    beta_wml REAL,
    beta_qmj REAL,
    beta_mkt_t REAL,
    beta_smb_t REAL,
    beta_hml_t REAL,
    beta_wml_t REAL,
    beta_qmj_t REAL,
    idiosyncratic_vol REAL,
    observations INTEGER,
    model_status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS peer_attribution_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    scheme_code TEXT,
    category_median_alpha REAL,
    house_alpha REAL,
    manager_alpha REAL,
    manager_alpha_net REAL,
    pre_excess_alpha REAL,
    post_excess_alpha REAL,
    did_alpha REAL,
    ci_low REAL,
    ci_high REAL,
    statistically_significant INTEGER,
    peer_count INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    manager_key TEXT,
    start_date TEXT,
    end_date TEXT,
    metrics_json TEXT,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manager_scorecards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_key TEXT,
    manager_name TEXT,
    scheme_code TEXT,
    event_id INTEGER,
    composite_score REAL,
    label TEXT,
    alpha_score REAL,
    consistency_score REAL,
    risk_score REAL,
    factor_efficiency_score REAL,
    tenure_score REAL,
    investor_risk_score REAL,
    alert_text TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_status (
    source_name TEXT PRIMARY KEY,
    last_success TEXT,
    last_attempt TEXT,
    status TEXT,
    rows_loaded INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS failed_scrapes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT,
    url TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    source_type TEXT,
    source_url TEXT,
    query TEXT,
    title TEXT,
    snippet TEXT,
    raw_json TEXT,
    observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    relevance_score REAL,
    extraction_status TEXT DEFAULT 'raw'
);

CREATE INDEX IF NOT EXISTS idx_source_evidence_query ON source_evidence(query);
CREATE INDEX IF NOT EXISTS idx_source_evidence_url ON source_evidence(source_url);

CREATE TABLE IF NOT EXISTS manager_claims (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id INTEGER,
    claim_type TEXT NOT NULL,
    manager_name TEXT,
    manager_key TEXT,
    scheme_code TEXT,
    scheme_name TEXT,
    amc_name TEXT,
    event_date TEXT,
    claim_text TEXT,
    confidence_score REAL DEFAULT 0.5,
    system_confidence REAL,
    llm_confidence REAL,
    source_type TEXT,
    parsed_json TEXT,
    parse_status TEXT,
    error_message TEXT,
    llm_verdict TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    FOREIGN KEY(evidence_id) REFERENCES source_evidence(evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_claims_status ON manager_claims(status);
CREATE INDEX IF NOT EXISTS idx_manager_claims_manager ON manager_claims(manager_key);

CREATE TABLE IF NOT EXISTS manager_identity_map (
    identity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_manager_key TEXT NOT NULL,
    canonical_manager_name TEXT NOT NULL,
    amc_name TEXT,
    alias_name TEXT NOT NULL,
    source TEXT,
    confidence_score REAL DEFAULT 0.8,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(canonical_manager_key, alias_name, amc_name)
);

CREATE TABLE IF NOT EXISTS scheme_manager_tenures (
    tenure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    scheme_name TEXT,
    amc_name TEXT,
    manager_name TEXT,
    manager_key TEXT,
    start_date TEXT,
    end_date TEXT,
    role TEXT DEFAULT 'manager',
    is_lead_manager INTEGER DEFAULT 0,
    evidence_count INTEGER DEFAULT 0,
    confidence_score REAL DEFAULT 0.5,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scheme_manager_tenures_scheme ON scheme_manager_tenures(scheme_code);
CREATE INDEX IF NOT EXISTS idx_scheme_manager_tenures_manager ON scheme_manager_tenures(manager_key);

CREATE TABLE IF NOT EXISTS llm_audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    model TEXT,
    prompt_hash TEXT,
    input_summary TEXT,
    output_text TEXT,
    parsed_json TEXT,
    parse_status TEXT,
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    source_evidence_ids TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manager_identity (
    manager_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    first_known_date TEXT,
    last_known_date TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS manager_alias (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER NOT NULL,
    alias_name TEXT NOT NULL,
    source TEXT,
    confidence_score REAL DEFAULT 0.8,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(manager_id, alias_name, source),
    FOREIGN KEY(manager_id) REFERENCES manager_identity(manager_id)
);

CREATE TABLE IF NOT EXISTS manager_tenure (
    tenure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER NOT NULL,
    scheme_code TEXT,
    scheme_name TEXT,
    amc_name TEXT,
    role TEXT DEFAULT 'manager',
    rank INTEGER DEFAULT 1,
    start_date TEXT,
    end_date TEXT,
    confidence_score REAL DEFAULT 0.5,
    evidence_ids TEXT,
    source TEXT,
    source_type TEXT,
    source_url TEXT,
    notes TEXT,
    event_type TEXT,
    transition_type TEXT,
    analytics_status TEXT,
    predecessor_manager_id INTEGER,
    successor_manager_id INTEGER,
    is_verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(manager_id) REFERENCES manager_identity(manager_id)
);

CREATE INDEX IF NOT EXISTS idx_manager_tenure_scheme ON manager_tenure(scheme_code);
CREATE INDEX IF NOT EXISTS idx_manager_tenure_manager ON manager_tenure(manager_id);

CREATE TABLE IF NOT EXISTS data_quality_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name TEXT,
    status TEXT,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS did_diagnostics (
    diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    scheme_code TEXT,
    fund_trend_slope REAL,
    category_trend_slope REAL,
    slope_difference REAL,
    diagnostic_label TEXT,
    message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_name TEXT,
    row_type TEXT,
    entity_id TEXT,
    check_name TEXT,
    status TEXT,
    message TEXT,
    source_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portable_alpha_scores (
    pas_id INTEGER PRIMARY KEY AUTOINCREMENT,
    manager_id INTEGER,
    manager_name TEXT,
    portable_alpha REAL,
    peer_adjusted_alpha REAL,
    tenure_weighted_alpha REAL,
    tenure_months REAL,
    confidence_weight REAL,
    tenure_count INTEGER,
    regime_adjustment REAL,
    aum_adjustment REAL,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS factor_matched_did (
    did_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    scheme_code TEXT,
    peer_scheme_codes TEXT,
    pre_alpha_fund REAL,
    post_alpha_fund REAL,
    pre_alpha_peer REAL,
    post_alpha_peer REAL,
    did_alpha REAL,
    peer_count INTEGER,
    parallel_trends_label TEXT,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transition_impact_forecasts (
    forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    scheme_code TEXT,
    departing_manager TEXT,
    incoming_manager TEXT,
    expected_alpha_change REAL,
    nav_impact_12m_p10 REAL,
    nav_impact_12m_p50 REAL,
    nav_impact_12m_p90 REAL,
    nav_impact_24m_p10 REAL,
    nav_impact_24m_p50 REAL,
    nav_impact_24m_p90 REAL,
    recommendation TEXT,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    status TEXT,
    summary_json TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS alert_log (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    recipient TEXT,
    channel TEXT,
    severity TEXT,
    subject TEXT,
    body TEXT,
    report_path TEXT,
    delivery_status TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS current_manager_snapshot (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_code TEXT,
    scheme_name TEXT,
    amc_name TEXT,
    manager_name TEXT,
    manager_key TEXT,
    role TEXT DEFAULT 'manager',
    rank INTEGER DEFAULT 1,
    confirmed_date TEXT,
    source TEXT,
    source_url TEXT,
    confidence_score REAL DEFAULT 0.5,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scheme_code, manager_key, confirmed_date)
);

CREATE TABLE IF NOT EXISTS investor_portfolios (
    portfolio_id INTEGER PRIMARY KEY AUTOINCREMENT,
    investor_email TEXT NOT NULL,
    whatsapp_number TEXT,
    scheme_code TEXT NOT NULL,
    scheme_name TEXT,
    invested_amount REAL,
    units_held REAL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_investor_portfolios_email ON investor_portfolios(investor_email);
CREATE INDEX IF NOT EXISTS idx_investor_portfolios_scheme ON investor_portfolios(scheme_code);
CREATE INDEX IF NOT EXISTS idx_manager_scorecards_scheme ON manager_scorecards(scheme_code);
CREATE INDEX IF NOT EXISTS idx_manager_scorecards_manager ON manager_scorecards(manager_key);

CREATE TABLE IF NOT EXISTS agent_heartbeat (
    agent_name TEXT PRIMARY KEY,
    last_run TEXT,
    status TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS rolling_alpha_series (
    roll_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          INTEGER REFERENCES change_events(event_id),
    manager_id        INTEGER,
    scheme_code       TEXT NOT NULL,
    window_end_date   TEXT NOT NULL,
    window_start_date TEXT NOT NULL,
    window_months     INTEGER DEFAULT 36,
    alpha_monthly     REAL,
    alpha_annualised  REAL,
    alpha_tstat       REAL,
    alpha_pval        REAL,
    adj_r2            REAL,
    observations      INTEGER,
    beta_mkt          REAL,
    beta_smb          REAL,
    beta_hml          REAL,
    beta_wml          REAL,
    computed_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rolling_alpha_scheme
    ON rolling_alpha_series(scheme_code, window_end_date);

-- Manager style & qualitative assessment layer.
-- derived_json holds the quantitative style tilts (factor loadings) so the
-- narrative can always be traced back to the regression that produced it.
CREATE TABLE IF NOT EXISTS manager_qualitative (
    manager_id            INTEGER PRIMARY KEY,
    canonical_name        TEXT,
    style_label           TEXT,
    aggression            TEXT,
    style_summary         TEXT,
    investment_approach   TEXT,
    transition_note       TEXT,
    curated               INTEGER DEFAULT 0,
    sources_json          TEXT,
    derived_json          TEXT,
    updated_at            TEXT DEFAULT (datetime('now'))
);
"""


def initialize_database(db_path=DB_PATH) -> None:
    ensure_dirs()
    with sqlite3.connect(db_path, timeout=60.0, check_same_thread=False) as conn:
        conn.executescript(SCHEMA)
        _apply_lightweight_migrations(conn)
        conn.commit()


def _apply_lightweight_migrations(conn: sqlite3.Connection) -> None:
    migrations = {
        "factor_data": [
            ("rfr_monthly", "REAL"),
            ("rfr_source", "TEXT"),
            ("rfr_is_fallback", "INTEGER DEFAULT 0"),
            ("factor_source", "TEXT"),
            ("factor_is_fallback", "INTEGER DEFAULT 0"),
            ("dropped_observations", "INTEGER DEFAULT 0"),
        ],
        "manager_claims": [
            ("system_confidence", "REAL"),
            ("llm_confidence", "REAL"),
            ("source_type", "TEXT"),
            ("parsed_json", "TEXT"),
            ("parse_status", "TEXT"),
            ("error_message", "TEXT"),
        ],
        "llm_audit_log": [
            ("parsed_json", "TEXT"),
            ("parse_status", "TEXT"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("error_message", "TEXT"),
        ],
        "scheme_manager_tenures": [
            ("rank", "INTEGER DEFAULT 1"),
            ("source_type", "TEXT"),
            ("source_url", "TEXT"),
            ("evidence_ids", "TEXT"),
        ],
        "manager_tenure": [
            ("event_type", "TEXT"),
            ("predecessor_manager_id", "INTEGER"),
            ("successor_manager_id", "INTEGER"),
            ("announced_date", "TEXT"),
            ("effective_date", "TEXT"),
            ("analytics_status", "TEXT"),
            ("transition_type", "TEXT"),
        ],
        "attribution_results": [
            ("value_factor_label", "TEXT"),
            ("ir_appraisal", "REAL"),
            ("ir_practitioner", "REAL"),
            ("ir_classification", "TEXT"),
        ],
        "transition_impact_forecasts": [
            ("uncertainty_flag", "TEXT"),
        ],
        "manager_scorecards": [
            ("score_method", "TEXT"),
            ("score_warning", "TEXT"),
            ("peer_count", "INTEGER"),
        ],
        "source_evidence": [
            ("content_hash", "TEXT"),
            ("severity", "TEXT"),
        ],
        "alert_log": [
            ("investor_email", "TEXT"),
            ("scheme_code", "TEXT"),
            ("manager_id", "TEXT"),
            ("alert_type", "TEXT"),
            ("sent_at", "TEXT"),
            ("error_message", "TEXT"),
        ],
    }
    for table, columns in migrations.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for column, definition in columns:
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    _dedupe_exact_manager_tenures(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_manager_tenure_identity_scheme_dates
        ON manager_tenure(
            manager_id,
            COALESCE(scheme_code, ''),
            COALESCE(role, ''),
            COALESCE(start_date, ''),
            COALESCE(end_date, '')
        )
        """
    )


def _dedupe_exact_manager_tenures(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM manager_tenure
        WHERE tenure_id NOT IN (
            SELECT MIN(tenure_id)
            FROM manager_tenure
            GROUP BY manager_id, COALESCE(scheme_code, ''), COALESCE(role, ''),
                     COALESCE(start_date, ''), COALESCE(end_date, '')
        )
        """
    )


if __name__ == "__main__":
    initialize_database()
    print(f"Initialized Project Kairos database at {DB_PATH}")
