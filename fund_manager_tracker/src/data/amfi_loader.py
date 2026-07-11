from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

import pandas as pd

from src.utils.db import get_connection
from src.utils.rate_limiter import RateLimiter


MFAPI_BASE = "https://api.mfapi.in"
AMFI_NAV_ALL = "https://www.amfiindia.com/spages/NAVAll.txt"


class AMFILoader:
    def __init__(self, limiter: RateLimiter | None = None) -> None:
        self.limiter = limiter or RateLimiter()

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _cached_text(self, url: str, ttl_hours: int = 24) -> str | None:
        key = self._cache_key(url)
        cutoff = (datetime.utcnow() - timedelta(hours=ttl_hours)).isoformat()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT response_text FROM request_cache WHERE cache_key=? AND created_at>=?",
                (key, cutoff),
            ).fetchone()
            return row["response_text"] if row else None

    def _store_cache(self, url: str, text: str, status_code: int, content_type: str | None = None) -> None:
        key = self._cache_key(url)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO request_cache(cache_key, url, response_text, status_code, content_type, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (key, url, text, status_code, content_type),
            )

    def fetch_scheme_list(self, use_cache: bool = True) -> pd.DataFrame:
        url = f"{MFAPI_BASE}/mf"
        text = self._cached_text(url) if use_cache else None
        if text is None:
            response = self.limiter.get(url)
            text = response.text
            self._store_cache(url, text, response.status_code, response.headers.get("content-type"))
        data = json.loads(text)
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["scheme_code", "scheme_name"])
        df = df.rename(columns={"schemeCode": "scheme_code", "schemeName": "scheme_name"})
        df["scheme_code"] = df["scheme_code"].astype(str)
        return df[["scheme_code", "scheme_name"]].drop_duplicates()

    def fetch_nav_history(self, scheme_code: str, use_cache: bool = True) -> pd.DataFrame:
        url = f"{MFAPI_BASE}/mf/{scheme_code}"
        text = self._cached_text(url, ttl_hours=12) if use_cache else None
        if text is None:
            response = self.limiter.get(url)
            text = response.text
            self._store_cache(url, text, response.status_code, response.headers.get("content-type"))
        payload = json.loads(text)
        rows = payload.get("data", [])
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["scheme_code", "nav_date", "nav", "source"])
        df["scheme_code"] = str(scheme_code)
        df["nav_date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        df["source"] = "mfapi"
        return df[["scheme_code", "nav_date", "nav", "source"]].dropna(subset=["nav_date", "nav"])

    def fetch_nav_all_text(self, use_cache: bool = True) -> str:
        text = self._cached_text(AMFI_NAV_ALL, ttl_hours=12) if use_cache else None
        if text is not None:
            return text
        response = self.limiter.get(AMFI_NAV_ALL)
        text = response.text
        self._store_cache(AMFI_NAV_ALL, text, response.status_code, response.headers.get("content-type"))
        return text

    def parse_nav_all(self, text: str) -> pd.DataFrame:
        rows: list[dict[str, str]] = []
        current_amc = None
        current_category = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if ";" not in line:
                if "Mutual Fund" in line or "Fund" in line:
                    current_amc = line
                else:
                    current_category = line
                continue
            if line.startswith("Scheme Code;"):
                continue
            parts = line.split(";")
            if len(parts) < 6:
                continue
            rows.append(
                {
                    "scheme_code": parts[0].strip(),
                    "isin_growth": parts[1].strip() or None,
                    "isin_div_reinvestment": parts[2].strip() or None,
                    "scheme_name": parts[3].strip(),
                    "nav": parts[4].strip(),
                    "nav_date": parts[5].strip(),
                    "amc_name": current_amc,
                    "category": current_category,
                    "scheme_type": current_category.split("(")[0].strip() if current_category else None,
                    "sub_category": current_category,
                    "source": "amfi_navall",
                }
            )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["nav_date"] = pd.to_datetime(df["nav_date"], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
        df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
        return df

    def refresh_scheme_master(self) -> int:
        nav_all = self.parse_nav_all(self.fetch_nav_all_text())
        scheme_list = self.fetch_scheme_list()
        if nav_all.empty:
            master = scheme_list
            for col in ["isin_growth", "isin_div_reinvestment", "amc_name", "category", "sub_category", "scheme_type", "source"]:
                master[col] = None
        else:
            latest = nav_all.sort_values("nav_date").drop_duplicates("scheme_code", keep="last")
            master = latest[
                [
                    "scheme_code",
                    "isin_growth",
                    "isin_div_reinvestment",
                    "scheme_name",
                    "amc_name",
                    "category",
                    "sub_category",
                    "scheme_type",
                    "source",
                ]
            ].copy()
            if not scheme_list.empty:
                master = master.merge(scheme_list, on="scheme_code", how="outer", suffixes=("", "_api"))
                master["scheme_name"] = master["scheme_name"].fillna(master["scheme_name_api"])
                master = master.drop(columns=[c for c in master.columns if c.endswith("_api")])
        master["nav_name"] = master["scheme_name"]
        master["status"] = "active"
        with get_connection() as conn:
            for _, row in master.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO scheme_master
                    (scheme_code, isin_growth, isin_div_reinvestment, scheme_name, amc_name, category,
                     sub_category, scheme_type, nav_name, status, source, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        str(row.get("scheme_code")),
                        row.get("isin_growth"),
                        row.get("isin_div_reinvestment"),
                        row.get("scheme_name"),
                        row.get("amc_name"),
                        row.get("category"),
                        row.get("sub_category"),
                        row.get("scheme_type"),
                        row.get("nav_name"),
                        row.get("status"),
                        row.get("source") or "mfapi",
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded) VALUES('amfi_scheme_master',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'ok',?)",
                (len(master),),
            )
        return len(master)

    def refresh_nav_history(self, scheme_codes: list[str]) -> int:
        total = 0
        for scheme_code in scheme_codes:
            df = self.fetch_nav_history(str(scheme_code))
            if not df.empty:
                with get_connection() as conn:
                    for _, row in df.iterrows():
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO nav_history(scheme_code, nav_date, nav, source)
                            VALUES (?, ?, ?, ?)
                            """,
                            (row["scheme_code"], row["nav_date"], float(row["nav"]), row["source"]),
                        )
                    total += len(df)
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded) VALUES('amfi_nav_history',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'ok',?)",
                (total,),
            )
        return total
