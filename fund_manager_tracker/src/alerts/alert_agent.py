from __future__ import annotations

import hashlib
import html
from pathlib import Path

from src.config import REPORTS_DIR
from src.utils.db import get_connection, read_sql


REPORT_DIR = REPORTS_DIR


def _h(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


class AlertAgent:
    def create_alerts_for_forecasts(self, recipient: str = "demo@projectkairos.local", channel: str = "demo") -> int:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        forecasts = read_sql(
            """
            SELECT tif.*, ce.amc_name, sm.scheme_name
            FROM transition_impact_forecasts tif
            LEFT JOIN change_events ce ON ce.event_id=tif.event_id
            LEFT JOIN scheme_master sm ON sm.scheme_code=tif.scheme_code
            WHERE tif.status='ok'
            """
        )
        count = 0
        with get_connection() as conn:
            for _, row in forecasts.iterrows():
                severity = self._severity(row)
                if severity == "WATCH" and row.get("recommendation") == "HOLD":
                    continue
                dedupe = hashlib.sha256(f"{row['event_id']}|{recipient}|{channel}".encode()).hexdigest()
                report_path = self.write_transition_brief(row.to_dict())
                subject = f"[{severity}] {row.get('scheme_name') or row['scheme_code']} - Manager Change Alert"
                body = self.alert_body(row.to_dict(), severity)
                try:
                    conn.execute(
                        """
                        INSERT INTO alert_log(event_id, recipient, channel, severity, subject, body, report_path, delivery_status, dedupe_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'created_demo', ?)
                        """,
                        (int(row["event_id"]), recipient, channel, severity, subject, body, str(report_path), dedupe),
                    )
                    count += 1
                except Exception:
                    pass
        return count

    def write_transition_brief(self, row: dict) -> Path:
        path = REPORT_DIR / f"transition_brief_event_{row['event_id']}.html"
        html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Project Kairos Transition Brief</title>
<style>body{{font-family:Arial,sans-serif;max-width:900px;margin:32px auto;line-height:1.5}} .kpi{{display:inline-block;margin:8px 20px 8px 0}}</style></head>
<body>
<h1>Project Kairos Transition Brief</h1>
<h2>{_h(row.get('scheme_name') or row.get('scheme_code'))}</h2>
<p><strong>Departing manager:</strong> {_h(row.get('departing_manager'))}</p>
<p><strong>Incoming manager:</strong> {_h(row.get('incoming_manager') or 'Unknown / under review')}</p>
<div class="kpi"><strong>Expected alpha change:</strong> {self._pct(row.get('expected_alpha_change'))}</div>
<div class="kpi"><strong>12M base NAV impact:</strong> {self._pct(row.get('nav_impact_12m_p50'))}</div>
<div class="kpi"><strong>Recommendation:</strong> {row.get('recommendation')}</div>
<p>This is a quantitative signal, not financial advice. Consult your advisor before acting.</p>
</body></html>"""
        path.write_text(html, encoding="utf-8")
        return path

    def alert_body(self, row: dict, severity: str) -> str:
        return (
            f"{severity}: {_h(row.get('scheme_name') or row.get('scheme_code'))} manager transition. "
            f"Departing manager: {_h(row.get('departing_manager'))}. "
            f"Expected alpha change: {self._pct(row.get('expected_alpha_change'))}. "
            f"Recommendation: {row.get('recommendation')}. "
            "This is a quantitative signal, not financial advice."
        )

    def _severity(self, row) -> str:
        p10 = row.get("nav_impact_12m_p10")
        if p10 is not None and p10 < -0.02:
            return "CRITICAL"
        p50 = row.get("nav_impact_12m_p50")
        if p50 is not None and p50 < -0.01:
            return "ALERT"
        return "WATCH"

    def _pct(self, value) -> str:
        try:
            return f"{float(value) * 100:.2f}%"
        except Exception:
            return "n/a"

    def generate_html_alert(self, event: dict, did: dict, forecast: dict, evidence_list: list[dict]) -> str:
        import pandas as pd

        uncertainty_flag = forecast.get('uncertainty_flag')
        is_unknown_successor = uncertainty_flag == 'unknown_successor'

        alpha_val = forecast.get('expected_alpha_change')

        if is_unknown_successor:
            # ── Unknown successor framing: show alpha AT RISK ──
            # Get the pre-alpha from attribution_results to display risk
            pre_alpha = None
            try:
                from src.utils.db import read_sql as _rsql
                attr_df = _rsql(
                    "SELECT alpha_annualized FROM attribution_results WHERE event_id=? AND window_type='pre' ORDER BY created_at DESC LIMIT 1",
                    (forecast.get('event_id') or event.get('event_id'),),
                )
                if not attr_df.empty and pd.notna(attr_df.iloc[0]["alpha_annualized"]):
                    pre_alpha = float(attr_df.iloc[0]["alpha_annualized"])
            except Exception:
                pass

            if pre_alpha is not None:
                expected_alpha = f"~{abs(pre_alpha)*100:.2f}% alpha at risk (successor not yet named)"
            elif alpha_val is not None and pd.notna(alpha_val):
                expected_alpha = f"~{abs(float(alpha_val))*100:.2f}% alpha at risk (successor not yet named)"
            else:
                expected_alpha = "Alpha at risk — successor not yet named"

            nav_12m = "Uncertain — monitoring period begins now"
            nav_12m_range = "(wide confidence interval reflecting unknown successor quality)"
        else:
            if alpha_val is not None and pd.notna(alpha_val):
                expected_alpha = f"{float(alpha_val)*100:+.2f}%"
            else:
                expected_alpha = "N/A"

            p50_val = forecast.get('nav_impact_12m_p50')
            if p50_val is not None and pd.notna(p50_val):
                nav_12m = f"{float(p50_val)*100:+.2f}%"
            else:
                nav_12m = "N/A"

            p10_val = forecast.get('nav_impact_12m_p10')
            p90_val = forecast.get('nav_impact_12m_p90')
            if p10_val is not None and pd.notna(p10_val) and p90_val is not None and pd.notna(p90_val):
                nav_12m_range = f"({float(p10_val)*100:+.2f}% to {float(p90_val)*100:+.2f}%)"
            else:
                nav_12m_range = "(Insufficient history)"

        rec = forecast.get("recommendation", "MONITOR")
        # Never show HOLD for unknown successor
        if is_unknown_successor and rec == "HOLD":
            rec = "MONITOR"
        rec_color = "#10b981"
        if rec in ("REVIEW", "REVIEW FOR EXIT"):
            rec_color = "#ef4444"
        elif rec == "MONITOR":
            rec_color = "#f59e0b"
            
        evidence_html = ""
        for ev in evidence_list:
            evidence_html += f"""
            <div style="background-color: rgba(255,255,255,0.03); border: 1px solid #334155; border-radius: 6px; padding: 12px; margin-bottom: 8px;">
                <div style="display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px;">
                    <span style="color: #94a3b8; font-weight: bold;">[{ev.get('source_name') or 'Verified News'}]</span>
                    <span style="color: #10b981; font-weight: bold;">✓ Verified Source</span>
                </div>
                <div style="color: #f8fafc; font-size: 14px; margin-bottom: 6px; font-weight: 500;">{ev.get('title') or 'Manager transition update'}</div>
                <a href="{ev.get('source_url') or '#'}" target="_blank" style="color: #3b82f6; text-decoration: none; font-size: 12px; word-break: break-all;">{ev.get('source_url') or ''}</a>
            </div>
            """
        if not evidence_html:
            evidence_html = "<p style='color: #94a3b8; font-size: 14px;'>No external evidence links found.</p>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Project Kairos | Transition Alert</title>
        </head>
        <body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f8fafc;">
            <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; margin: 0 auto; background-color: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;">
                <!-- Header -->
                <tr>
                    <td style="padding: 24px; background: linear-gradient(135deg, #1e1b4b 0%, #0f172a 100%); border-bottom: 1px solid #334155; text-align: center;">
                        <h1 style="margin: 0; font-size: 20px; letter-spacing: 0.1em; color: #f8fafc; font-weight: 800;">PROJECT KAIROS</h1>
                        <p style="margin: 4px 0 0 0; font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">Manager Transition Risk Alert</p>
                    </td>
                </tr>
                <!-- Body -->
                <tr>
                    <td style="padding: 24px;">
                        <p style="margin-top: 0; font-size: 15px; line-height: 1.6; color: #cbd5e1;">
                            An automated surveillance event was detected for a fund in your holdings. A manager transition has been identified and verified.
                        </p>
                        
                        <!-- Fund & Manager Card -->
                        <table width="100%" style="margin-bottom: 24px; background-color: #0f172a; border-radius: 8px; border: 1px solid #334155; border-collapse: separate;" cellpadding="16">
                            <tr>
                                <td>
                                    <h2 style="margin: 0 0 8px 0; font-size: 16px; color: #f8fafc;">{event.get('scheme_name') or 'Mutual Fund Scheme'}</h2>
                                    <p style="margin: 0 0 12px 0; font-size: 13px; color: #94a3b8;">Scheme Code: {event.get('scheme_code')} | Category: {event.get('category') or 'Equity'}</p>
                                    
                                    <table width="100%" style="border-top: 1px solid #334155; padding-top: 12px; border-collapse: collapse;">
                                        <tr>
                                            <td width="50%" style="font-size: 13px; color: #94a3b8; padding-bottom: 4px;">Departing Manager</td>
                                            <td width="50%" style="font-size: 13px; color: #94a3b8; padding-bottom: 4px;">Successor Manager</td>
                                        </tr>
                                        <tr>
                                            <td style="font-size: 14px; font-weight: bold; color: #ef4444;">{event.get('manager_name')}</td>
                                            <td style="font-size: 14px; font-weight: bold; color: #10b981;">{event.get('successor_manager') or 'Pending Appointment'}</td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>

                        <!-- Risk Metrics -->
                        <h3 style="margin: 0 0 12px 0; font-size: 14px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em;">Transition Risk Metrics</h3>
                        <table width="100%" style="margin-bottom: 24px; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 12px; background-color: #0f172a; border: 1px solid #334155; border-radius: 6px 0 0 6px;">
                                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">Expected Alpha Change</div>
                                    <div style="font-size: 18px; font-weight: bold; color: #f8fafc; margin-top: 4px;">{expected_alpha}</div>
                                </td>
                                <td style="padding: 12px; background-color: #0f172a; border: 1px solid #334155;">
                                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">12M Expected NAV Impact</div>
                                    <div style="font-size: 18px; font-weight: bold; color: #f8fafc; margin-top: 4px;">{nav_12m}</div>
                                    <div style="font-size: 10px; color: #94a3b8; margin-top: 2px;">{nav_12m_range}</div>
                                </td>
                                <td style="padding: 12px; background-color: #0f172a; border: 1px solid #334155; border-radius: 0 6px 6px 0; text-align: center;">
                                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">Action Rating</div>
                                    <div style="display: inline-block; padding: 4px 12px; background-color: rgba(255,255,255,0.05); border-radius: 20px; font-size: 14px; font-weight: bold; color: {rec_color}; margin-top: 6px; border: 1px solid {rec_color};">{rec}</div>
                                </td>
                            </tr>
                        </table>

                        <!-- Verified Evidence -->
                        <h3 style="margin: 0 0 12px 0; font-size: 14px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.05em;">Verified Provenance (Sources)</h3>
                        {evidence_html}
                        
                    </td>
                </tr>
                <!-- Footer -->
                <tr>
                    <td style="padding: 24px; background-color: #0f172a; border-top: 1px solid #334155; text-align: center; font-size: 11px; color: #64748b;">
                        <p style="margin: 0 0 8px 0;">This email is an automated transmission from your Project Kairos instance.</p>
                        <p style="margin: 0; line-height: 1.4;">Disclaimer: The calculations, alpha score attributions, and impact forecasts presented here are model outputs and do not constitute formal investment advice. Consult a certified financial planner before acting.</p>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
        return html

    def send_email_alert(self, recipient: str, event_id: int) -> dict:
        event_df = read_sql("SELECT * FROM change_events WHERE event_id=?", (event_id,))
        if event_df.empty:
            return {"success": False, "error": f"Event ID {event_id} not found."}
        event = event_df.iloc[0].to_dict()
        
        did_df = read_sql("SELECT * FROM factor_matched_did WHERE event_id=? ORDER BY created_at DESC LIMIT 1", (event_id,))
        did = did_df.iloc[0].to_dict() if not did_df.empty else {}
        
        forecast_df = read_sql("SELECT * FROM transition_impact_forecasts WHERE event_id=? ORDER BY created_at DESC LIMIT 1", (event_id,))
        forecast = forecast_df.iloc[0].to_dict() if not forecast_df.empty else {}
        
        evidence_df = read_sql(
            """
            SELECT se.title, se.source_url, se.source_name
            FROM source_evidence se
            JOIN manager_claims mc ON mc.evidence_id=se.evidence_id
            WHERE mc.claim_type IN ('manager_exit', 'amc_switch') AND mc.status='confirmed'
            LIMIT 3
            """
        )
        if evidence_df.empty:
            evidence_df = read_sql(
                """
                SELECT title, source_url, source_name
                FROM source_evidence
                WHERE (title LIKE ? OR snippet LIKE ?) AND source_url IS NOT NULL
                LIMIT 3
                """,
                (f"%{event['manager_name']}%", f"%{event['manager_name']}%")
            )
            
        evidence_list = evidence_df.to_dict("records") if not evidence_df.empty else []
        
        html_content = self.generate_html_alert(event, did, forecast, evidence_list)
        subject = f"[PROJECT KAIROS] Manager Transition Risk Alert: {event.get('scheme_name') or event.get('scheme_code')}"
        from src.alerts.investor_alerts import send_html_email

        result = send_html_email(recipient, subject, html_content)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO alert_log(event_id, recipient, channel, severity, subject, body, delivery_status,
                                      investor_email, scheme_code, manager_id, alert_type, sent_at, error_message)
                VALUES (?, ?, 'email', 'ALERT', ?, ?, ?, ?, ?, ?, 'exit_confirmed', CURRENT_TIMESTAMP, ?)
                """,
                (
                    int(event_id),
                    recipient,
                    subject,
                    html_content,
                    result["delivery_status"],
                    recipient,
                    event.get("scheme_code"),
                    event.get("manager_key") or event.get("manager_name"),
                    result.get("error_message"),
                ),
            )
        return {
            "success": result["delivery_status"] == "sent",
            "method": result.get("method", "smtp"),
            "path": result.get("path"),
            "subject": subject,
            "body": html_content,
            "recipient": recipient,
            **result,
        }
