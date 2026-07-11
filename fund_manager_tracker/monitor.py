from __future__ import annotations

from plyer import notification

from db_setup import initialize_database
from src.analytics.factor_matched_did import FactorMatchedDID
from src.analytics.impact_forecast import TransitionImpactForecaster
from src.alerts.investor_alerts import run_investor_alert_scan
from src.config import MAJOR_AMCS
from src.data.news_monitor import NewsMonitor
from src.intelligence.update_manager_database import auto_classify_raw_evidence, search_manager_transition_evidence
from src.utils.db import read_sql
from src.utils.db import get_connection


def update_heartbeat(agent_name: str, status: str, error: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_heartbeat(agent_name,last_run,status,error)
            VALUES(?, CURRENT_TIMESTAMP, ?, ?)
            ON CONFLICT(agent_name) DO UPDATE SET
              last_run=CURRENT_TIMESTAMP,
              status=excluded.status,
              error=excluded.error
            """,
            (agent_name, status, error),
        )


def safe_job_wrapper(job_fn, job_name: str) -> dict:
    try:
        update_heartbeat(job_name, "running", None)
        result = job_fn()
        update_heartbeat(job_name, "ok", None)
        return {"status": "ok", "job": job_name, "result": result}
    except Exception as exc:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO data_quality_log(check_name, status, details)
                VALUES(?, 'scheduler_crash', ?)
                """,
                (job_name, str(exc)[:2000]),
            )
        update_heartbeat(job_name, "crashed", str(exc)[:2000])
        return {"status": "crashed", "job": job_name, "error": str(exc)}


def run_daily_monitor() -> dict:
    initialize_database()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO agent_runs(agent_name,status,summary_json) VALUES('daily_monitor','running','{}')"
        )
        run_id = int(cur.lastrowid)
    amcs = read_sql("SELECT DISTINCT amc_name FROM scheme_master WHERE amc_name IS NOT NULL")
    amc_names = amcs["amc_name"].dropna().head(30).tolist() if not amcs.empty else MAJOR_AMCS
    news_rows = NewsMonitor().refresh(amc_names)
    classified = auto_classify_raw_evidence(limit=200)
    pending = read_sql(
        "SELECT COUNT(*) AS n FROM manager_claims WHERE status='pending' AND claim_type IN ('manager_exit','amc_switch')"
    )
    pending_count = int(pending.iloc[0]["n"]) if not pending.empty else 0
    tavily = {"skipped": True}
    if pending_count:
        tavily = search_manager_transition_evidence(amc_names[:10], max_results=5)
        classified += auto_classify_raw_evidence(limit=200)
    investor_alerts = run_investor_alert_scan(days=7)
    if pending_count:
        try:
            notification.notify(
                title="Project Kairos",
                message=f"{pending_count} pending manager transition claims need review.",
                timeout=8,
            )
        except Exception:
            pass
    summary = {
        "news_rows": news_rows,
        "classified": classified,
        "pending_transition_claims": pending_count,
        "tavily": tavily,
        "investor_alerts": investor_alerts,
    }
    with get_connection() as conn:
        conn.execute(
            "UPDATE agent_runs SET completed_at=CURRENT_TIMESTAMP,status='ok',summary_json=? WHERE run_id=?",
            (str(summary), run_id),
        )
    return summary


def run_analytics_agent() -> dict:
    initialize_database()
    did_count = FactorMatchedDID().refresh_all()
    forecast_count = TransitionImpactForecaster().refresh_all()
    return {"factor_matched_did": did_count, "forecasts": forecast_count}


if __name__ == "__main__":
    print(run_daily_monitor())
