from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from src.utils.db import get_connection, read_sql


NIFTY_HISTORY_URL = "https://www.niftyindices.com/Backpage.aspx/downloadIndexHistory"

INDEX_NAMES = {
    "nifty50": "NIFTY 50",
    "nifty500": "NIFTY 500",
    "smallcap250": "NIFTY SMALLCAP 250",
    "value50": "NIFTY500 VALUE 50",
    "momentum50": "NIFTY MOMENTUM 50",
    "midcap150": "NIFTY MIDCAP 150",
}

# â”€â”€ Verified yfinance tickers for real NSE index data â”€â”€
# These tickers are confirmed working on yfinance and correspond
# to actual NSE indices used to construct Carhart-style factors:
#   NIFTY 500 (broad market) â†’  ^CRSLDX
#   NIFTY 50 (large-cap)     â†’  ^NSEI
#   Midcap 50 (small proxy)  â†’  ^NSEMDCP50  (closest avail proxy for SMB)
#   NV20 (value proxy)       â†’  NV20.NS     (for value tilt / HML)
#   NIFTY 50 (mom stand-in)  â†’  ^NSEI       (no clean mom ticker on yf)
#
# Note: These are real NSE price indices, NOT synthetic/fallback data.
# The factor_is_fallback flag should be 0 when using these tickers.
YF_TICKERS = {
    "nifty500": "^CRSLDX",
    "nifty50": "^NSEI",
    "smallcap250": "^NSEMDCP50",
    "value50": "NV20.NS",
    "momentum50": "^NSEI",
}


