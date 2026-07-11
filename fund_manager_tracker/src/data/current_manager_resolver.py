from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import DB_PATH
from src.detection.change_detector import manager_key
from src.intelligence.tavily_search import TavilySearchClient
from src.utils.db import get_connection, read_sql


_MANAGER_PATTERNS = [
    re.compile(r"(?:fund\s+manager|managed\s+by|manager\s+is|managers?\s+are)\s*[-:\u2013]?\s*(?:Mr\.?\s*)?([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})", re.I),
    re.compile(r"([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\s+(?:since|is\s+the\s+current\s+fund\s+manager)", re.I),
]

_BAD_NAME_WORDS = {
    "fund", "funds", "manager", "managers", "managed", "scheme", "schemes",
    "growth", "direct", "regular", "mutual", "latest", "current",
    "performance", "portfolio", "india", "indian", "top", "best", "rising",
    "leading", "list", "ranked", "asset", "management", "equity", "debt",
    "small", "mid", "large", "cap", "amc", "review", "returns", "nav",
}

# Titles that indicate a listicle/roundup rather than scheme-specific coverage —
# these pages name many managers and extraction from them is unreliable.
_LISTICLE_TITLE_RE = re.compile(r"\b(top|best|\d+\s+(?:best|top)|ranked|list of)\b", re.I)

_NAME_WORD_RE = re.compile(r"^[A-Z][a-z.'-]+$|^[A-Z]\.?$")
_NAME_PARTICLES = {"da", "de", "van", "von", "bin", "al", "el"}  # Sunaina da Cunha


def _is_plausible_person_name(name: str) -> bool:
    """Every word properly capitalized (lowercase name particles allowed),
    2-4 words, no domain stopwords.

    The extraction regexes run case-insensitively to catch lead-in phrases,
    which silently disables their [A-Z] anchors — this validator restores the
    capitalization requirement (fixes 'rising in India' being read as a name).
    """
    words = name.split()
    if not 2 <= len(words) <= 4:
        return False
    if any(w.lower() in _BAD_NAME_WORDS for w in words):
        return False
    return all(_NAME_WORD_RE.match(w) or w in _NAME_PARTICLES for w in words)


