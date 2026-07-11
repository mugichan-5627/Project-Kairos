from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from db_setup import initialize_database
from src.analytics.factor_model import FactorModel
from src.analytics.metrics import PerformanceMetrics
from src.data.canonical_manager import import_seed_dataframe, sync_canonical_to_legacy
from src.detection.change_detector import ManagerChangeDetector
from src.intelligence.claim_promotion import promote_claim_to_truth
from src.intelligence.confidence import system_confidence
from src.intelligence.llm_judge import parse_llm_json
from src.alerts.investor_alerts import build_investor_alert_email
from src.utils.db import get_connection, read_sql


def seed_minimal_data() -> None:
    initialize_database()
    rng = np.random.default_rng(7)
    months = pd.date_range("2090-01-31", periods=24, freq="ME")
    days = pd.date_range("2090-01-01", periods=760, freq="D")
    with get_connection() as conn:
        conn.execute("DELETE FROM manager_claims WHERE scheme_code='DEMO1' OR claim_text LIKE '%Kairos Demo%'")
        conn.execute("DELETE FROM source_evidence WHERE query='smoke'")
        conn.execute("DELETE FROM manager_tenure WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM manager_alias WHERE alias_name IN ('Asha Rao','Rohan Mehta')")
        conn.execute("DELETE FROM manager_identity WHERE canonical_name IN ('Asha Rao','Rohan Mehta')")
        conn.execute("DELETE FROM manager_scorecards WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM attribution_results WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM change_events WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM manager_scheme_history WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM scheme_master WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM nav_history WHERE scheme_code='DEMO1'")
        conn.execute("DELETE FROM factor_data WHERE source='smoke'")
        conn.execute(
            """
            INSERT OR REPLACE INTO scheme_master(scheme_code, scheme_name, amc_name, category, sub_category, source)
            VALUES('DEMO1','Kairos Demo Equity Fund','Kairos AMC','Equity','Flexi Cap','smoke')
            """
        )
        nav = 100.0
        for dt in days:
            nav *= 1 + 0.00035 + rng.normal(0, 0.006)
            conn.execute(
                "INSERT OR REPLACE INTO nav_history(scheme_code, nav_date, nav, source) VALUES('DEMO1',?,?, 'smoke')",
                (dt.strftime("%Y-%m-%d"), float(nav)),
            )
        for dt in months:
            vals = rng.normal(0.01, 0.04, size=5)
            conn.execute(
                """
                INSERT OR REPLACE INTO factor_data
                (factor_date,nifty500_return,nifty50_return,smallcap250_return,value50_return,momentum50_return,
                 quality_lowvol30_return,midcap150_return,repo_rate,risk_free_monthly,mkt_rf,smb,hml,wml,qmj,source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'smoke')
                """,
                (
                    dt.strftime("%Y-%m-%d"),
                    vals[0],
                    vals[0] - 0.002,
                    vals[0] + vals[1],
                    vals[0] + vals[2],
                    vals[0] + vals[3],
                    vals[0] + vals[4],
                    vals[0] + vals[1] / 2,
                    0.065,
                    0.065 / 12,
                    vals[0] - 0.065 / 12,
                    vals[1],
                    vals[2],
                    vals[3],
                    vals[4],
                ),
            )


