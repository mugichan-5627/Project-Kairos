from __future__ import annotations

import argparse

import pandas as pd

from db_setup import initialize_database
from src.analytics.pipeline_runner import AttributionPipeline
from src.analytics.did_diagnostics import DIDDiagnostics
from src.analytics.factor_matched_did import FactorMatchedDID
from src.analytics.impact_forecast import TransitionImpactForecaster
from src.analytics.portable_alpha import PortableAlphaEngine
from src.alerts.alert_agent import AlertAgent
from src.alerts.investor_alerts import run_investor_alert_scan, send_test_email
from src.config_checks import validate_environment
from src.data.verification import validate_manager_tenures
from src.data.amfi_loader import AMFILoader
from src.data.canonical_manager import (
    SEED_TEMPLATE_PATH,
    STARTER_SEED_PATH,
    backfill_canonical_from_legacy,
    import_seed_dataframe,
    import_starter_seed,
    sync_canonical_to_legacy,
)
from src.data.manager_history_importer import import_manager_history
from src.data.news_monitor import NewsMonitor
from src.data.nse_factors import NSEFactorLoader
from src.data.seed_imports import import_current_manager_seed, import_scheme_lineage_seed
from src.data.rbi_risk_free import RBIRiskFreeLoader
from src.detection.change_detector import ManagerChangeDetector
from src.intelligence.claim_promotion import promote_claim_to_truth, update_claim_status
from src.intelligence.llm_judge import judge_claim
from src.intelligence.update_manager_database import auto_classify_raw_evidence, search_manager_transition_evidence
from src.scoring.scorecard import ManagerScorecard
from src.utils.db import read_sql


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "task",
        choices=[
            "schema",
            "amfi-master",
            "nav",
            "factors",
            "news",
            "detect",
            "attribution",
            "scorecards",
            "import-history",
            "tavily-evidence",
            "classify-evidence",
            "download-seed-template",
            "import-seed",
            "import-starter-seed",
            "sync-canonical-history",
            "load-nse-factors",
            "load-risk-free-rate",
            "judge-claims",
            "promote-claim",
            "reject-claim",
            "run-did-diagnostics",
            "import-current-manager-seed",
            "import-scheme-lineage-seed",
            "monitor",
            "verify-tenures",
            "portable-alpha",
            "factor-matched-did",
            "impact-forecast",
            "create-demo-alerts",
            "env-check",
            "send-test-email",
            "investor-alert-scan",
        ],
    )
    parser.add_argument("--max-schemes", type=int, default=100)
    parser.add_argument("--csv")
    parser.add_argument("--claim-id", type=int)
    args = parser.parse_args()

    initialize_database()
    if args.task == "schema":
        print("schema-ok")
    elif args.task == "amfi-master":
        print(AMFILoader().refresh_scheme_master())
    elif args.task == "nav":
        schemes = read_sql("SELECT scheme_code FROM scheme_master ORDER BY scheme_code LIMIT ?", (args.max_schemes,))
        print(AMFILoader().refresh_nav_history(schemes["scheme_code"].astype(str).tolist()))
    elif args.task == "factors":
        print(NSEFactorLoader().refresh())
    elif args.task == "load-nse-factors":
        print(NSEFactorLoader().refresh())
    elif args.task == "load-risk-free-rate":
        print(RBIRiskFreeLoader().refresh())
    elif args.task == "news":
        print(NewsMonitor().refresh())
    elif args.task == "detect":
        print(ManagerChangeDetector().refresh_change_events())
    elif args.task == "attribution":
        print(AttributionPipeline().refresh_all_events())
    elif args.task == "scorecards":
        print(ManagerScorecard().refresh_all())
    elif args.task == "import-history":
        if not args.csv:
            raise SystemExit("--csv is required")
        print(import_manager_history(pd.read_csv(args.csv)))
    elif args.task == "tavily-evidence":
        print(search_manager_transition_evidence(max_results=8))
    elif args.task == "classify-evidence":
        print(auto_classify_raw_evidence(limit=200))
    elif args.task == "download-seed-template":
        print({"template": str(SEED_TEMPLATE_PATH), "starter_seed": str(STARTER_SEED_PATH)})
    elif args.task == "import-seed":
        if not args.csv:
            raise SystemExit("--csv is required")
        result = import_seed_dataframe(pd.read_csv(args.csv), verified=True)
        result["change_events"] = ManagerChangeDetector().refresh_change_events()
        print(result)
    elif args.task == "import-starter-seed":
        result = import_starter_seed()
        result["change_events"] = ManagerChangeDetector().refresh_change_events()
        print(result)
    elif args.task == "sync-canonical-history":
        print({"backfill": backfill_canonical_from_legacy(), "sync": sync_canonical_to_legacy()})
    elif args.task == "judge-claims":
        if args.claim_id:
            print(judge_claim(args.claim_id))
        else:
            claims = read_sql("SELECT claim_id FROM manager_claims WHERE status IN ('pending','needs_review') LIMIT 25")
            print([judge_claim(int(c)) for c in claims["claim_id"].tolist()])
    elif args.task == "promote-claim":
        if not args.claim_id:
            raise SystemExit("--claim-id is required")
        print(promote_claim_to_truth(args.claim_id))
    elif args.task == "reject-claim":
        if not args.claim_id:
            raise SystemExit("--claim-id is required")
        print(update_claim_status(args.claim_id, "rejected"))
    elif args.task == "run-did-diagnostics":
        print(DIDDiagnostics().refresh_all())
    elif args.task == "import-current-manager-seed":
        print(import_current_manager_seed())
    elif args.task == "import-scheme-lineage-seed":
        print(import_scheme_lineage_seed())
    elif args.task == "monitor":
        from monitor import run_daily_monitor

        print(run_daily_monitor())
    elif args.task == "verify-tenures":
        report = validate_manager_tenures(output_csv="data_quality_report.csv", head_check=False)
        print({"rows": len(report), "output": "data_quality_report.csv"})
    elif args.task == "portable-alpha":
        print(PortableAlphaEngine().refresh_all())
    elif args.task == "factor-matched-did":
        print(FactorMatchedDID().refresh_all())
    elif args.task == "impact-forecast":
        print(TransitionImpactForecaster().refresh_all())
    elif args.task == "create-demo-alerts":
        print(AlertAgent().create_alerts_for_forecasts())
    elif args.task == "env-check":
        print(validate_environment().to_dict())
    elif args.task == "send-test-email":
        print(send_test_email())
    elif args.task == "investor-alert-scan":
        print(run_investor_alert_scan(days=7))


if __name__ == "__main__":
    main()
