"""Manager assessment engine: quantitative style + qualitative narrative.

Quantitative layer (conventional standards):
  * Empirical-Bayes alpha shrinkage — raw OLS alphas overstate skill when the
    estimate is noisy. The posterior mean under a zero-centred prior with a
    unit-information variance is alpha * t^2 / (1 + t^2), the standard
    "t-shrinkage" used in the fund-skill literature (Jones & Shanken 2005
    flavour). A 2%/yr alpha with t=0.5 shrinks to ~0.4%; with t=3 it keeps ~90%.
  * Returns-based style tilts (Sharpe 1992) — the manager's tenure-weighted
    Carhart loadings classify size (SMB), value/growth (HML), momentum (WML)
    and market posture (beta), each with conventional cutoffs.

Qualitative layer:
  * `manager_qualitative` rows: curated entries carry web-verified summaries
    with source URLs; non-curated entries carry only what the regressions
    support (clearly labelled as derived).
  * `transition_impact_text` composes the expert-style readout an analyst
    would give for a manager arriving at / leaving a fund.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from src.utils.db import get_connection, read_sql


# ── Quantitative primitives ─────────────────────────────────────────


def shrink_alpha(alpha: float | None, tstat: float | None) -> float | None:
    """Empirical-Bayes t-shrinkage: posterior mean = alpha * t² / (1 + t²)."""
    if alpha is None:
        return None
    if tstat is None:
        return 0.0
    t2 = float(tstat) ** 2
    return float(alpha) * t2 / (1.0 + t2)


# Conventional returns-based style cutoffs on long-only tilt factors.
_SIZE_CUTS = [(0.35, "small/mid-cap tilt"), (0.10, "multi-cap"), (-99.0, "large-cap oriented")]
_VALUE_CUTS = [(0.20, "value tilt"), (-0.20, "style-neutral"), (-99.0, "growth tilt")]
_MOM_CUTS = [(0.15, "momentum-following"), (-0.15, "momentum-neutral"), (-99.0, "contrarian vs momentum")]


def _bucket(value: float | None, cuts: list[tuple[float, str]], default: str) -> str:
    if value is None or pd.isna(value):
        return default
    for threshold, label in cuts:
        if value >= threshold:
            return label
    return default


def style_from_loadings(beta_mkt: float | None, beta_smb: float | None,
                        beta_hml: float | None, beta_wml: float | None,
                        adj_r2: float | None) -> dict[str, str]:
    size = _bucket(beta_smb, _SIZE_CUTS, "unclassified")
    value = _bucket(beta_hml, _VALUE_CUTS, "unclassified")
    momentum = _bucket(beta_wml, _MOM_CUTS, "unclassified")
    if beta_mkt is None or pd.isna(beta_mkt):
        posture = "unclassified"
    elif beta_mkt >= 1.10:
        posture = "aggressive (high beta)"
    elif beta_mkt <= 0.90:
        posture = "defensive (low beta)"
    else:
        posture = "market-like beta"
    # Low R² against the factor set = returns not explained by common factors,
    # i.e. a benchmark-agnostic, idiosyncratic stock picker.
    if adj_r2 is not None and not pd.isna(adj_r2) and adj_r2 < 0.70:
        activeness = "benchmark-agnostic stock picker"
    else:
        activeness = "factor-disciplined"
    return {
        "size": size,
        "value_growth": value,
        "momentum": momentum,
        "market_posture": posture,
        "activeness": activeness,
    }


def manager_quant_profile(manager_id: int) -> dict[str, Any] | None:
    """Tenure-weighted Carhart loadings + shrunk alpha for one manager."""
    rows = read_sql(
        """
        SELECT mt.scheme_code, mt.start_date, mt.end_date,
               ar.alpha_annualized, ar.alpha_tstat, ar.adj_r2,
               ar.beta_mkt, ar.beta_smb, ar.beta_hml, ar.beta_wml,
               ar.observations, ar.ir_practitioner, ar.ir_classification
        FROM manager_tenure mt
        JOIN attribution_results ar
          ON ar.scheme_code = mt.scheme_code
         AND ar.window_type = 'pre' AND ar.model_status = 'ok'
        WHERE mt.manager_id = ?
        """,
        (manager_id,),
    )
    if rows.empty:
        return None
    rows = rows.drop_duplicates(subset=["scheme_code"], keep="first").copy()
    start = pd.to_datetime(rows["start_date"], errors="coerce")
    end = pd.to_datetime(rows["end_date"], errors="coerce").fillna(pd.Timestamp.utcnow().tz_localize(None))
    rows["months"] = ((end - start).dt.days / 30.44).clip(lower=1.0).fillna(12.0)
    w = rows["months"] / rows["months"].sum()

    def wavg(col: str) -> float | None:
        vals = pd.to_numeric(rows[col], errors="coerce")
        mask = vals.notna()
        if not mask.any():
            return None
        return float((vals[mask] * w[mask]).sum() / w[mask].sum())

    alpha = wavg("alpha_annualized")
    tstat = wavg("alpha_tstat")
    profile = {
        "manager_id": manager_id,
        "schemes_covered": int(len(rows)),
        "total_obs": int(pd.to_numeric(rows["observations"], errors="coerce").fillna(0).sum()),
        "alpha_annualized": alpha,
        "alpha_tstat": tstat,
        "alpha_shrunk": shrink_alpha(alpha, tstat),
        "beta_mkt": wavg("beta_mkt"),
        "beta_smb": wavg("beta_smb"),
        "beta_hml": wavg("beta_hml"),
        "beta_wml": wavg("beta_wml"),
        "adj_r2": wavg("adj_r2"),
        "ir_practitioner": wavg("ir_practitioner"),
    }
    profile["style"] = style_from_loadings(
        profile["beta_mkt"], profile["beta_smb"], profile["beta_hml"],
        profile["beta_wml"], profile["adj_r2"],
    )
    return profile


# ── Persistence + narrative ─────────────────────────────────────────


def _derived_style_label(style: dict[str, str]) -> str:
    bits = [style["size"], style["value_growth"]]
    if style["momentum"] != "momentum-neutral":
        bits.append(style["momentum"])
    return ", ".join(b for b in bits if b and b != "unclassified") or "insufficient data"


def refresh_derived_assessments() -> int:
    """Write/refresh derived rows for every manager with attribution coverage.

    Curated rows (curated=1) keep their human-verified text; only their
    derived_json snapshot is refreshed.
    """
    managers = read_sql("SELECT manager_id, canonical_name FROM manager_identity")
    count = 0
    for _, m in managers.iterrows():
        manager_id = int(m["manager_id"])
        profile = manager_quant_profile(manager_id)
        if profile is None:
            continue
        derived = json.dumps(profile, default=str)
        label = _derived_style_label(profile["style"])
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT curated FROM manager_qualitative WHERE manager_id=?", (manager_id,)
            ).fetchone()
            if existing and int(existing["curated"] or 0) == 1:
                conn.execute(
                    "UPDATE manager_qualitative SET derived_json=?, updated_at=datetime('now') WHERE manager_id=?",
                    (derived, manager_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO manager_qualitative
                        (manager_id, canonical_name, style_label, aggression, style_summary,
                         investment_approach, transition_note, curated, sources_json, derived_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, datetime('now'))
                    ON CONFLICT(manager_id) DO UPDATE SET
                        style_label=excluded.style_label,
                        aggression=excluded.aggression,
                        style_summary=excluded.style_summary,
                        derived_json=excluded.derived_json,
                        updated_at=datetime('now')
                    """,
                    (
                        manager_id,
                        m["canonical_name"],
                        label,
                        _aggression_from_profile(profile),
                        f"Style derived from Carhart factor loadings over {profile['schemes_covered']} scheme tenure(s): {label}; {profile['style']['market_posture']}; {profile['style']['activeness']}.",
                        None,
                        None,
                        json.dumps(["derived: Carhart 4-factor loadings (Kairos attribution engine)"]),
                        derived,
                    ),
                )
        count += 1
    return count


