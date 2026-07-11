from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from src.utils.db import get_connection, read_sql


def validate_manager_tenures(output_csv: str | Path | None = None, head_check: bool = False) -> pd.DataFrame:
    tenures = read_sql(
        """
        SELECT mt.*, mi.canonical_name
        FROM manager_tenure mt
        JOIN manager_identity mi ON mi.manager_id=mt.manager_id
        """
    )
    rows: list[dict] = []
    schemes = read_sql("SELECT scheme_code FROM scheme_master")
    known_schemes = set(schemes["scheme_code"].astype(str).tolist()) if not schemes.empty else set()
    for _, row in tenures.iterrows():
        entity_id = str(row["tenure_id"])
        scheme_code = str(row.get("scheme_code") or "")
        rows.append(_check(entity_id, "scheme_code_resolves", "pass" if not known_schemes or scheme_code in known_schemes else "fail", f"scheme_code={scheme_code}"))
        transition_type = row.get("event_type") or "unknown"
        rows.append(_check(entity_id, "transition_type_present", "pass" if transition_type else "warn", f"event_type={transition_type}"))
        source_url = row.get("source_url")
        if source_url and head_check:
            rows.append(_check(entity_id, "primary_source_accessible", "pass" if _url_ok(source_url) else "warn", source_url, source_url))
        else:
            rows.append(_check(entity_id, "primary_source_present", "pass" if source_url else "warn", source_url or "missing", source_url))
        nav = read_sql("SELECT MIN(nav_date) AS first_nav, MAX(nav_date) AS last_nav, COUNT(*) AS n FROM nav_history WHERE scheme_code=?", (scheme_code,))
        if not nav.empty and nav.iloc[0]["n"]:
            start = pd.to_datetime(row.get("start_date"), errors="coerce")
            first_nav = pd.to_datetime(nav.iloc[0]["first_nav"], errors="coerce")
            rows.append(_check(entity_id, "start_after_inception", "pass" if pd.isna(start) or pd.isna(first_nav) or start >= first_nav else "fail", f"start={start}; first_nav={first_nav}"))
            if row.get("end_date"):
                end = pd.to_datetime(row.get("end_date"), errors="coerce")
                pre = read_sql("SELECT COUNT(*) AS n FROM nav_history WHERE scheme_code=? AND nav_date BETWEEN date(?,'-365 day') AND ?", (scheme_code, row["end_date"], row["end_date"]))
                post = read_sql("SELECT COUNT(*) AS n FROM nav_history WHERE scheme_code=? AND nav_date BETWEEN ? AND date(?,'+365 day')", (scheme_code, row["end_date"], row["end_date"]))
                ok = int(pre.iloc[0]["n"] or 0) > 150 and int(post.iloc[0]["n"] or 0) > 150
                rows.append(_check(entity_id, "nav_12m_each_side", "pass" if ok else "warn", f"pre_days={pre.iloc[0]['n']}; post_days={post.iloc[0]['n']}"))
        else:
            rows.append(_check(entity_id, "nav_history_available", "warn", "No NAV rows for scheme"))
    report = pd.DataFrame(rows)
    with get_connection() as conn:
        conn.execute("DELETE FROM data_quality_reports WHERE report_name='manager_tenure_verification'")
        for _, row in report.iterrows():
            conn.execute(
                """
                INSERT INTO data_quality_reports(report_name,row_type,entity_id,check_name,status,message,source_url)
                VALUES('manager_tenure_verification','manager_tenure',?,?,?,?,?)
                """,
                (row["entity_id"], row["check_name"], row["status"], row["message"], row.get("source_url")),
            )
    if output_csv:
        report.to_csv(output_csv, index=False)
    return report


def validate_factor_csv(file_obj, index_key: str) -> dict:
    df = pd.read_csv(file_obj)
    cols = {c.strip().lower() for c in df.columns}
    required = {"date", "close"}
    missing = sorted(required - cols)
    if missing:
        return {"status": "fail", "index_key": index_key, "message": f"Missing columns: {', '.join(missing)}"}
    date_col = next(c for c in df.columns if c.strip().lower() == "date")
    close_col = next(c for c in df.columns if c.strip().lower() == "close")
    dates = pd.to_datetime(df[date_col], errors="coerce")
    close = pd.to_numeric(df[close_col], errors="coerce")
    missing_pct = float((dates.isna() | close.isna()).mean())
    status = "pass" if missing_pct <= 0.02 and len(df) >= 250 else "warn"
    return {"status": status, "index_key": index_key, "rows": len(df), "missing_pct": missing_pct, "first_date": str(dates.min()), "last_date": str(dates.max())}


def _check(entity_id: str, check_name: str, status: str, message: str, source_url: str | None = None) -> dict:
    return {"entity_id": entity_id, "check_name": check_name, "status": status, "message": message, "source_url": source_url}


def _url_ok(url: str) -> bool:
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        return response.status_code < 400
    except Exception:
        return False
