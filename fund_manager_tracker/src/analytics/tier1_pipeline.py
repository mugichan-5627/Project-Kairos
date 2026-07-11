from __future__ import annotations

from dataclasses import dataclass, asdict

import pandas as pd

from src.analytics.factor_matched_did import FactorMatchedDID
from src.analytics.impact_forecast import TransitionImpactForecaster
from src.analytics.pipeline_runner import run_full_pipeline
from src.data.amfi_loader import AMFILoader
from src.detection.change_detector import ManagerChangeDetector, manager_key
from src.scoring.scorecard import ManagerScorecard
from src.utils.db import get_connection, read_sql


@dataclass(frozen=True)
class Tier1Target:
    manager_name: str
    scheme_code: str
    start_date: str
    end_date: str
    transition_type: str = "resignation"
    source_type: str = "curated_public_record"
    confidence_score: float = 0.90


TIER1_TARGETS: tuple[Tier1Target, ...] = (
    Tier1Target("Prashant Jain", "100119", "1994-01-01", "2022-07-28", "retirement", confidence_score=0.95),
    Tier1Target("Kenneth Andrade", "111862", "2005-09-01", "2015-09-30"),
    Tier1Target("Anoop Bhaskar", "111862", "2016-04-01", "2023-08-31"),
    Tier1Target("Sunil Singhania", "100377", "2003-12-01", "2018-09-28"),
    Tier1Target("Chirag Setalvad", "105758", "2007-07-01", "2023-06-15", "retirement"),
    Tier1Target("Pankaj Tibrewal", "104908", "2010-10-01", "2024-12-31"),
    Tier1Target("Nilesh Shah", "112090", "2009-09-17", "2019-12-31", "internal_transfer"),
    Tier1Target("Sohini Andani", "103504", "2010-07-01", "2023-09-30"),
    Tier1Target("Jinesh Gopani", "112277", "2012-01-01", "2023-03-31"),
)


def _scheme_info(scheme_code: str) -> dict:
    rows = read_sql("SELECT scheme_name, amc_name, category, sub_category FROM scheme_master WHERE scheme_code=?", (scheme_code,))
    return {} if rows.empty else rows.iloc[0].to_dict()


def _ensure_tenure(target: Tier1Target) -> dict:
    info = _scheme_info(target.scheme_code)
    amc_name = info.get("amc_name")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO manager_identity(canonical_name, first_known_date, last_known_date)
            VALUES (?, ?, ?)
            ON CONFLICT(canonical_name) DO UPDATE SET
              first_known_date=COALESCE(manager_identity.first_known_date, excluded.first_known_date),
              last_known_date=COALESCE(excluded.last_known_date, manager_identity.last_known_date),
              updated_at=CURRENT_TIMESTAMP
            """,
            (target.manager_name, target.start_date, target.end_date),
        )
        manager_id = conn.execute(
            "SELECT manager_id FROM manager_identity WHERE canonical_name=?",
            (target.manager_name,),
        ).fetchone()["manager_id"]
        existing = conn.execute(
            """
            SELECT tenure_id FROM manager_tenure
            WHERE manager_id=? AND scheme_code=? AND COALESCE(start_date,'')=? AND COALESCE(end_date,'')=?
            """,
            (manager_id, target.scheme_code, target.start_date, target.end_date),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE manager_tenure
                SET scheme_name=COALESCE(?, scheme_name),
                    amc_name=COALESCE(?, amc_name),
                    role='lead',
                    rank=1,
                    is_verified=1,
                    source_type=?,
                    event_type='manager_exit',
                    transition_type=?,
                    analytics_status=COALESCE(analytics_status, 'pending'),
                    confidence_score=MAX(confidence_score, ?),
                    updated_at=CURRENT_TIMESTAMP
                WHERE tenure_id=?
                """,
                (
                    info.get("scheme_name"),
                    amc_name,
                    target.source_type,
                    target.transition_type,
                    target.confidence_score,
                    existing["tenure_id"],
                ),
            )
            tenure_id = existing["tenure_id"]
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO manager_tenure
                (manager_id, scheme_code, scheme_name, amc_name, role, rank, start_date, end_date,
                 confidence_score, source_type, notes, event_type, transition_type, analytics_status, is_verified)
                VALUES (?, ?, ?, ?, 'lead', 1, ?, ?, ?, ?, ?, 'manager_exit', ?, 'pending', 1)
                """,
                (
                    manager_id,
                    target.scheme_code,
                    info.get("scheme_name"),
                    amc_name,
                    target.start_date,
                    target.end_date,
                    target.confidence_score,
                    target.source_type,
                    "Tier 1 curated transition seed; source review recommended before production use.",
                    target.transition_type,
                ),
            )
            tenure_id = int(cur.lastrowid)
            action = "inserted"
    return {"manager_id": manager_id, "tenure_id": tenure_id, "action": action, "scheme_info": info}


def _ensure_change_event(target: Tier1Target) -> int:
    info = _scheme_info(target.scheme_code)
    ManagerChangeDetector().refresh_change_events()
    key = manager_key(target.manager_name, info.get("amc_name"))
    existing = read_sql(
        """
        SELECT event_id FROM change_events
        WHERE scheme_code=? AND manager_name=? AND change_date=?
        ORDER BY event_id DESC LIMIT 1
        """,
        (target.scheme_code, target.manager_name, target.end_date),
    )
    if not existing.empty:
        return int(existing.iloc[0]["event_id"])
    start = pd.to_datetime(target.start_date, errors="coerce")
    end = pd.to_datetime(target.end_date, errors="coerce")
    tenure_months = float((end - start).days / 30.44) if pd.notna(start) and pd.notna(end) else None
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO change_events
            (scheme_code, manager_name, manager_key, change_type, change_date, pre_tenure_months,
             predecessor_manager, successor_manager, amc_name, category, confidence_score, status)
            VALUES (?, ?, ?, 'Full Exit', ?, ?, ?, NULL, ?, ?, ?, 'confirmed')
            """,
            (
                target.scheme_code,
                target.manager_name,
                key,
                target.end_date,
                tenure_months,
                target.manager_name,
                info.get("amc_name"),
                info.get("sub_category") or info.get("category"),
                target.confidence_score,
            ),
        )
        return int(cur.lastrowid)


