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

# Serverless platforms (Vercel/AWS Lambda) mount the code directory read-only;
# only /tmp is writable. Detect that and route all writes there.
IS_SERVERLESS = bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))

_BUNDLED_DB = ROOT_DIR / "fund_data.db"


def _resolve_db_path() -> Path:
    raw = os.getenv("KAIROS_DB_PATH", "")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT_DIR / path
    elif IS_SERVERLESS:
        path = Path("/tmp/fund_data.db")
    else:
        path = _BUNDLED_DB
    # Seed the writable copy from the bundled read-only store on cold start.
    if (
        IS_SERVERLESS
        and str(path).replace("\\", "/").startswith("/tmp/")
        and not path.exists()
        and _BUNDLED_DB.exists()
    ):
        import shutil

        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_BUNDLED_DB, path)
    return path


DB_PATH = _resolve_db_path()
DB_IS_EPHEMERAL = str(DB_PATH).replace("\\", "/").startswith("/tmp/")

_WRITE_ROOT = Path("/tmp/kairos") if IS_SERVERLESS else ROOT_DIR
LOG_DIR = _WRITE_ROOT / "logs"
CACHE_DIR = _WRITE_ROOT / "cache"
REPORTS_DIR = _WRITE_ROOT / "reports"
RAW_SID_DIR = CACHE_DIR / "raw_sids"
LAST_UPDATED_PATH = _WRITE_ROOT / "last_updated.json"

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
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Read-only filesystem — callers that only read the DB don't care.
            pass