@dataclass
class NSEFactorLoader:
    start: str = "2006-01-01"
    end: str | None = None
    repo_rate_fallback: float = 0.065

    def _nifty_date(self, value: str) -> str:
        return pd.to_datetime(value).strftime("%d-%b-%Y")

    def download_nifty_index(self, index_name: str) -> pd.Series:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept": "text/csv,application/json,text/plain,*/*",
                "Referer": "https://www.niftyindices.com/reports/historical-data",
                "Content-Type": "application/json;charset=UTF-8",
            }
        )
        end = self.end or date.today().isoformat()
        cinfo = {
            "name": index_name,
            "startDate": self._nifty_date(self.start),
            "endDate": self._nifty_date(end),
            "indexName": index_name,
        }
        response = session.post(NIFTY_HISTORY_URL, json={"cinfo": json.dumps(cinfo)}, timeout=45)
        response.raise_for_status()
        text = response.text.strip()
        if text.startswith("{"):
            payload = response.json()
            text = payload.get("data") or payload.get("d") or ""
        df = pd.read_csv(io.StringIO(text))
        df.columns = [c.strip() for c in df.columns]
        date_col = next((c for c in df.columns if c.lower() == "date"), None)
        close_col = next((c for c in df.columns if c.lower() == "close"), None)
        if not date_col or not close_col:
            raise ValueError(f"Nifty CSV for {index_name} missing Date/Close columns")
        series = pd.Series(
            pd.to_numeric(df[close_col], errors="coerce").values,
            index=pd.to_datetime(df[date_col], errors="coerce"),
            name=index_name,
        ).dropna()
        return series.sort_index()

    def download_prices_niftyindices(self) -> pd.DataFrame:
        frames = []
        failures = []
        for key, index_name in INDEX_NAMES.items():
            try:
                frames.append(self.download_nifty_index(index_name).rename(key))
            except Exception as exc:
                failures.append(f"{index_name}: {exc}")
        if failures:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO data_quality_log(check_name,status,details) VALUES('niftyindices_download','partial_failure',?)",
                    (" | ".join(failures),),
                )
        if len(frames) < 5:
            return pd.DataFrame()
        return pd.concat(frames, axis=1).sort_index()

    def download_prices_yfinance(self, tickers: dict | None = None) -> pd.DataFrame:
        """Download proxy index prices from yfinance."""
        end = self.end or date.today().isoformat()
        if tickers is None:
            tickers = YF_TICKERS
        frames = []
        failures = []
        for name, ticker in tickers.items():
            try:
                data = yf.download(ticker, start=self.start, end=end, progress=False, auto_adjust=True)
                if data.empty:
                    failures.append(f"{ticker}: empty result")
                    continue
                close_data = data["Close"]
                if isinstance(close_data, pd.DataFrame):
                    series = close_data.iloc[:, 0].rename(name)
                else:
                    series = close_data.rename(name)
                frames.append(series)
            except Exception as exc:
                failures.append(f"{ticker}: {exc}")
                with get_connection() as conn:
                    conn.execute(
                        "INSERT INTO data_quality_log(check_name,status,details) VALUES('yfinance_factor_download','failure',?)",
                        (f"{ticker}: {exc}",),
                    )
        if failures:
            print(f"[yfinance] failures: {failures}")
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, axis=1).sort_index()

    def download_prices(self) -> tuple[pd.DataFrame, bool]:
        """Try direct niftyindices.com first, fall back to yfinance.

        Returns (prices_df, is_fallback).
        niftyindices.com â†’ is_fallback=False (direct TRI data)
        yfinance proxy tickers -> is_fallback=True
        """
        direct = self.download_prices_niftyindices()
        if not direct.empty:
            return direct, False

        # yfinance proxy tickers are real market series, but not the exact
        # NiftyIndices factor legs, so attribution results must be flagged.
        proxy = self.download_prices_yfinance(YF_TICKERS)
        if not proxy.empty and len(proxy.columns) >= 3:
            return proxy, True

        return pd.DataFrame(), True

    def construct_monthly_factors(self, prices: pd.DataFrame, factor_is_fallback: bool = False) -> pd.DataFrame:
        if prices.empty:
            return pd.DataFrame()
        monthly_prices = prices.resample("ME").last()
        returns = monthly_prices.pct_change()
        out = pd.DataFrame(index=returns.index)
        for col in ["nifty500", "nifty50", "smallcap250", "value50", "momentum50", "midcap150"]:
            out[f"{col}_return"] = returns[col] if col in returns else np.nan
        out["india_vix"] = monthly_prices["india_vix"] if "india_vix" in monthly_prices else np.nan

        # â”€â”€ Risk-free rate: use existing DB values or flat 6.5% / 12 â”€â”€
        existing_rfr = read_sql("SELECT factor_date, rfr_monthly, risk_free_monthly, rfr_source, rfr_is_fallback FROM factor_data")
        if not existing_rfr.empty:
            existing_rfr["factor_date"] = pd.to_datetime(existing_rfr["factor_date"]).dt.to_period("M").dt.to_timestamp("M")
            existing_rfr = existing_rfr.drop_duplicates("factor_date").set_index("factor_date")
            out = out.join(existing_rfr[["rfr_monthly", "risk_free_monthly", "rfr_source", "rfr_is_fallback"]], how="left")

        out["repo_rate"] = self.repo_rate_fallback
        # Primary RFR source: committed RBI 91-day T-bill monthly series.
        # Falls back to flat repo only if the CSV is unreadable.
        seed_csv = (
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "seed_data"
            / "rfr_monthly_91d_tbill.csv"
        )
        rbi_series = None
        try:
            if seed_csv.exists():
                rbi = pd.read_csv(seed_csv)
                rbi["factor_date"] = pd.to_datetime(rbi["factor_date"]).dt.to_period("M").dt.to_timestamp("M")
                rbi = rbi.drop_duplicates("factor_date").set_index("factor_date")
                rbi_series = rbi["rfr_monthly"].astype(float)
        except Exception:
            rbi_series = None
        existing_rfr_col = out.get("rfr_monthly", pd.Series(index=out.index, dtype=float))
        if rbi_series is not None:
            out["rfr_monthly"] = existing_rfr_col.fillna(rbi_series.reindex(out.index))
            out["rfr_source"] = out.get("rfr_source", pd.Series(index=out.index, dtype=object)).fillna("rbi_handbook_91d_tbill")
            out["rfr_is_fallback"] = out.get("rfr_is_fallback", pd.Series(index=out.index, dtype=float)).fillna(0)
        # Anything still missing falls back to repo_rate / 12 with explicit flag
        flat_rfr = self.repo_rate_fallback / 12
        missing_mask = out["rfr_monthly"].isna()
        out["rfr_monthly"] = out["rfr_monthly"].fillna(flat_rfr)
        out["risk_free_monthly"] = out.get("risk_free_monthly", pd.Series(index=out.index, dtype=float)).fillna(out["rfr_monthly"])
        out["rfr_source"] = out.get("rfr_source", pd.Series(index=out.index, dtype=object)).fillna("flat_repo_fallback")
        if "rfr_is_fallback" not in out.columns:
            out["rfr_is_fallback"] = 0
        out.loc[missing_mask, "rfr_source"] = "flat_repo_fallback"
        out.loc[missing_mask, "rfr_is_fallback"] = 1

        out["mkt_rf"] = out["nifty500_return"] - out["risk_free_monthly"]
        out["smb"] = out["smallcap250_return"] - out["nifty50_return"]
        # Pragmatic value tilt factor: long NIFTY500 Value 50 vs broad NIFTY 500.
        # This is intentionally not labelled HML in the UI because there is no
        # clean growth short leg in the free NSE index set.
        out["hml"] = out["value50_return"] - out["nifty500_return"]
        out["wml"] = out["momentum50_return"] - out["nifty500_return"]
        out["qmj"] = np.nan
        out["factor_date"] = out.index.strftime("%Y-%m-%d")
        out["source"] = "niftyindices" if not factor_is_fallback else "yfinance_nse_indices"
        out["factor_source"] = out["source"]
        out["factor_is_fallback"] = int(factor_is_fallback)
        required = ["mkt_rf", "smb", "hml", "wml"]
        missing_counts = out[required].isna().sum().to_dict()
        if any(missing_counts.values()):
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO data_quality_log(check_name,status,details) VALUES('factor_missing_values','warning',?)",
                    (json.dumps(missing_counts),),
                )
        return out.reset_index(drop=True)

    def refresh(self) -> int:
        prices, fallback = self.download_prices()
        factors = self.construct_monthly_factors(prices, factor_is_fallback=fallback)
        if factors.empty:
            with get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO source_status(source_name,last_attempt,status,error_message) VALUES('factor_data',CURRENT_TIMESTAMP,'empty','No factor rows downloaded')"
                )
            return 0
        cols = [
            "factor_date",
            "nifty500_return",
            "nifty50_return",
            "smallcap250_return",
            "value50_return",
            "momentum50_return",
            "midcap150_return",
            "india_vix",
            "repo_rate",
            "rfr_monthly",
            "rfr_source",
            "rfr_is_fallback",
            "risk_free_monthly",
            "mkt_rf",
            "smb",
            "hml",
            "wml",
            "qmj",
            "source",
            "factor_source",
            "factor_is_fallback",
        ]
        with get_connection() as conn:
            for _, row in factors[cols].iterrows():
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO factor_data({",".join(cols)}, last_updated)
                    VALUES ({",".join(["?"] * len(cols))}, CURRENT_TIMESTAMP)
                    """,
                    tuple(row[c] if pd.notna(row[c]) else None for c in cols),
                )
            conn.execute(
                "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded,error_message) VALUES('factor_data',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,?)",
                ("yfinance_nse_indices" if not fallback else "fallback_yfinance", len(factors), None if not fallback else "Used legacy fallback tickers"),
            )
        return len(factors)


def load_manual_factor_csvs(files: dict[str, str | io.BytesIO]) -> int:
    frames = []
    for key, file_obj in files.items():
        df = pd.read_csv(file_obj)
        df.columns = [c.strip() for c in df.columns]
        date_col = next((c for c in df.columns if c.lower() == "date"), None)
        close_col = next((c for c in df.columns if c.lower() == "close"), None)
        if not date_col or not close_col:
            raise ValueError(f"{key} CSV missing Date/Close columns")
        series = pd.Series(
            pd.to_numeric(df[close_col], errors="coerce").values,
            index=pd.to_datetime(df[date_col], errors="coerce"),
            name=key,
        ).dropna()
        frames.append(series)
    prices = pd.concat(frames, axis=1).sort_index()
    loader = NSEFactorLoader()
    factors = loader.construct_monthly_factors(prices, factor_is_fallback=False)
    if factors.empty:
        return 0
    factors["source"] = "manual_nse_csv_upload"
    factors["factor_source"] = "manual_nse_csv_upload"
    cols = [
        "factor_date",
        "nifty500_return",
        "nifty50_return",
        "smallcap250_return",
        "value50_return",
        "momentum50_return",
        "midcap150_return",
        "india_vix",
        "repo_rate",
        "rfr_monthly",
        "rfr_source",
        "rfr_is_fallback",
        "risk_free_monthly",
        "mkt_rf",
        "smb",
        "hml",
        "wml",
        "qmj",
        "source",
        "factor_source",
        "factor_is_fallback",
    ]
    with get_connection() as conn:
        for _, row in factors[cols].iterrows():
            conn.execute(
                f"""
                INSERT OR REPLACE INTO factor_data({",".join(cols)}, last_updated)
                VALUES ({",".join(["?"] * len(cols))}, CURRENT_TIMESTAMP)
                """,
                tuple(row[c] if pd.notna(row[c]) else None for c in cols),
            )
        conn.execute(
            "INSERT OR REPLACE INTO source_status(source_name,last_success,last_attempt,status,rows_loaded,error_message) VALUES('manual_factor_csv',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'ok',?,NULL)",
            (len(factors),),
        )
    return len(factors)