def main() -> None:
    try:
        seed_minimal_data()
        regression = FactorModel().run_regression("DEMO1", "2090-01-01", "2092-01-31")
        assert regression["model_status"] == "ok", regression
        assert "beta_qmj" not in regression, regression
        frame = FactorModel().regression_frame("DEMO1", "2090-01-01", "2092-01-31")
        assert np.allclose(frame["hml"], frame["value50_return"] - frame["nifty500_return"], equal_nan=False)
        nav = read_sql("SELECT nav_date, nav FROM nav_history WHERE scheme_code='DEMO1'")
        metrics = PerformanceMetrics().compute(nav)
        assert metrics["status"] == "ok", metrics
        detector = ManagerChangeDetector()
        history = pd.DataFrame(
            [
                {
                    "scheme_code": "DEMO1",
                    "scheme_name": "Kairos Demo Equity Fund",
                    "amc_name": "Kairos AMC",
                    "manager_name": "Asha Rao",
                    "start_date": "2022-01-01",
                    "end_date": "2023-01-31",
                    "source": "smoke_sid",
                    "confidence_score": 1.0,
                },
                {
                    "scheme_code": "DEMO1",
                    "scheme_name": "Kairos Demo Equity Fund",
                    "amc_name": "Kairos AMC",
                    "manager_name": "Rohan Mehta",
                    "start_date": "2023-02-01",
                    "end_date": None,
                    "source": "smoke_vro",
                    "confidence_score": 0.9,
                },
            ]
        )
        detector.persist_history(history)
        created = detector.refresh_change_events()
        assert created >= 0
        seed_result = import_seed_dataframe(
            pd.DataFrame(
                [
                    {
                        "scheme_code": "DEMO1",
                        "scheme_name": "Kairos Demo Equity Fund",
                        "amc_name": "Kairos AMC",
                        "manager_name": "Asha Rao",
                        "role": "lead",
                        "rank": 1,
                        "start_date": "2090-01-01",
                        "end_date": "2091-01-31",
                        "source": "smoke",
                        "source_type": "sebi_circular",
                        "source_url": "https://example.com/smoke",
                        "confidence_score": 0.9,
                    }
                ]
            )
        )
        assert seed_result["seed_rows"] == 1
        assert system_confidence([{"source_type": "sebi_circular"}]) == 0.9
        parsed = parse_llm_json('{"verdict":"needs_review","confidence":0.4,"reasoning":"weak","extracted_manager_name":null,"extracted_scheme":null,"extracted_amc":null,"extracted_date":null,"claim_type":"manager_related"}')
        assert parsed["verdict"] == "needs_review"
        fenced = parse_llm_json('```json\n{"verdict":"needs_review","confidence":0.4,"reasoning":"weak","extracted_manager_name":null,"extracted_scheme":null,"extracted_amc":null,"extracted_date":null,"claim_type":"manager_related"}\n```')
        assert fenced["claim_type"] == "manager_related"
        email_html = build_investor_alert_email(
            {"investor_email": "demo@example.com", "scheme_name": "Kairos Demo Equity Fund", "invested_amount": None},
            {"scheme_name": "Kairos Demo Equity Fund", "manager_name": "Asha Rao", "change_type": "Full Exit"},
            {"nav_impact_12m_p10": -0.012, "nav_impact_12m_p90": -0.002, "recommendation": "MONITOR"},
        )
        assert "PROJECT KAIROS" in email_html
        assert "Rs None" not in email_html
        try:
            parse_llm_json("not json")
            raise AssertionError("Invalid JSON should not parse")
        except Exception:
            pass
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO source_evidence(source_name, source_type, source_url, query, title, snippet)
                VALUES('smoke','sebi_circular','https://example.com/smoke','smoke','Kairos Demo','Asha Rao exits Kairos Demo Equity Fund')
                """
            )
            evidence_id = int(cur.lastrowid)
            cur = conn.execute(
                """
                INSERT INTO manager_claims(evidence_id, claim_type, manager_name, scheme_code, scheme_name, amc_name,
                 event_date, claim_text, system_confidence, confidence_score, source_type, status)
                VALUES (?, 'manager_exit', 'Asha Rao', 'DEMO1', 'Kairos Demo Equity Fund', 'Kairos AMC',
                 '2091-02-01', 'Asha Rao exits Kairos Demo Equity Fund', 0.9, 0.9, 'sebi_circular', 'pending')
                """,
                (evidence_id,),
            )
            claim_id = int(cur.lastrowid)
        promotion = promote_claim_to_truth(claim_id)
        assert promotion["status"] == "accepted", promotion
        print({"factor_model": regression["model_status"], "metrics": metrics["status"], "change_events_created": created, "promotion": promotion["status"]})
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM manager_claims WHERE scheme_code='DEMO1' OR claim_text LIKE '%Kairos Demo%'")
            conn.execute("DELETE FROM source_evidence WHERE query='smoke'")
            conn.execute("DELETE FROM manager_tenure WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM manager_alias WHERE alias_name IN ('Asha Rao','Rohan Mehta')")
            conn.execute("DELETE FROM manager_identity WHERE canonical_name IN ('Asha Rao','Rohan Mehta')")
            conn.execute("DELETE FROM manager_scorecards WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM attribution_results WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM did_diagnostics WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM change_events WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM manager_scheme_history WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM nav_history WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM scheme_master WHERE scheme_code='DEMO1'")
            conn.execute("DELETE FROM factor_data WHERE source='smoke'")


if __name__ == "__main__":
    main()