def _aggression_from_profile(profile: dict[str, Any]) -> str:
    beta = profile.get("beta_mkt")
    smb = profile.get("beta_smb")
    score = 0
    if beta is not None and beta >= 1.10:
        score += 1
    if smb is not None and smb >= 0.35:
        score += 1
    if profile.get("adj_r2") is not None and profile["adj_r2"] < 0.70:
        score += 1
    return {0: "conservative", 1: "balanced", 2: "aggressive", 3: "aggressive"}[score]


def get_assessment(manager_id: int | None = None, manager_name: str | None = None) -> dict[str, Any] | None:
    """Full qualitative + quantitative assessment for one manager."""
    if manager_id is None and manager_name:
        row = read_sql(
            """
            SELECT manager_id FROM manager_identity WHERE canonical_name=?
            UNION
            SELECT manager_id FROM manager_alias WHERE alias_name=?
            LIMIT 1
            """,
            (manager_name, manager_name),
        )
        if row.empty:
            return None
        manager_id = int(row.iloc[0]["manager_id"])
    if manager_id is None:
        return None
    qual = read_sql("SELECT * FROM manager_qualitative WHERE manager_id=?", (manager_id,))
    if qual.empty:
        return None
    record = qual.iloc[0].to_dict()
    for key in ("sources_json", "derived_json"):
        try:
            record[key.replace("_json", "")] = json.loads(record.pop(key) or "null")
        except Exception:
            record[key.replace("_json", "")] = None
    record["curated"] = bool(record.get("curated"))
    return record


