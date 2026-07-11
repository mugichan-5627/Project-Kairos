from __future__ import annotations

import hashlib
import html
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import pandas as pd

from src.utils.db import get_connection, read_sql


@dataclass
class SchemeMatch:
    scheme_code: str | None
    scheme_name: str | None
    amc_name: str | None
    score: float
    status: str


def _norm(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower().replace("-", " "))
    return " ".join(cleaned.split())


# Share-class / plan words identify a variant of a scheme, not the scheme itself.
# "Quant Small Cap Fund - Growth Plan": identity = {quant, small, cap};
# share-class = {growth, plan}. Matching on identity prevents a scheme literally
# named "Growth" from hijacking every growth-plan query.
_SHARE_CLASS_TOKENS = {
    "plan", "option", "direct", "regular", "growth", "idcw", "dividend",
    "reinvestment", "payout", "bonus", "cumulative",
}
_GENERIC_TOKENS = {"fund", "scheme", "mutual", "the", "of", "an", "a"}
# Words that CAN name a fund ("Nippon India Growth Fund", "Value Fund") —
# they count as identity when they appear BEFORE the word "fund", and as
# share-class noise after it ("... Fund - Growth Plan").
_AMBIGUOUS_TOKENS = {"growth", "value", "dividend"}


def _identity_tokens(normalized: str) -> set[str]:
    tokens = normalized.split()
    try:
        fund_pos = tokens.index("fund")
    except ValueError:
        fund_pos = len(tokens)
    out: set[str] = set()
    for pos, tok in enumerate(tokens):
        if tok in _GENERIC_TOKENS:
            continue
        if tok in _SHARE_CLASS_TOKENS and not (tok in _AMBIGUOUS_TOKENS and pos < fund_pos):
            continue
        out.add(tok)
    return out


# Verified scheme renames (old marketing name -> current AMFI name fragment).
# Only entries confirmed against the live scheme master belong here.
_RENAME_ALIASES = {
    "axis bluechip": "axis large cap",           # renamed 2024 (SEBI categorization)
    "parag parikh long term value": "parag parikh flexi cap",
    "hdfc prudence": "hdfc balanced advantage",  # merged 2018
    "hdfc top 200": "hdfc top 100",
}


def fuzzy_match_scheme(query: str, min_score: float = 0.65) -> SchemeMatch:
    if not query:
        return SchemeMatch(None, None, None, 0.0, "empty_query")
    normalized_query = _norm(query)
    for old, new in _RENAME_ALIASES.items():
        if old in normalized_query:
            query = normalized_query.replace(old, new)
            break

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT scheme_code, scheme_name, amc_name FROM scheme_master")
        schemes = cursor.fetchall()

    if not schemes:
        return SchemeMatch(None, None, None, 0.0, "scheme_master_empty")

    q = _norm(query)
    q_identity = _identity_tokens(q)
    q_share = set(q.split()) & _SHARE_CLASS_TOKENS
    if not q_identity:
        return SchemeMatch(None, None, None, 0.0, "empty_query")

    nav_covered: set[str] = set()
    try:
        nav_covered = {
            str(r["scheme_code"])
            for r in read_sql("SELECT DISTINCT scheme_code FROM nav_history").to_dict("records")
        }
    except Exception:
        pass

    best = None
    best_key: tuple = (-1.0,)
    best_score = 0.0
    for row in schemes:
        name = _norm(row["scheme_name"])
        amc = _norm(row["amc_name"])
        # Identity must be extracted per-string: the AMC text ("X Mutual Fund")
        # contains "fund", which would corrupt the positional growth/value rule
        # if the two strings were concatenated first.
        c_identity = _identity_tokens(name) | _identity_tokens(amc)
        if not c_identity:
            continue  # junk rows ("Growth", "Direct Plan", ...) can never match

        shared = q_identity & c_identity
        if not shared:
            continue
        # How much of what the user typed is present in the candidate, and how
        # focused the candidate is on what the user typed.
        coverage_query = len(shared) / len(q_identity)
        coverage_cand = len(shared) / len(c_identity)
        if coverage_query < 0.70 or (len(q_identity) >= 2 and len(shared) < 2):
            continue
        # A short query fully contained in a much longer, unfocused name
        # ("Kotak Emerging Equity" ⊂ "Kotak Global Emerging Market Overseas
        # Equity Omni FOF") is usually the WRONG fund — reject, don't guess.
        if len(c_identity) >= 2 * len(q_identity) + 2 and coverage_cand < 0.5:
            continue

        score = 0.65 * coverage_query + 0.25 * coverage_cand
        # Share-class alignment as a mild tiebreaker, never a substitute for identity.
        c_share = set(name.split()) & _SHARE_CLASS_TOKENS
        if q_share:
            share_hit = len(q_share & c_share) / len(q_share)
            score += 0.10 * share_hit
        else:
            score += 0.05  # nothing requested; neutral
        # Word-order / typo similarity refinement.
        score = 0.85 * score + 0.15 * SequenceMatcher(None, q, f"{amc} {name}".strip()).ratio()

        key = (
            round(score, 6),
            1 if str(row["scheme_code"]) in nav_covered else 0,  # prefer analytics-capable
            1 if "direct" not in name.split() else 0,            # default to regular plan
            -len(c_identity),                                    # prefer tighter names
        )
        if key > best_key:
            best_key = key
            best = row
            best_score = score

    if best is None or best_score < min_score:
        return SchemeMatch(None, None, None, round(best_score, 4), "unmatched")

    return SchemeMatch(str(best["scheme_code"]), best["scheme_name"], best["amc_name"], round(best_score, 4), "matched")


