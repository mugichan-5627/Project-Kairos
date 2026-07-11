from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

from src.llm.nvidia_client import NvidiaLLMClient
from src.llm.router import call_llm_with_fallback
from src.utils.db import get_connection


REQUIRED_KEYS = {
    "verdict",
    "confidence",
    "reasoning",
    "extracted_manager_name",
    "extracted_scheme",
    "extracted_amc",
    "extracted_date",
    "claim_type",
}


@dataclass
class LLMJudgeResult:
    raw_output: str
    parsed: dict | None
    parse_status: str
    retry_count: int
    error_message: str | None


def parse_llm_json(output: str) -> dict:
    text = output.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    missing = REQUIRED_KEYS - set(parsed)
    if missing:
        raise ValueError(f"Missing required JSON keys: {', '.join(sorted(missing))}")
    return parsed


def run_heuristic_judge(evidence_rows: list[dict]) -> dict:
    from src.intelligence.evidence_extractor import classify_evidence_text, extract_possible_names
    from src.utils.db import read_sql
    import re
    
    # Combine all evidence texts
    combined_text = ""
    for row in evidence_rows:
        for val in [row.get("title"), row.get("snippet"), row.get("claim_text"), row.get("notes")]:
            if val:
                combined_text += " " + str(val)
    
    # 1. Determine claim type using existing classify_evidence_text
    claim_type, score = classify_evidence_text(combined_text)
    
    # 2. Extract manager name: first check known names in database
    extracted_manager = None
    try:
        known_managers_df = read_sql("SELECT canonical_name FROM manager_identity")
        if not known_managers_df.empty:
            for name in known_managers_df["canonical_name"].tolist():
                if name.lower() in combined_text.lower():
                    extracted_manager = name
                    break
    except Exception:
        pass
        
    if not extracted_manager:
        # Fallback to regex name extractor
        possible = extract_possible_names(combined_text)
        if possible:
            extracted_manager = possible[0]
            
    # 3. Extract AMC
    extracted_amc = None
    from src.config import MAJOR_AMCS
    for amc in MAJOR_AMCS:
        # Remove " AMC" or " Mutual Fund" or " Funds" for easier matching
        short_amc = amc.replace(" AMC", "").replace(" Mutual Fund", "").replace(" Funds", "")
        if short_amc.lower() in combined_text.lower():
            extracted_amc = amc
            break
            
    # 4. Extract Scheme
    extracted_scheme = None
    try:
        # Check if any scheme code or name matches
        # Let's search for 6 digit scheme codes in text
        match_code = re.search(r"\b\d{6}\b", combined_text)
        if match_code:
            code = match_code.group(0)
            scheme_df = read_sql("SELECT scheme_name FROM scheme_master WHERE scheme_code=?", (code,))
            if not scheme_df.empty:
                extracted_scheme = scheme_df.iloc[0]["scheme_name"]
    except Exception:
        pass
        
    # 5. Extract Date
    # Match YYYY-MM-DD
    extracted_date = None
    match_date = re.search(r"\b\d{4}-\d{2}-\d{2}\b", combined_text)
    if match_date:
        extracted_date = match_date.group(0)
    else:
        # Check if any row has date/published field
        for row in evidence_rows:
            d = row.get("published") or row.get("change_date") or row.get("created_at")
            if d:
                # Format to YYYY-MM-DD
                match_d = re.search(r"\b\d{4}-\d{2}-\d{2}\b", str(d))
                if match_d:
                    extracted_date = match_d.group(0)
                    break
                    
    # 6. Verdict and reasoning
    if claim_type == "unknown":
        verdict = "reject"
        confidence = 0.2
        reasoning = "Heuristic check: No manager transition keywords detected."
    elif extracted_manager is None:
        verdict = "needs_review"
        confidence = 0.5
        reasoning = "Heuristic check: Transition keywords detected but manager name could not be identified."
    else:
        verdict = "accept"
        confidence = 0.8
        reasoning = f"Heuristic check: Successfully detected manager '{extracted_manager}' with claim type '{claim_type}'."
        
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
        "extracted_manager_name": extracted_manager,
        "extracted_scheme": extracted_scheme,
        "extracted_amc": extracted_amc,
        "extracted_date": extracted_date,
        "claim_type": claim_type
    }