def transition_impact_text(manager_name: str, direction: str = "departing",
                           scheme_name: str | None = None) -> dict[str, Any]:
    """Expert-style readout of what a manager change means for an investor.

    Combines the shrunk-alpha estimate (quantitative) with the style profile
    (qualitative). `direction` is 'departing' or 'incoming'.
    """
    assessment = get_assessment(manager_name=manager_name)
    scheme_bit = f" of {scheme_name}" if scheme_name else ""
    if assessment is None:
        return {
            "text": (
                f"{manager_name} ({direction}{scheme_bit}): no attribution history in the Kairos "
                "universe — impact cannot be quantified. Treat the transition as elevated "
                "uncertainty and monitor the first 12 months of successor performance."
            ),
            "assessment": None,
        }
    derived = assessment.get("derived") or {}
    alpha_s = derived.get("alpha_shrunk")
    tstat = derived.get("alpha_tstat")
    style = assessment.get("style_label") or "unclassified style"
    aggression = assessment.get("aggression") or "unknown"
    parts: list[str] = []
    if alpha_s is not None:
        confidence = "statistically robust" if (tstat or 0) >= 2 else "statistically tentative"
        parts.append(
            f"{manager_name} carries a shrinkage-adjusted alpha of {alpha_s * 100:+.2f}%/yr "
            f"({confidence}, t={tstat:.2f})." if tstat is not None else
            f"{manager_name} carries a shrinkage-adjusted alpha of {alpha_s * 100:+.2f}%/yr."
        )
    parts.append(f"Style: {style}; risk posture: {aggression}.")
    if assessment.get("curated") and assessment.get("style_summary"):
        parts.append(assessment["style_summary"])
    if direction == "departing":
        if alpha_s is not None and alpha_s > 0.005:
            parts.append(
                f"Departure{scheme_bit} puts that alpha at risk: expect reversion toward the "
                "factor benchmark unless the successor demonstrates a comparable record. "
                "Watch for style drift in the first two quarters."
            )
        else:
            parts.append(
                f"Departure{scheme_bit} is low-alpha-risk on the numbers; the larger risk is a "
                "style change under the successor (size/value tilts repositioning the fund)."
            )
    else:
        parts.append(
            f"Arrival{scheme_bit}: expect the fund's factor profile to drift toward this "
            f"manager's historical tilts ({style}). Re-check portfolio overlap once the first "
            "two portfolio disclosures are out."
        )
    if assessment.get("transition_note"):
        parts.append(assessment["transition_note"])
    return {"text": " ".join(parts), "assessment": assessment}
