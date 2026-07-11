from __future__ import annotations

import json
from typing import Any

from src.detection.change_detector import manager_key
from src.intelligence.confidence import system_confidence
from src.utils.db import get_connection


def store_tavily_results(query: str, payload: dict[str, Any]) -> list[int]:
    evidence_ids: list[int] = []
    results = payload.get("results", []) or []
    with get_connection() as conn:
        for item in results:
            cur = conn.execute(
                """
                INSERT INTO source_evidence
                (source_name, source_type, source_url, query, title, snippet, raw_json, relevance_score)
                VALUES('tavily', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("source") or "web",
                    item.get("url"),
                    query,
                    item.get("title"),
                    item.get("content") or item.get("snippet"),
                    json.dumps(item, default=str),
                    item.get("score"),
                ),
            )
            evidence_ids.append(int(cur.lastrowid))
    return evidence_ids


def store_claim(
    evidence_id: int | None,
    claim_type: str,
    claim_text: str,
    manager_name: str | None = None,
    amc_name: str | None = None,
    scheme_code: str | None = None,
    scheme_name: str | None = None,
    event_date: str | None = None,
    confidence_score: float = 0.5,
    llm_verdict: str | None = None,
    status: str = "pending",
) -> int:
    key = manager_key(manager_name, amc_name) if manager_name else None
    evidence_items = []
    if evidence_id is not None:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM source_evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            if row:
                evidence_items.append(dict(row))
    sys_confidence = system_confidence(evidence_items)
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO manager_claims
            (evidence_id, claim_type, manager_name, manager_key, scheme_code, scheme_name, amc_name,
             event_date, claim_text, confidence_score, system_confidence, source_type, llm_verdict, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                claim_type,
                manager_name,
                key,
                scheme_code,
                scheme_name,
                amc_name,
                event_date,
                claim_text,
                sys_confidence,
                sys_confidence,
                None,
                llm_verdict,
                status,
            ),
        )
        return int(cur.lastrowid)
