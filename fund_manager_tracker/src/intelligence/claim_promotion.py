from __future__ import annotations

import json

import pandas as pd

from src.data.canonical_manager import import_seed_dataframe, sync_canonical_to_legacy
from src.intelligence.confidence import system_confidence
from src.utils.db import get_connection, read_sql


PROMOTION_THRESHOLD = 0.70


def evidence_for_claim(claim_id: int) -> list[dict]:
    rows = read_sql(
        """
        SELECT se.*
        FROM manager_claims mc
        LEFT JOIN source_evidence se ON se.evidence_id=mc.evidence_id
        WHERE mc.claim_id=?
        """,
        (claim_id,),
    )
    return rows.dropna(how="all").to_dict("records")


def recompute_claim_confidence(claim_id: int) -> float:
    confidence = system_confidence(evidence_for_claim(claim_id))
    with get_connection() as conn:
        conn.execute(
            "UPDATE manager_claims SET system_confidence=?, confidence_score=? WHERE claim_id=?",
            (confidence, confidence, claim_id),
        )
    return confidence


def update_claim_status(claim_id: int, status: str, reviewer_note: str | None = None) -> dict:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE manager_claims
            SET status=?, reviewed_at=CURRENT_TIMESTAMP,
                error_message=COALESCE(?, error_message)
            WHERE claim_id=?
            """,
            (status, reviewer_note, claim_id),
        )
    return {"claim_id": claim_id, "status": status}


def promote_claim_to_truth(claim_id: int, edited_fields: dict | None = None) -> dict:
    claim_df = read_sql("SELECT * FROM manager_claims WHERE claim_id=?", (claim_id,))
    if claim_df.empty:
        return {"status": "missing_claim", "claim_id": claim_id}
    claim = claim_df.iloc[0].to_dict()
    if edited_fields:
        claim.update({k: v for k, v in edited_fields.items() if v is not None})
    confidence = claim.get("system_confidence")
    if confidence is None or pd.isna(confidence):
        confidence = recompute_claim_confidence(claim_id)
    if float(confidence) < PROMOTION_THRESHOLD:
        update_claim_status(claim_id, "needs_review", "System confidence below promotion threshold")
        return {"status": "needs_review", "claim_id": claim_id, "system_confidence": confidence}
    manager_name = claim.get("manager_name") or _from_parsed(claim, "extracted_manager_name")
    scheme_name = claim.get("scheme_name") or _from_parsed(claim, "extracted_scheme")
    amc_name = claim.get("amc_name") or _from_parsed(claim, "extracted_amc")
    event_date = claim.get("event_date") or _from_parsed(claim, "extracted_date")
    if not manager_name or not scheme_name or not amc_name:
        update_claim_status(claim_id, "needs_review", "Missing manager, scheme, or AMC")
        return {"status": "needs_review", "claim_id": claim_id, "reason": "missing_required_fields"}
    seed_row = pd.DataFrame(
        [
            {
                "scheme_code": claim.get("scheme_code") or f"CLAIM-{claim_id}",
                "scheme_name": scheme_name,
                "amc_name": amc_name,
                "manager_name": manager_name,
                "role": "lead",
                "rank": 1,
                "start_date": event_date if claim.get("claim_type") in ("manager_join", "manager_related") else None,
                "end_date": event_date if claim.get("claim_type") in ("manager_exit", "amc_switch") else None,
                "source": "claim_promotion",
                "source_type": claim.get("source_type") or "tavily",
                "source_url": _evidence_url(claim_id),
                "confidence_score": confidence,
                "event_type": claim.get("claim_type"),
                "notes": claim.get("claim_text"),
                "evidence_ids": str(claim.get("evidence_id") or ""),
            }
        ]
    )
    result = import_seed_dataframe(seed_row, verified=True)
    sync_canonical_to_legacy()
    update_claim_status(claim_id, "accepted")
    return {"status": "accepted", "claim_id": claim_id, "system_confidence": confidence, **result}


def _from_parsed(claim: dict, key: str) -> str | None:
    try:
        parsed = json.loads(claim.get("parsed_json") or "{}")
    except json.JSONDecodeError:
        return None
    value = parsed.get(key)
    if value in ("null", ""):
        return None
    return value


def _evidence_url(claim_id: int) -> str | None:
    rows = read_sql(
        """
        SELECT se.source_url FROM manager_claims mc
        LEFT JOIN source_evidence se ON se.evidence_id=mc.evidence_id
        WHERE mc.claim_id=?
        """,
        (claim_id,),
    )
    if rows.empty:
        return None
    return rows.iloc[0]["source_url"]
