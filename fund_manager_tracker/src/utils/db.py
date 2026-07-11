from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from src.config import DB_PATH, ensure_dirs


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def read_sql(query: str, params: tuple | dict | None = None, db_path: Path = DB_PATH) -> pd.DataFrame:
    with get_connection(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def upsert_dataframe(df: pd.DataFrame, table: str, conn: sqlite3.Connection, if_exists: str = "append") -> int:
    if df.empty:
        return 0
    df.to_sql(table, conn, if_exists=if_exists, index=False)
    return len(df)