@dataclass
class CurrentManagerResolver:
    search_client: Any | None = None
    db_path: Path = DB_PATH

    def __post_init__(self) -> None:
        if self.search_client is None:
            self.search_client = TavilySearchClient()

    def resolve(self, scheme_code: str, scheme_name: str, amc_name: str | None = None) -> dict[str, Any]:
        cached = self._from_snapshot(scheme_code)
        if cached:
            return cached

        history = self._from_open_history(scheme_code)
        if history:
            self._cache(scheme_code, scheme_name, amc_name, history)
            return history

        live = self._from_live_search(scheme_name, amc_name)
        if live:
            self._cache(scheme_code, scheme_name, amc_name, live)
            return live

        return {
            "manager_name": None,
            "confirmed_date": None,
            "source": None,
            "source_url": None,
            "confidence_score": 0.0,
            "resolution_status": "unknown",
        }

    def _from_snapshot(self, scheme_code: str) -> dict[str, Any] | None:
        current = read_sql(
            """
            SELECT manager_name, confirmed_date, source, source_url, confidence_score
            FROM current_manager_snapshot
            WHERE scheme_code=? AND manager_name IS NOT NULL
            ORDER BY confirmed_date DESC, created_at DESC
            LIMIT 1
            """,
            (scheme_code,),
            db_path=self.db_path,
        )
        if current.empty:
            return None
        row = current.iloc[0]
        return {
            "manager_name": row["manager_name"],
            "confirmed_date": row.get("confirmed_date"),
            "source": row.get("source") or "current_manager_snapshot",
            "source_url": row.get("source_url"),
            "confidence_score": float(row.get("confidence_score") or 0.75),
            "resolution_status": "cached",
        }

    def _from_open_history(self, scheme_code: str) -> dict[str, Any] | None:
        history = read_sql(
            """
            SELECT manager_name, start_date, source, raw_evidence, confidence_score
            FROM manager_scheme_history
            WHERE scheme_code=? AND manager_name IS NOT NULL
              AND (end_date IS NULL OR end_date='' OR date(end_date) >= date('now'))
            ORDER BY start_date DESC
            LIMIT 1
            """,
            (scheme_code,),
            db_path=self.db_path,
        )
        if history.empty:
            return None
        row = history.iloc[0]
        return {
            "manager_name": row["manager_name"],
            "confirmed_date": row.get("start_date"),
            "source": row.get("source") or "manager_scheme_history",
            "source_url": row.get("raw_evidence"),
            "confidence_score": float(row.get("confidence_score") or 0.7),
            "resolution_status": "history",
        }

    def _from_live_search(self, scheme_name: str, amc_name: str | None) -> dict[str, Any] | None:
        if not self.search_client or not getattr(self.search_client, "enabled", False):
            return None
        query = self._query(scheme_name, amc_name)
        payload = self.search_client.search(query, max_results=5, include_answer=True, topic="general")
        if payload.get("error"):
            return None

        candidates: list[dict[str, Any]] = []
        for item in payload.get("results", []) or []:
            candidates.append({
                "text": " ".join(str(item.get(k) or "") for k in ("title", "content")),
                "url": item.get("url"),
                "source_title": item.get("title"),
            })
        answer = payload.get("answer")
        if answer:
            candidates.append({"text": answer, "url": None, "source_title": "Tavily answer"})

        known = self._known_manager_names()
        for candidate in candidates:
            title = str(candidate.get("source_title") or "")
            if _LISTICLE_TITLE_RE.search(title):
                continue  # roundup pages name many managers; skip
            manager = self._extract_manager_name(candidate["text"])
            if not manager:
                continue
            # Cross-validation against the canonical manager universe raises
            # confidence; an unknown name from a live page stays tentative.
            recognized = manager.lower() in known
            confidence = 0.80 if recognized else (0.62 if candidate.get("url") else 0.5)
            return {
                "manager_name": manager,
                "confirmed_date": self._extract_since_date(candidate["text"]),
                "source": "live_search",
                "source_url": candidate.get("url"),
                "source_title": candidate.get("source_title"),
                "confidence_score": confidence,
                "resolution_status": "live_search" if recognized else "live_search_unverified",
            }
        return None

    def _known_manager_names(self) -> set[str]:
        try:
            rows = read_sql(
                """
                SELECT canonical_name AS name FROM manager_identity
                UNION SELECT alias_name AS name FROM manager_alias
                """,
                db_path=self.db_path,
            )
            return {str(n).lower() for n in rows["name"].dropna()}
        except Exception:
            return set()

    def _query(self, scheme_name: str, amc_name: str | None) -> str:
        compact_name = re.sub(r"\s+-\s+(Direct|Regular).*", "", scheme_name, flags=re.I)
        parts = [compact_name, "current fund manager"]
        if amc_name:
            parts.append(amc_name)
        parts.append("mutual fund India")
        return " ".join(parts)

    def _extract_manager_name(self, text: str) -> str | None:
        clean = re.sub(r"\s+", " ", text or "").strip()
        for pattern in _MANAGER_PATTERNS:
            match = pattern.search(clean)
            if not match:
                continue
            name = re.sub(r"\s+(?:since|from|for)\b.*$", "", match.group(1), flags=re.I).strip(" .,-")
            if _is_plausible_person_name(name):
                return name
        return None

    def _extract_since_date(self, text: str) -> str | None:
        match = re.search(r"(?:since|from|effect\s+from)\s+(\d{1,2}[-\s][A-Za-z]{3,9}[-\s]\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|[A-Za-z]{3,9}\s+\d{4})", text or "", re.I)
        if not match:
            return None
        raw = match.group(1).replace(",", "")
        parsed = pd.to_datetime(raw, errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")

    def _cache(self, scheme_code: str, scheme_name: str, amc_name: str | None, resolved: dict[str, Any]) -> None:
        manager_name = resolved.get("manager_name")
        if not manager_name:
            return
        confirmed_date = resolved.get("confirmed_date") or date.today().isoformat()
        key = manager_key(manager_name, amc_name)
        with get_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO current_manager_snapshot
                (scheme_code, scheme_name, amc_name, manager_name, manager_key, role, rank,
                 confirmed_date, source, source_url, confidence_score)
                VALUES (?, ?, ?, ?, ?, 'manager', 1, ?, ?, ?, ?)
                """,
                (
                    scheme_code,
                    scheme_name,
                    amc_name,
                    manager_name,
                    key,
                    confirmed_date,
                    resolved.get("source") or "resolver",
                    resolved.get("source_url"),
                    float(resolved.get("confidence_score") or 0.5),
                ),
            )
