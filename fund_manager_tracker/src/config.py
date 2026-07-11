from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

# Cloud platforms provide environment variables directly.  A local .env file is
# only a developer convenience and is intentionally loaded as a fallback.
# Search up the directory tree so a `.env` at the project root (sibling of
# fund_manager_tracker/) is picked up, not just one inside this package.
try:
    from dotenv import load_dotenv

    for _candidate in (
        ROOT_DIR / ".env",
        ROOT_DIR.parent / ".env",
        ROOT_DIR.parent / ".env.local",
        ROOT_DIR / ".env.local",
    ):
        if _candidate.exists():
            load_dotenv(_candidate, override=False)
except ImportError:
    pass

DB_PATH = Path(os.getenv("KAIROS_DB_PATH", "fund_data.db"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT_DIR / DB_PATH
DB_IS_EPHEMERAL = str(DB_PATH).replace("\\", "/").startswith("/tmp/")

LOG_DIR = ROOT_DIR / "logs"
CACHE_DIR = ROOT_DIR / "cache"
RAW_SID_DIR = CACHE_DIR / "raw_sids"
LAST_UPDATED_PATH = ROOT_DIR / "last_updated.json"

REQUEST_LOG_PATH = LOG_DIR / "requests.log"
PIPELINE_LOG_PATH = LOG_DIR / "pipeline.log"

EQUITY_ONLY = os.getenv("KAIROS_EQUITY_ONLY", "true").lower() == "true"
MAX_NAV_SCHEMES_PER_RUN = int(os.getenv("KAIROS_MAX_NAV_SCHEMES_PER_RUN", "100"))
VRO_LIMIT_PER_RUN = int(os.getenv("KAIROS_VRO_LIMIT_PER_RUN", "50"))

MAJOR_AMCS = [
    "HDFC AMC",
    "ICICI Prudential",
    "SBI Funds",
    "Axis Mutual Fund",
    "Kotak Mahindra",
    "DSP Mutual Fund",
    "Mirae Asset",
    "Franklin Templeton India",
    "Nippon India",
    "UTI Mutual Fund",
    "Aditya Birla Sun Life",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/124.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def ensure_dirs() -> None:
    for path in (LOG_DIR, CACHE_DIR, RAW_SID_DIR):
        path.mkdir(parents=True, exist_ok=True)
