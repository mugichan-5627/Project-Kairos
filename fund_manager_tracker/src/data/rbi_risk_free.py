from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from src.utils.db import get_connection


FBIL_BENCHMARK_URL = "https://www.fbil.org.in/benchmark.html"
CCIL_TBILL_INDEX_URL = "https://www.ccilindia.com/web/ccil/ccil-tbill-index"
FALLBACK_RFR_PATH = Path(__file__).resolve().parents[2] / "seed_data" / "rfr_monthly_fallback.csv"


@dataclass
class RBIRiskFreeLoader:
    fallback_annual_rate: float = 0.065

    def fetch_fbil(self) -> pd.DataFrame:
        response = requests.get(FBIL_BENCHMARK_URL, headers={"User-Agent": "Mozilla/5.0 Project Kairos"}, timeout=45)
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        return self._parse_rate_tables(tables, source="fbil_91_day_tbill")

    def fetch_ccil(self) -> pd.DataFrame:
        response = requests.get(CCIL_TBILL_INDEX_URL, headers={"User-Agent": "Mozilla/5.0 Project Kairos"}, timeout=45)
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        return self._parse_rate_tables(tables, source="ccil_tbill")

    def _parse_rate_tables(self, tables: list[pd.DataFrame], source: str) -> pd.DataFrame:
        frames = []
        for table in tables:
            df = table.copy()
            df.columns = [str(c).strip().lower() for c in df.columns]
            table_text = " ".join(df.columns) + " " + " ".join(map(str, df.head(5).values.flatten())).lower()
            if "91" not in table_text or ("t-bill" not in table_text and "tbill" not in table_text and "treasury" not in table_text):
                continue
            date_col = next((c for c in df.columns if "date" in c), df.columns[0])
            rate_col = next((c for c in df.columns if "91" in c and any(k in c for k in ["yield", "rate", "t-bill", "tbill"])), None)
            if rate_col is None:
                numeric_cols = [c for c in df.columns if c != date_col and pd.to_numeric(df[c].astype(str).str.extract(r"([0-9]+\.?[0-9]*)")[0], errors="coerce").notna().sum() > 0]
                rate_col = numeric_cols[-1] if numeric_cols else None
            if rate_col is None:
                continue
            parsed = pd.DataFrame(
                {
                    "date": pd.to_datetime(df[date_col], errors="coerce", dayfirst=True),
                    "annual_yield": pd.to_numeric(df[rate_col].astype(str).str.extract(r"([0-9]+\.?[0-9]*)")[0], errors="coerce") / 100,
                }
            ).dropna()
            if not parsed.empty:
                frames.append(parsed)
        if not frames:
            return pd.DataFrame()
        weekly = pd.concat(frames).drop_duplicates("date").sort_values("date")
        monthly = weekly.set_index("date")["annual_yield"].resample("ME").mean().dropna()
        return pd.DataFrame({"factor_date": monthly.index.strftime("%Y-%m-%d"), "rfr_monthly": monthly.values / 12, "rfr_source": source})

    def curated_fallback_series(self) -> pd.DataFrame:
        if FALLBACK_RFR_PATH.exists():
            df = pd.read_csv(FALLBACK_RFR_PATH)
            df["factor_date"] = pd.to_datetime(df["factor_date"]).dt.to_period("M").dt.to_timestamp("M")
            df = df.set_index("factor_date").sort_index()
            monthly_index = pd.date_range(df.index.min(), pd.Timestamp.today().to_period("M").to_timestamp("M"), freq="ME")
            annual = df["annual_yield"].reindex(monthly_index).interpolate(method="time").ffill().bfill()
            return pd.DataFrame({"factor_date": monthly_index.strftime("%Y-%m-%d"), "rfr_monthly": annual.values / 12, "rfr_source": "curated_rfr_monthly_fallback"})
        dates = pd.date_range("2010-01-31", pd.Timestamp.today(), freq="ME")
        return pd.DataFrame({"factor_date": dates.strftime("%Y-%m-%d"), "rfr_monthly": self.fallback_annual_rate / 12, "rfr_source": "constant_rate_fallback"})

    def refresh(self) -> int:
        source_status = "ok"
        try:
            monthly = self.fetch_fbil()
        except Exception as exc:
            monthly = pd.DataFrame()
            with get_connection() as conn:
                conn.execute("INSERT INTO data_quality_log(check_name,status,details) VALUES('fbil_rfr_download','failure',?)", (str(exc),))
        if monthly.empty:
            try:
                monthly = self.fetch_ccil()
            except Exception as exc:
                with get_connection() as conn:
                    conn.execute("INSERT INTO data_quality_log(check_name,status,details) VALUES('ccil_rfr_download','failure',?)", (str(exc),))
        fallback = monthly.empty
        if fallback:
            monthly = self.curated_fallback_series()
            source_status = "curated_fallback"
        with get_connection() as conn:
            for _, row in monthly.iterrows():
                conn.execute(
                    """
                    INSERT INTO factor_data(factor_date, rfr_monthly, risk_free_monthly, rfr_source, rfr_is_fallback, last_updated)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(factor_date) DO UPDATE SET
                        rfr_monthly=excluded.rfr_monthly,
                        risk_free_monthly=excluded.risk_free_monthly,
                        rfr_source=excluded.rfr_source,
                        rfr_is_fallback=excluded.rfr_is_fallback,
                        last_updated=CURRENT_TIMESTAMP
                    """,
                    (row["factor_date"], float(row["rfr_monthly"]), float(row["rfr_monthly"]), row["rfr_source"], int(fallback)),
                )
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded,error_message) VALUES('risk_free_rate',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,?)",
                (source_status, len(monthly), None if not fallback else "Used seed_data/rfr_monthly_fallback.csv"),
            )
        return len(monthly)
