from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fuzzywuzzy import fuzz

from src.detection.change_detector import manager_key
from src.utils.db import get_connection, read_sql


SEED_DIR = Path(__file__).resolve().parents[2] / "seed_data"
SEED_TEMPLATE_PATH = SEED_DIR / "manager_transition_seed_template.csv"
STARTER_SEED_PATH = SEED_DIR / "starter_manager_transitions.csv"

REQUIRED_SEED_COLUMNS = {
    "scheme_code",
    "scheme_name",
    "amc_name",
    "manager_name",
    "role",
    "rank",
    "start_date",
    "end_date",
    "source",
    "source_type",
    "source_url",
    "confidence_score",
}


def normalize_seed_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip().lower().replace(" ", "_") for c in out.columns]
    missing = REQUIRED_SEED_COLUMNS - set(out.columns)
    if missing:
        raise ValueError(f"Missing required seed columns: {', '.join(sorted(missing))}")
    defaults = {
        "predecessor_manager": None,
        "successor_manager": None,
        "event_type": None,
        "notes": None,
        "evidence_ids": None,
    }
    for col, value in defaults.items():
        if col not in out.columns:
            out[col] = value
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce").fillna(1).astype(int)
    out["confidence_score"] = pd.to_numeric(out["confidence_score"], errors="coerce").fillna(0.5)
    return out


def resolve_manager_identity(manager_name: str, source: str = "import", threshold: int = 94) -> int:
    canonical = " ".join(str(manager_name).split())
    identities = read_sql("SELECT manager_id, canonical_name FROM manager_identity")
    best_id = None
    best_score = 0
    for _, row in identities.iterrows():
        score = fuzz.token_set_ratio(canonical, row["canonical_name"])
        if score > best_score:
            best_score = score
            best_id = int(row["manager_id"])
    with get_connection() as conn:
        if best_id is not None and best_score >= threshold:
            conn.execute(
                "INSERT OR IGNORE INTO manager_alias(manager_id, alias_name, source, confidence_score) VALUES (?, ?, ?, ?)",
                (best_id, canonical, source, best_score / 100),
            )
            return best_id
        cur = conn.execute(
            "INSERT INTO manager_identity(canonical_name) VALUES (?)",
            (canonical,),
        )
        manager_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO manager_alias(manager_id, alias_name, source, confidence_score) VALUES (?, ?, ?, ?)",
            (manager_id, canonical, source, 1.0),
        )
        return manager_id


def import_seed_dataframe(df: pd.DataFrame, verified: bool = True) -> dict:
    seed = normalize_seed_columns(df)
    rows = 0
    for _, row in seed.iterrows():
        manager_id = resolve_manager_identity(row["manager_name"], row["source"])
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO manager_tenure
                (manager_id, scheme_code, scheme_name, amc_name, role, rank, start_date, end_date,
                 confidence_score, evidence_ids, source, source_type, source_url, notes, is_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manager_id,
                    str(row["scheme_code"]),
                    row["scheme_name"],
                    row["amc_name"],
                    row["role"],
                    int(row["rank"]),
                    row["start_date"] if pd.notna(row["start_date"]) else None,
                    row["end_date"] if pd.notna(row["end_date"]) and str(row["end_date"]).strip() else None,
                    float(row["confidence_score"]),
                    row["evidence_ids"] if pd.notna(row["evidence_ids"]) else None,
                    row["source"],
                    row["source_type"],
                    row["source_url"],
                    row["notes"],
                    int(verified),
                ),
            )
            conn.execute(
                """
                UPDATE manager_tenure
                SET event_type=?, predecessor_manager_id=?, successor_manager_id=?
                WHERE tenure_id=last_insert_rowid()
                """,
                (
                    row.get("event_type") if pd.notna(row.get("event_type")) else None,
                    None,
                    None,
                ),
            )
            rows += 1
    sync_canonical_to_legacy()
    return {"seed_rows": rows, "verified": verified}


def import_starter_seed() -> dict:
    return import_seed_dataframe(pd.read_csv(STARTER_SEED_PATH), verified=False)


def sync_canonical_to_legacy() -> dict:
    tenures = read_sql(
        """
        SELECT mt.*, mi.canonical_name
        FROM manager_tenure mt
        JOIN manager_identity mi ON mi.manager_id=mt.manager_id
        """
    )
    inserted = 0
    conflicts = []
    with get_connection() as conn:
        for _, row in tenures.iterrows():
            key = manager_key(row["canonical_name"], row["amc_name"])
            existing = conn.execute(
                """
                SELECT id FROM manager_scheme_history
                WHERE scheme_code=? AND manager_key=? AND COALESCE(start_date,'')=COALESCE(?,'')
                """,
                (str(row["scheme_code"]), key, row["start_date"]),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO manager_scheme_history
                (scheme_code, scheme_name, amc_name, manager_name, manager_key, start_date, end_date,
                 source, confidence_score, is_lead_manager, raw_evidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["scheme_code"]),
                    row["scheme_name"],
                    row["amc_name"],
                    row["canonical_name"],
                    key,
                    row["start_date"],
                    row["end_date"],
                    row["source"],
                    row["confidence_score"],
                    int(row["rank"] == 1),
                    json.dumps(
                        {
                            "tenure_id": int(row["tenure_id"]),
                            "source_type": row["source_type"],
                            "source_url": row["source_url"],
                            "evidence_ids": row["evidence_ids"],
                            "notes": row["notes"],
                        },
                        default=str,
                    ),
                ),
            )
            inserted += 1
    return {"legacy_rows_inserted": inserted, "conflicts": conflicts}


def backfill_canonical_from_legacy() -> dict:
    legacy = read_sql("SELECT * FROM manager_scheme_history")
    rows = 0
    with get_connection() as conn:
        for _, row in legacy.iterrows():
            manager_id = resolve_manager_identity(row["manager_name"], row.get("source") or "legacy")
            exists = conn.execute(
                """
                SELECT tenure_id FROM manager_tenure
                WHERE manager_id=? AND scheme_code=? AND COALESCE(start_date,'')=COALESCE(?,'')
                """,
                (manager_id, str(row["scheme_code"]), row["start_date"]),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO manager_tenure
                (manager_id, scheme_code, scheme_name, amc_name, role, rank, start_date, end_date,
                 confidence_score, source, source_type, notes, is_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manager_id,
                    str(row["scheme_code"]),
                    row["scheme_name"],
                    row["amc_name"],
                    "lead" if row["is_lead_manager"] else "manager",
                    1 if row["is_lead_manager"] else 2,
                    row["start_date"],
                    row["end_date"],
                    row["confidence_score"],
                    row["source"],
                    "legacy_import",
                    row["raw_evidence"],
                    0,
                ),
            )
            rows += 1
    return {"canonical_rows_inserted": rows}
