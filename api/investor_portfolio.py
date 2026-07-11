from __future__ import annotations

from http.server import BaseHTTPRequestHandler

from _kairos import (
    apply_cors,
    bootstrap,
    ensure_scheme_master_loaded,
    query_params,
    read_json_body,
    require_admin,
    send_error,
    send_json,
)
from src.alerts.investor_alerts import (
    list_portfolio,
    register_portfolio_rows,
    send_test_email,
    set_portfolio_active,
    unsubscribe_email,
)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        apply_cors(self)
        self.end_headers()

    def do_GET(self):
        bootstrap()
        params = query_params(self)
        action = params.get("action")
        email = params.get("email")
        if action == "unsubscribe" and email:
            send_json(self, unsubscribe_email(email))
            return
        if not email:
            send_error(
                self,
                code="missing_parameter",
                message="email query parameter is required",
                status=400,
                hint="Pass ?email=<address> to list a portfolio, or ?action=unsubscribe&email=<address> to unsubscribe.",
            )
            return
        send_json(self, {"portfolio": list_portfolio(email)})

    def do_POST(self):
        bootstrap()
        try:
            payload = read_json_body(self)
        except ValueError as exc:
            send_error(self, code="invalid_json", message=str(exc), status=400)
            return
        action = payload.get("action", "register")
        if action == "remove":
            portfolio_id = payload.get("portfolio_id")
            if not portfolio_id:
                send_error(self, code="missing_parameter", message="portfolio_id is required", status=400)
                return
            send_json(self, set_portfolio_active(int(portfolio_id), 0))
            return
        if action == "restore":
            portfolio_id = payload.get("portfolio_id")
            if not portfolio_id:
                send_error(self, code="missing_parameter", message="portfolio_id is required", status=400)
                return
            send_json(self, set_portfolio_active(int(portfolio_id), 1))
            return
        if action == "send_test_email":
            # Outbound email under the project's Resend key - owner only.
            if not require_admin(self):
                return
            send_json(self, send_test_email(payload.get("email")))
            return
        scheme_master_status = ensure_scheme_master_loaded()
        email = payload.get("investor_email") or payload.get("email")
        holdings = payload.get("holdings", [])
        if not email:
            send_error(self, code="missing_parameter", message="investor_email is required", status=400)
            return
        if not isinstance(holdings, list) or not holdings:
            send_error(self, code="invalid_holdings", message="holdings must be a non-empty list", status=400)
            return
        result = register_portfolio_rows(email, holdings, payload.get("whatsapp_number"))
        result["scheme_master_status"] = scheme_master_status
        send_json(self, result)
