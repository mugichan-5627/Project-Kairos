from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.utils.db import get_connection, read_sql


def manager_key(manager_name: str, amc_name: str | None) -> str:
    return f"{' '.join(str(manager_name).split())} | {' '.join(str(amc_name or 'Unknown AMC').split())}"


class ManagerChangeDetector:
    confidence_threshold = 0.7

    def normalize_history(self, history: pd.DataFrame) -> pd.DataFrame:
        if history.empty:
            return history
        df = history.copy()
        df["manager_key"] = [manager_key(m, a) for m, a in zip(df["manager_name"], df["amc_name"])]
        df["confidence_score"] = pd.to_numeric(df.get("confidence_score", 0.5), errors="coerce").fillna(0.5)
        df["start_date"] = pd.to_datetime(df.get("start_date"), errors="coerce")
        df["end_date"] = pd.to_datetime(df.get("end_date"), errors="coerce")
        return df

    def persist_history(self, history: pd.DataFrame) -> int:
        df = self.normalize_history(history)
        if df.empty:
            return 0
        with get_connection() as conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT INTO manager_scheme_history
                    (scheme_code, scheme_name, amc_name, manager_name, manager_key, start_date, end_date,
                     source, confidence_score, is_lead_manager, raw_evidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("scheme_code"),
                        row.get("scheme_name"),
                        row.get("amc_name"),
                        row.get("manager_name"),
                        row.get("manager_key"),
                        row.get("start_date").strftime("%Y-%m-%d") if pd.notna(row.get("start_date")) else None,
                        row.get("end_date").strftime("%Y-%m-%d") if pd.notna(row.get("end_date")) else None,
                        row.get("source"),
                        float(row.get("confidence_score")),
                        int(row.get("is_lead_manager", 0) or 0),
                        row.get("raw_evidence"),
                    ),
                )
        return len(df)

    def detect_from_history(self) -> pd.DataFrame:
        canonical = read_sql(
            """
            SELECT
                mt.tenure_id,
                mt.scheme_code,
                mt.scheme_name,
                mt.amc_name,
                mi.canonical_name AS manager_name,
                mt.start_date,
                mt.end_date,
                mt.role,
                mt.rank,
                mt.event_type,
                mt.confidence_score,
                mt.source,
                mt.created_at,
                sm.category,
                sm.sub_category
            FROM manager_tenure mt
            JOIN manager_identity mi ON mi.manager_id=mt.manager_id
            LEFT JOIN scheme_master sm ON sm.scheme_code=mt.scheme_code
            WHERE mt.confidence_score>=?
            ORDER BY mt.scheme_code, COALESCE(mt.start_date, mt.end_date), mt.rank
            """,
            (self.confidence_threshold,),
        )
        if not canonical.empty:
            canonical["manager_key"] = [manager_key(m, a) for m, a in zip(canonical["manager_name"], canonical["amc_name"])]
            return self._detect_from_canonical(canonical)
        history = read_sql(
            """
            SELECT h.*, sm.category, sm.sub_category
            FROM manager_scheme_history h
            LEFT JOIN scheme_master sm ON sm.scheme_code=h.scheme_code
            WHERE h.confidence_score>=?
            ORDER BY h.scheme_code, h.start_date
            """,
            (self.confidence_threshold,),
        )
        if history.empty:
            return pd.DataFrame()
        events = []
        for scheme_code, group in history.groupby("scheme_code"):
            group = group.sort_values(["start_date", "created_at"])
            prior_managers: set[str] = set()
            prior_rows = []
            for _, row in group.iterrows():
                current_key = row["manager_key"]
                start = pd.to_datetime(row["start_date"], errors="coerce")
                if not prior_managers:
                    prior_managers.add(current_key)
                    prior_rows.append(row)
                    continue
                if current_key not in prior_managers and pd.notna(start):
                    predecessor = prior_rows[-1]
                    pre_start = pd.to_datetime(predecessor["start_date"], errors="coerce")
                    tenure = ((start - pre_start).days / 30.44) if pd.notna(pre_start) else None
                    events.append(
                        {
                            "scheme_code": scheme_code,
                            "manager_name": predecessor["manager_name"],
                            "manager_key": predecessor["manager_key"],
                            "change_date": start.strftime("%Y-%m-%d"),
                            "pre_tenure_months": tenure,
                            "predecessor_manager": predecessor["manager_name"],
                            "successor_manager": row["manager_name"],
                            "amc_name": row["amc_name"],
                            "category": row.get("sub_category") or row.get("category"),
                            "confidence_score": min(1.0, max(float(row["confidence_score"]), float(predecessor["confidence_score"]))),
                        }
                    )
                prior_managers.add(current_key)
                prior_rows.append(row)
        return pd.DataFrame(events)

    def _detect_from_canonical(self, history: pd.DataFrame) -> pd.DataFrame:
        events = []
        for scheme_code, group in history.groupby("scheme_code"):
            group = group.copy()
            group["start_dt"] = pd.to_datetime(group["start_date"], errors="coerce")
            group["end_dt"] = pd.to_datetime(group["end_date"], errors="coerce")
            group = group.sort_values(["start_dt", "end_dt", "rank"], na_position="last")
            for _, row in group.iterrows():
                if pd.isna(row["end_dt"]):
                    continue
                successor_candidates = group[
                    (group["manager_key"] != row["manager_key"])
                    & (group["start_dt"].notna())
                    & (group["start_dt"] >= row["end_dt"] - pd.Timedelta(days=31))
                ].sort_values(["start_dt", "rank"])
                successor = successor_candidates.iloc[0] if not successor_candidates.empty else None
                pre_start = row["start_dt"]
                tenure = ((row["end_dt"] - pre_start).days / 30.44) if pd.notna(pre_start) else None
                change_type = row.get("event_type")
                events.append(
                    {
                        "scheme_code": scheme_code,
                        "manager_name": row["manager_name"],
                        "manager_key": row["manager_key"],
                        "change_date": row["end_dt"].strftime("%Y-%m-%d"),
                        "pre_tenure_months": tenure,
                        "predecessor_manager": row["manager_name"],
                        "successor_manager": None if successor is None else successor["manager_name"],
                        "amc_name": row["amc_name"],
                        "category": row.get("sub_category") or row.get("category"),
                        "confidence_score": float(row["confidence_score"]),
                        "canonical_event_type": change_type,
                    }
                )
        return pd.DataFrame(events)

    def classify_change(self, event: pd.Series) -> str:
        if str(event.get("canonical_event_type") or "").lower() in {"full exit", "manager_exit"}:
            return "Full Exit"
        if str(event.get("canonical_event_type") or "").lower() == "amc_switch":
            return "AMC Switch"
        history = read_sql(
            """
            SELECT DISTINCT mt.scheme_code, mt.amc_name
            FROM manager_tenure mt
            JOIN manager_identity mi ON mi.manager_id=mt.manager_id
            WHERE mi.canonical_name || ' | ' || COALESCE(mt.amc_name,'Unknown AMC')=?
            """,
            (event["manager_key"],),
        )
        if history.empty:
            history = read_sql("SELECT DISTINCT scheme_code, amc_name FROM manager_scheme_history WHERE manager_key=?", (event["manager_key"],))
        same_amc_other = history[(history["amc_name"] == event["amc_name"]) & (history["scheme_code"] != event["scheme_code"])]
        other_amc = history[history["amc_name"] != event["amc_name"]]
        if not other_amc.empty:
            return "AMC Switch"
        if not same_amc_other.empty:
            return "Partial Exit"
        return "Full Exit"

    def refresh_change_events(self) -> int:
        events = self.detect_from_history()
        if events.empty:
            return 0
        with get_connection() as conn:
            count = 0
            for _, event in events.iterrows():
                change_type = self.classify_change(event)
                exists = conn.execute(
                    "SELECT 1 FROM change_events WHERE scheme_code=? AND manager_key=? AND change_date=?",
                    (event["scheme_code"], event["manager_key"], event["change_date"]),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO change_events
                    (scheme_code, manager_name, manager_key, change_type, change_date, pre_tenure_months,
                     predecessor_manager, successor_manager, amc_name, category, confidence_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["scheme_code"],
                        event["manager_name"],
                        event["manager_key"],
                        change_type,
                        event["change_date"],
                        event["pre_tenure_months"],
                        event["predecessor_manager"],
                        event["successor_manager"],
                        event["amc_name"],
                        event["category"],
                        event["confidence_score"],
                    ),
                )
                count += 1
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded) VALUES('change_detector',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'ok',?)",
                (count,),
            )
        return count

    @staticmethod
    def inferred_current_date() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")