def _nav_status(scheme_code: str) -> dict:
    rows = read_sql(
        "SELECT COUNT(*) AS rows, MIN(nav_date) AS first_date, MAX(nav_date) AS last_date FROM nav_history WHERE scheme_code=?",
        (scheme_code,),
    )
    return rows.iloc[0].to_dict() if not rows.empty else {"rows": 0, "first_date": None, "last_date": None}


def run_tier1_pipeline(load_missing_nav: bool = True, rerun_existing: bool = True) -> dict:
    loader = AMFILoader()
    target_results = []
    for target in TIER1_TARGETS:
        seeded = _ensure_tenure(target)
        nav_before = _nav_status(target.scheme_code)
        if load_missing_nav and int(nav_before.get("rows") or 0) < 50:
            try:
                loader.refresh_nav_history([target.scheme_code])
            except Exception as exc:
                target_results.append({**asdict(target), "status": "nav_load_failed", "error": str(exc), "seeded": seeded})
                continue
        nav = _nav_status(target.scheme_code)
        if int(nav.get("rows") or 0) < 50:
            target_results.append({**asdict(target), "status": "insufficient_nav_data", "nav": nav, "seeded": seeded})
            continue
        event_id = _ensure_change_event(target)
        existing_ok = read_sql(
            """
            SELECT COUNT(*) AS ok_rows
            FROM attribution_results
            WHERE event_id=? AND window_type='pre' AND model_status='ok'
            """,
            (event_id,),
        )
        should_run = rerun_existing or existing_ok.empty or int(existing_ok.iloc[0]["ok_rows"]) == 0
        pipeline_result = {"status": "skipped_existing"}
        if should_run:
            pipeline_result = run_full_pipeline(event_id)
        target_results.append(
            {
                **asdict(target),
                "status": pipeline_result.get("status", "unknown"),
                "event_id": event_id,
                "nav": nav,
                "seeded": seeded,
                "pipeline": pipeline_result,
            }
        )
    ManagerScorecard().refresh_all()
    return {"targets": target_results, "summary": tier1_summary()}


def tier1_summary() -> list[dict]:
    rows = read_sql(
        """
        SELECT
          ce.event_id,
          ce.manager_name,
          ce.scheme_code,
          sm.scheme_name,
          ce.change_date,
          ar.alpha_annualized,
          ar.alpha_tstat,
          ar.ir_practitioner,
          ar.ir_classification,
          ar.observations,
          ar.model_status,
          ar.value_factor_label,
          COALESCE(ras.windows, 0) AS rolling_windows
        FROM change_events ce
        JOIN attribution_results ar ON ar.event_id=ce.event_id AND ar.window_type='pre'
        LEFT JOIN scheme_master sm ON sm.scheme_code=ce.scheme_code
        LEFT JOIN (
          SELECT event_id, COUNT(*) AS windows
          FROM rolling_alpha_series
          GROUP BY event_id
        ) ras ON ras.event_id=ce.event_id
        WHERE ce.manager_name IN ({})
        ORDER BY ar.alpha_annualized DESC
        """.format(",".join(["?"] * len({t.manager_name for t in TIER1_TARGETS}))),
        tuple(sorted({t.manager_name for t in TIER1_TARGETS})),
    )
    out = []
    for row in rows.to_dict("records"):
        alpha = row.get("alpha_annualized")
        ir = row.get("ir_practitioner")
        flags = []
        if row.get("model_status") != "ok":
            flags.append(f"model_status={row.get('model_status')}")
        if alpha is None or pd.isna(alpha):
            flags.append("missing_alpha")
        elif alpha < -0.05 or alpha > 0.10:
            flags.append("alpha_outside_plausibility_band")
        if ir is not None and not pd.isna(ir) and (ir < -1.0 or ir > 2.0):
            flags.append("ir_outside_plausibility_band")
        if int(row.get("observations") or 0) < 12:
            flags.append("insufficient_observations")
        row["alpha_pct"] = None if alpha is None or pd.isna(alpha) else round(float(alpha) * 100, 2)
        row["alpha_tstat"] = None if row.get("alpha_tstat") is None or pd.isna(row.get("alpha_tstat")) else round(float(row["alpha_tstat"]), 2)
        row["ir_practitioner"] = None if ir is None or pd.isna(ir) else round(float(ir), 3)
        row["quality_flags"] = flags
        out.append(row)
    return out


def tier1_audit() -> dict:
    return {
        "targets": [asdict(t) for t in TIER1_TARGETS],
        "summary": tier1_summary(),
        "pending_seeded_tenures": read_sql(
            """
            SELECT mt.manager_id, mt.scheme_code, mt.end_date, mi.canonical_name
            FROM manager_tenure mt
            JOIN manager_identity mi ON mi.manager_id=mt.manager_id
            WHERE mt.end_date IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM change_events ce
                WHERE ce.scheme_code=mt.scheme_code
                  AND ce.manager_name=mi.canonical_name
              )
            ORDER BY mi.canonical_name
            """
        ).to_dict("records"),
    }
