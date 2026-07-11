from __future__ import annotations

from src.config import MAJOR_AMCS
from src.intelligence.claim_store import store_tavily_results
from src.intelligence.confidence import system_confidence
from src.intelligence.evidence_extractor import classify_evidence_text
from src.intelligence.tavily_search import TavilySearchClient
from src.utils.db import get_connection, read_sql


def search_manager_transition_evidence(amc_names: list[str] | None = None, max_results: int = 8) -> dict:
    client = TavilySearchClient()
    amc_names = amc_names or MAJOR_AMCS
    summary = {"queries": 0, "evidence_rows": 0, "errors": []}
    for amc in amc_names:
        query = f'{amc} fund manager change exit joins mutual fund India'
        payload = client.search(query, max_results=max_results)
        if payload.get("error"):
            summary["errors"].append(payload["error"])
            continue
        evidence_ids = store_tavily_results(query, payload)
        summary["queries"] += 1
        summary["evidence_rows"] += len(evidence_ids)
    return summary


def auto_classify_raw_evidence(limit: int = 100) -> int:
    evidence = read_sql(
        "SELECT evidence_id, title, snippet FROM source_evidence WHERE extraction_status='raw' ORDER BY observed_at DESC LIMIT ?",
        (limit,),
    )
    count = 0
    with get_connection() as conn:
        for _, row in evidence.iterrows():
            text = f"{row.get('title') or ''}\n{row.get('snippet') or ''}"
            claim_type, score = classify_evidence_text(text)
            conn.execute(
                "UPDATE source_evidence SET extraction_status=? WHERE evidence_id=?",
                (claim_type, int(row["evidence_id"])),
            )
            if claim_type != "unknown":
                evidence_item = row.to_dict()
                evidence_item["source_type"] = "tavily"
                conn.execute(
                    """
                    INSERT INTO manager_claims(evidence_id, claim_type, claim_text, confidence_score, system_confidence, source_type, status)
                    VALUES (?, ?, ?, ?, ?, 'tavily', 'pending')
                    """,
                    (int(row["evidence_id"]), claim_type, text[:1000], system_confidence([evidence_item]), system_confidence([evidence_item])),
                )
            count += 1
    return count