def register_portfolio_rows(
    investor_email: str,
    holdings: list[dict],
    whatsapp_number: str | None = None,
) -> dict:
    stored: list[dict] = []
    unmatched: list[dict] = []
    with get_connection() as conn:
        for item in holdings:
            raw_name = str(item.get("scheme_name") or item.get("query") or "").strip()
            match = fuzzy_match_scheme(raw_name)
            amount = item.get("invested_amount", item.get("amount"))
            units = item.get("units_held")
            row = {
                "query": raw_name,
                "scheme_code": match.scheme_code,
                "scheme_name": match.scheme_name,
                "amc_name": match.amc_name,
                "match_score": match.score,
                "match_status": match.status,
                "invested_amount": amount,
                "units_held": units,
            }
            if match.status != "matched":
                unmatched.append(row)
                continue
            cur = conn.execute(
                """
                INSERT INTO investor_portfolios
                (investor_email, whatsapp_number, scheme_code, scheme_name, invested_amount, units_held, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    investor_email,
                    whatsapp_number,
                    match.scheme_code,
                    match.scheme_name,
                    float(amount) if amount not in (None, "") else None,
                    float(units) if units not in (None, "") else None,
                ),
            )
            row["portfolio_id"] = int(cur.lastrowid)
            stored.append(row)
    return {"stored": stored, "unmatched": unmatched}


def list_portfolio(investor_email: str) -> list[dict]:
    rows = read_sql(
        """
        SELECT portfolio_id, investor_email, whatsapp_number, scheme_code, scheme_name,
               invested_amount, units_held, added_at, active
        FROM investor_portfolios
        WHERE investor_email=?
        ORDER BY active DESC, added_at DESC
        """,
        (investor_email,),
    )
    return rows.to_dict("records")


def set_portfolio_active(portfolio_id: int, active: int = 0) -> dict:
    with get_connection() as conn:
        conn.execute("UPDATE investor_portfolios SET active=? WHERE portfolio_id=?", (int(active), int(portfolio_id)))
    return {"portfolio_id": portfolio_id, "active": int(active)}


def unsubscribe_email(investor_email: str) -> dict:
    with get_connection() as conn:
        cur = conn.execute("UPDATE investor_portfolios SET active=0 WHERE investor_email=?", (investor_email,))
    return {"investor_email": investor_email, "deactivated_rows": cur.rowcount}


def _resend_config() -> dict:
    return {
        "api_key": os.getenv("RESEND_API_KEY") or os.getenv("KAIROS_RESEND_API_KEY") or "",
        # Resend free tier requires the onboarding sender unless a domain is verified.
        "from_address": os.getenv("RESEND_FROM_ADDRESS")
        or os.getenv("KAIROS_FROM_EMAIL")
        or "Project Kairos <onboarding@resend.dev>",
    }


def _send_via_resend(recipient: str, subject: str, html: str) -> dict:
    """Deliver via the Resend HTTP API (https://resend.com). Serverless-friendly:
    plain HTTPS, no SMTP socket, works on Vercel without extra dependencies."""
    import requests

    cfg = _resend_config()
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        json={
            "from": cfg["from_address"],
            "to": [recipient],
            "subject": subject,
            "html": html,
        },
        timeout=15,
    )
    if response.status_code in (200, 201):
        return {"ok": True, "id": response.json().get("id"), "error": None}
    return {"ok": False, "id": None, "error": f"Resend HTTP {response.status_code}: {response.text[:300]}"}


def _smtp_config() -> dict:
    return {
        "host": os.getenv("SMTP_HOST") or os.getenv("KAIROS_SMTP_SERVER") or os.getenv("KAIROS_SMTP_HOST") or "",
        "port": int(os.getenv("SMTP_PORT") or os.getenv("KAIROS_SMTP_PORT") or "587"),
        "user": os.getenv("SMTP_USER") or os.getenv("KAIROS_SMTP_USER") or "",
        "password": os.getenv("SMTP_PASSWORD") or os.getenv("KAIROS_SMTP_PASSWORD") or "",
        "from_address": os.getenv("SMTP_FROM_ADDRESS") or os.getenv("KAIROS_FROM_EMAIL") or os.getenv("SMTP_USER") or os.getenv("KAIROS_SMTP_USER") or "alerts@projectkairos.local",
        "starttls": os.getenv("SMTP_STARTTLS", os.getenv("KAIROS_SMTP_STARTTLS", "true")).lower() == "true",
    }


def smtp_status() -> dict:
    cfg = _smtp_config()
    resend = _resend_config()
    missing = []
    if not cfg["host"]:
        missing.append("SMTP_HOST")
    if not cfg["user"]:
        missing.append("SMTP_USER")
    if not cfg["password"]:
        missing.append("SMTP_PASSWORD")
    smtp_configured = not missing
    resend_configured = bool(resend["api_key"])
    configured = resend_configured or smtp_configured
    if resend_configured:
        delivery_mode = "resend"
    elif smtp_configured:
        delivery_mode = "smtp"
    else:
        delivery_mode = "local_preview"
    return {
        "configured": configured,
        "delivery_mode": delivery_mode,
        "resend_configured": resend_configured,
        "smtp_configured": smtp_configured,
        "host_configured": bool(cfg["host"]),
        "auth_configured": bool(cfg["user"] and cfg["password"]),
        "from_address_configured": bool(cfg["from_address"]),
        "missing": [] if resend_configured else missing,
        "starttls": cfg["starttls"],
    }


def _h(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _money(value) -> str:
    try:
        amount = float(value)
    except Exception:
        return ""
    return "Rs {:,.0f}".format(amount)


def _pct_bps(value) -> str:
    try:
        return f"{float(value) * 10000:+.0f} bps"
    except Exception:
        return "n/a"


def _severity_and_recommendation(forecast: dict) -> tuple[str, str, str]:
    rec = forecast.get("recommendation") or "MONITOR"
    p10 = forecast.get("nav_impact_12m_p10")
    p50 = forecast.get("nav_impact_12m_p50")
    try:
        if p10 is not None and float(p10) < -0.02:
            return "CRITICAL", "REVIEW FOR EXIT", "#ef4444"
        if p50 is not None and float(p50) < -0.01:
            return "ALERT", "MONITOR", "#f59e0b"
    except Exception:
        pass
    return "WATCH", rec if rec in {"HOLD", "MONITOR", "REVIEW FOR EXIT"} else "MONITOR", "#10b981"


def build_investor_alert_email(portfolio: dict, event: dict, forecast: dict, evidence: dict | None = None) -> str:
    severity, recommendation, color = _severity_and_recommendation(forecast)
    amount_line = ""
    if portfolio.get("invested_amount") is not None:
        amount_line = f"<p><strong>Invested amount:</strong> {_money(portfolio.get('invested_amount'))}</p>"
    p10 = _pct_bps(forecast.get("nav_impact_12m_p10"))
    p90 = _pct_bps(forecast.get("nav_impact_12m_p90"))
    source_url = _h((evidence or {}).get("source_url") or "#")
    public_base = os.getenv("KAIROS_PUBLIC_BASE_URL", "").rstrip("/")
    unsub = _h(
        f"{public_base}/api/investor_portfolio?action=unsubscribe&email={quote(str(portfolio.get('investor_email') or ''))}"
        if public_base
        else "#"
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Project Kairos Manager Transition Alert</title></head>
<body style="margin:0;background:#0f172a;color:#e5e7eb;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:24px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#111827;border:1px solid #334155;border-radius:10px;overflow:hidden;">
        <tr><td style="background:#061525;padding:24px;text-align:center;">
          <div style="color:#fff;font-weight:800;letter-spacing:.14em;font-size:20px;">PROJECT KAIROS</div>
          <div style="color:#2dd4bf;margin-top:6px;font-size:13px;">Manager Transition Alert</div>
        </td></tr>
        <tr><td style="padding:24px;line-height:1.55;">
          <h2 style="margin:0 0 12px;color:#f8fafc;">What happened</h2>
          <p><strong>Fund:</strong> {_h(event.get('scheme_name') or portfolio.get('scheme_name') or event.get('scheme_code'))}</p>
          <p><strong>Manager:</strong> {_h(event.get('manager_name') or event.get('departing_manager') or 'Under review')}</p>
          <p><strong>Departure type:</strong> {_h(event.get('change_type') or event.get('claim_type') or 'manager_exit')}</p>
          <p><strong>Source:</strong> <a href="{source_url}" style="color:#60a5fa;">Open evidence</a></p>
          <hr style="border:0;border-top:1px solid #334155;margin:22px 0;">
          <h2 style="margin:0 0 12px;color:#f8fafc;">Impact on your portfolio</h2>
          {amount_line}
          <p><strong>Estimated alpha impact:</strong> {p10} to {p90} over next 12M</p>
          <p><strong>Severity:</strong> <span style="color:{color};font-weight:800;">{severity}</span></p>
          <p><strong>Recommendation:</strong> {_h(recommendation)}</p>
          <hr style="border:0;border-top:1px solid #334155;margin:22px 0;">
          <h2 style="margin:0 0 12px;color:#f8fafc;">About this signal</h2>
          <p>This is a quantitative signal based on Carhart four-factor attribution, factor-matched peer controls, and transition impact analysis. It is a risk-monitoring alert, not financial advice or a guarantee of future returns.</p>
        </td></tr>
        <tr><td style="background:#061525;padding:18px;text-align:center;color:#94a3b8;font-size:12px;line-height:1.5;">
          Project Kairos provides research signals only. Consult a certified advisor before making investment decisions.<br>
          <a href="{unsub}" style="color:#2dd4bf;">Unsubscribe from these alerts</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_html_email(recipient: str, subject: str, html: str) -> dict:
    # Always create a local copy of the email for verification and offline preview
    import re
    from pathlib import Path
    
    clean_subject = re.sub(r'[^a-zA-Z0-9_-]', '_', subject)
    filename = f"alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{clean_subject}.html"
    
    # Place it inside a workspace folder 'sent_emails'
    sent_dir = Path("sent_emails")
    try:
        sent_dir.mkdir(parents=True, exist_ok=True)
        file_path = sent_dir / filename
        file_path.write_text(html, encoding="utf-8")
        local_path_str = str(file_path.resolve())
    except Exception as e:
        local_path_str = f"Error saving file: {e}"

    cfg = _smtp_config()
    status = smtp_status()

    # ── Delivery priority: Resend HTTP API → SMTP → local preview ──
    resend_error: str | None = None
    if status["resend_configured"]:
        result = _send_via_resend(recipient, subject, html)
        if result["ok"]:
            return {
                "delivery_status": "sent",
                "method": "resend",
                "message_id": result["id"],
                "path": local_path_str,
                "error_message": None,
                "smtp_status": status,
            }
        resend_error = result["error"]

    if not status["smtp_configured"]:
        # No working provider - save preview to disk instead
        detail = resend_error or "Neither RESEND_API_KEY nor SMTP is configured"
        return {
            "delivery_status": "failed" if resend_error else "preview_saved",
            "method": "local_file",
            "path": local_path_str,
            "error_message": f"{detail}; copy saved to local file.",
            "smtp_status": status,
        }

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_address"]
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            if cfg["starttls"]:
                server.starttls()
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_address"], [recipient], msg.as_string())
        return {
            "delivery_status": "sent",
            "method": "smtp",
            "path": local_path_str,
            "error_message": None,
            "smtp_status": status,
        }
    except Exception as exc:
        return {
            "delivery_status": "failed",
            "method": "local_file",
            "path": local_path_str,
            "error_message": f"SMTP failed ({exc}). Copy saved to local file.",
            "smtp_status": status,
        }


def _recent_transition_rows(days: int = 7) -> pd.DataFrame:
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return read_sql(
        """
        SELECT ce.event_id, ce.scheme_code, sm.scheme_name, ce.manager_name, ce.manager_key,
               ce.change_type, ce.change_date, ce.category, ce.amc_name,
               tif.expected_alpha_change, tif.nav_impact_12m_p10, tif.nav_impact_12m_p50,
               tif.nav_impact_12m_p90, tif.recommendation
        FROM change_events ce
        LEFT JOIN scheme_master sm ON sm.scheme_code=ce.scheme_code
        LEFT JOIN transition_impact_forecasts tif ON tif.event_id=ce.event_id
        WHERE ce.change_type IN ('Full Exit','AMC Switch','manager_exit','amc_switch')
          AND (ce.created_at >= ? OR ce.change_date >= date('now','-7 day'))
        ORDER BY ce.created_at DESC
        """,
        (since,),
    )


def _evidence_for_manager(manager_name: str | None) -> dict | None:
    if not manager_name:
        return None
    rows = read_sql(
        """
        SELECT title, source_url, source_name
        FROM source_evidence
        WHERE source_url IS NOT NULL AND (title LIKE ? OR snippet LIKE ?)
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (f"%{manager_name}%", f"%{manager_name}%"),
    )
    return None if rows.empty else rows.iloc[0].to_dict()


def run_investor_alert_scan(days: int = 7) -> dict:
    portfolios = read_sql("SELECT * FROM investor_portfolios WHERE active=1")
    transitions = _recent_transition_rows(days=days)
    summary = {
        "portfolios": len(portfolios),
        "transitions": len(transitions),
        "sent": 0,
        "preview_saved": 0,
        "failed": 0,
        "skipped_duplicate": 0,
        "delivery_mode": smtp_status()["delivery_mode"],
    }
    if portfolios.empty or transitions.empty:
        return summary
    with get_connection() as conn:
        for _, portfolio in portfolios.iterrows():
            matches = transitions[transitions["scheme_code"].astype(str) == str(portfolio["scheme_code"])]
            for _, event in matches.iterrows():
                manager_id = str(event.get("manager_key") or event.get("manager_name") or "unknown")
                dedupe = hashlib.sha256(
                    f"{portfolio['investor_email']}|{portfolio['scheme_code']}|{manager_id}".encode("utf-8")
                ).hexdigest()
                dup = conn.execute(
                    """
                    SELECT 1 FROM alert_log
                    WHERE dedupe_key=? AND created_at >= datetime('now','-30 day')
                    LIMIT 1
                    """,
                    (dedupe,),
                ).fetchone()
                if dup:
                    status = "skipped_duplicate"
                    error = None
                    summary["skipped_duplicate"] += 1
                else:
                    evidence = _evidence_for_manager(event.get("manager_name"))
                    html = build_investor_alert_email(portfolio.to_dict(), event.to_dict(), event.to_dict(), evidence)
                    subject = f"[PROJECT KAIROS] {event.get('scheme_name') or portfolio.get('scheme_name')} manager transition"
                    delivered = send_html_email(str(portfolio["investor_email"]), subject, html)
                    status = delivered["delivery_status"]
                    error = delivered["error_message"]
                    if status == "sent":
                        summary["sent"] += 1
                    elif status == "preview_saved":
                        summary["preview_saved"] += 1
                    else:
                        summary["failed"] += 1
                conn.execute(
                    """
                    INSERT INTO alert_log
                    (event_id, recipient, channel, severity, subject, body, delivery_status, dedupe_key,
                     investor_email, scheme_code, manager_id, alert_type, sent_at, error_message)
                    VALUES (?, ?, 'email', ?, ?, ?, ?, ?, ?, ?, ?, 'exit_detected', CURRENT_TIMESTAMP, ?)
                    """,
                    (
                        int(event.get("event_id")) if event.get("event_id") is not None else None,
                        portfolio["investor_email"],
                        _severity_and_recommendation(event.to_dict())[0],
                        f"[PROJECT KAIROS] {event.get('scheme_name') or portfolio.get('scheme_name')} manager transition",
                        None,
                        status,
                        dedupe if status != "skipped_duplicate" else f"{dedupe}:{datetime.utcnow().timestamp()}",
                        portfolio["investor_email"],
                        portfolio["scheme_code"],
                        manager_id,
                        error,
                    ),
                )
    return summary


def send_test_email(recipient: str | None = None) -> dict:
    recipient = (
        recipient
        or os.getenv("KAIROS_ALERT_RECIPIENT")
        or os.getenv("SMTP_FROM_ADDRESS")
        or os.getenv("KAIROS_FROM_EMAIL")
        or os.getenv("SMTP_USER")
    )
    if not recipient:
        return {"delivery_status": "failed", "error_message": "No recipient configured for test email"}
    html = build_investor_alert_email(
        {"investor_email": recipient, "scheme_name": "Kairos Demo Equity Fund", "invested_amount": 250000},
        {"scheme_name": "Kairos Demo Equity Fund", "manager_name": "Demo Manager", "change_type": "Full Exit"},
        {"nav_impact_12m_p10": -0.018, "nav_impact_12m_p90": -0.004, "recommendation": "MONITOR"},
        {"source_url": "https://projectkairos.local"},
    )
    return send_html_email(recipient, "[PROJECT KAIROS] Test manager transition alert", html)