def judge_evidence_structured(evidence_rows: list[dict], api_key: str | None = None) -> LLMJudgeResult:
    client = NvidiaLLMClient(api_key=api_key)
    provider_available = client.enabled or bool(os.getenv("ANTHROPIC_API_KEY"))
    if not provider_available:
        parsed = run_heuristic_judge(evidence_rows)
        output = json.dumps(parsed)
        return LLMJudgeResult(output, parsed, "ok", 0, None)

    context = json.dumps(evidence_rows, indent=2, default=str)[:12000]
    prompt = (
        "Review the evidence and identify only verifiable mutual fund manager transition claims. "
        "Return exactly one valid JSON object with these keys: verdict, confidence, reasoning, "
        "extracted_manager_name, extracted_scheme, extracted_amc, extracted_date, claim_type. "
        "verdict must be accept, reject, or needs_review. claim_type must be manager_exit, manager_join, "
        "amc_switch, or manager_related. Use null when a field is unknown. Do not include prose outside JSON."
        f"\n\nEvidence:\n{context}"
    )
    strict_system = "You are a strict financial-data evidence judge. Never invent facts not present in evidence."
    retry_count = 0
    error_message = None
    parsed = None
    output = ""
    parse_status = "failed"
    for attempt in range(2):
        retry_count = attempt
        final_prompt = prompt if attempt == 0 else "Respond ONLY with valid JSON. No other text.\n\n" + prompt
        if api_key:
            output = client.complete(strict_system, final_prompt, temperature=0.0, max_tokens=700)
            provider = client.model
        else:
            routed_output, provider = call_llm_with_fallback(final_prompt, strict_system, max_retries=1, max_tokens=700)
            output = routed_output or ""
            if provider == "all_failed":
                error_message = "All configured LLM providers failed"
                parse_status = "failed"
                break
        try:
            parsed = parse_llm_json(output)
            parse_status = "ok"
            error_message = None
            break
        except Exception as exc:
            error_message = str(exc)
            parse_status = "failed"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO llm_audit_log
            (task_name, model, prompt_hash, input_summary, output_text, parsed_json, parse_status,
             retry_count, error_message, source_evidence_ids)
             VALUES('judge_evidence', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider if "provider" in locals() else client.model,
                hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                f"{len(evidence_rows)} evidence rows",
                output,
                json.dumps(parsed, default=str) if parsed else None,
                parse_status,
                retry_count,
                error_message,
                ",".join(str(r.get("evidence_id")) for r in evidence_rows if r.get("evidence_id")),
            ),
        )
    return LLMJudgeResult(output, parsed, parse_status, retry_count, error_message)


def judge_evidence(evidence_rows: list[dict], api_key: str | None = None) -> str:
    result = judge_evidence_structured(evidence_rows, api_key=api_key)
    if result.parse_status != "ok":
        return f"LLM parse failed - manual review required\n\nRaw output:\n{result.raw_output}"
    return json.dumps(result.parsed, indent=2)


def judge_claim(claim_id: int, api_key: str | None = None) -> dict:
    from src.utils.db import read_sql

    rows = read_sql(
        """
        SELECT mc.*, se.title, se.snippet, se.source_url, se.source_name, se.source_type
        FROM manager_claims mc
        LEFT JOIN source_evidence se ON se.evidence_id=mc.evidence_id
        WHERE mc.claim_id=?
        """,
        (claim_id,),
    )
    if rows.empty:
        return {"status": "missing_claim", "claim_id": claim_id}
    result = judge_evidence_structured(rows.to_dict("records"), api_key=api_key)
    parsed = result.parsed or {}
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE manager_claims
            SET parsed_json=?, parse_status=?, error_message=?, llm_confidence=?, llm_verdict=?,
                status=CASE WHEN ?='ok' THEN status ELSE 'needs_review' END
            WHERE claim_id=?
            """,
            (
                json.dumps(parsed, default=str) if parsed else None,
                result.parse_status,
                result.error_message,
                parsed.get("confidence") if parsed else None,
                parsed.get("verdict") if parsed else None,
                result.parse_status,
                claim_id,
            ),
        )
    return {
        "status": result.parse_status,
        "claim_id": claim_id,
        "parsed": parsed,
        "error_message": result.error_message,
    }
