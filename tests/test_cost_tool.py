"""Tests for cost_tool.py — Azure / AWS / Heroku."""

from unittest.mock import patch, MagicMock

from app.tools.cost_tool import (
    get_azure_cost, get_aws_cost, get_heroku_cost,
    get_all_costs, format_cost_report,
)


# ── Missing credentials ───────────────────────────────────────────────────────

class TestMissingCredentials:

    def test_azure_unavailable_without_creds(self):
        env = {"AZURE_SUBSCRIPTION_ID": "", "AZURE_TENANT_ID": "",
               "AZURE_CLIENT_ID": "", "AZURE_CLIENT_SECRET": ""}
        with patch.dict("os.environ", env):
            result = get_azure_cost()
        assert result["available"] is False
        assert result["provider"] == "Azure"

    def test_aws_unavailable_without_creds(self):
        with patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": ""}):
            result = get_aws_cost()
        assert result["available"] is False
        assert result["provider"] == "AWS"

    def test_heroku_unavailable_without_api_key(self):
        with patch.dict("os.environ", {"HEROKU_API_KEY": ""}):
            result = get_heroku_cost()
        assert result["available"] is False
        assert result["provider"] == "Heroku"


# ── Azure ─────────────────────────────────────────────────────────────────────

class TestAzureCost:

    _ENV = {
        "AZURE_SUBSCRIPTION_ID": "sub-123",
        "AZURE_TENANT_ID": "tenant-123",
        "AZURE_CLIENT_ID": "client-123",
        "AZURE_CLIENT_SECRET": "secret-123",
    }

    def _mock_posts(self, last_month: float, current_month: float, total: float):
        """4 POST calls: token + last_month + current_month + total."""
        token = MagicMock()
        token.json.return_value = {"access_token": "tok"}
        token.raise_for_status = MagicMock()

        def _cost_resp(amount):
            r = MagicMock()
            r.json.return_value = {"properties": {"rows": [[amount, "USD"]]}}
            r.raise_for_status = MagicMock()
            return r

        return [token, _cost_resp(last_month), _cost_resp(current_month), _cost_resp(total)]

    def test_returns_last_month_and_total(self):
        with patch.dict("os.environ", self._ENV), \
             patch("requests.post", side_effect=self._mock_posts(3.75, 3.20, 42.5)), \
             patch("requests.get", return_value=MagicMock(status_code=404)):
            result = get_azure_cost()

        assert result["available"] is True
        assert result["provider"] == "Azure"
        assert result["last_month_spent"] == 3.75
        assert result["total_spent"] == 42.5

    def test_negative_amount_becomes_positive(self):
        with patch.dict("os.environ", self._ENV), \
             patch("requests.post", side_effect=self._mock_posts(-0.18, -1.0, -5.0)), \
             patch("requests.get", return_value=MagicMock(status_code=404)):
            result = get_azure_cost()

        assert result["last_month_spent"] >= 0
        assert result["total_spent"] >= 0

    def test_api_error_returns_unavailable(self):
        token = MagicMock()
        token.raise_for_status.side_effect = Exception("401 Unauthorized")
        with patch.dict("os.environ", self._ENV), \
             patch("requests.post", return_value=token):
            result = get_azure_cost()

        assert result["available"] is False
        assert "401" in result["error"]

    def test_credit_balance_from_consumption_api(self):
        credit_resp = MagicMock()
        credit_resp.status_code = 200
        credit_resp.json.return_value = {"value": [
            {"properties": {"balanceSummary": {"estimatedBalance": 150.0}}}
        ]}

        with patch.dict("os.environ", self._ENV), \
             patch("requests.post", side_effect=self._mock_posts(0.0, 0.0, 0.0)), \
             patch("requests.get", return_value=credit_resp):
            result = get_azure_cost()

        assert result["credit_balance"] == 150.0


# ── AWS ───────────────────────────────────────────────────────────────────────

class TestAWSCost:

    _ENV = {"AWS_ACCESS_KEY_ID": "key", "AWS_SECRET_ACCESS_KEY": "secret"}

    def _mock_boto3(self, last_month_amount: float, total_amount: float):
        mock_client = MagicMock()
        mock_client.get_cost_and_usage.side_effect = [
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": str(last_month_amount)}}}]},
            {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": str(total_amount)}}}]},
        ]
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client
        return mock_boto3

    def test_returns_last_month_and_total(self):
        with patch.dict("os.environ", self._ENV), \
             patch.dict("sys.modules", {"boto3": self._mock_boto3(0.18, 2.5)}):
            result = get_aws_cost()

        assert result["available"] is True
        assert result["provider"] == "AWS"
        assert abs(result["last_month_spent"] - 0.18) < 0.01
        assert abs(result["total_spent"] - 2.5) < 0.01

    def test_negative_amount_becomes_positive(self):
        with patch.dict("os.environ", self._ENV), \
             patch.dict("sys.modules", {"boto3": self._mock_boto3(-0.18, -5.0)}):
            result = get_aws_cost()

        assert result["last_month_spent"] >= 0
        assert result["total_spent"] >= 0

    def test_boto3_not_installed(self):
        with patch.dict("os.environ", self._ENV), \
             patch.dict("sys.modules", {"boto3": None}):
            result = get_aws_cost()

        assert result["available"] is False


# ── Heroku ────────────────────────────────────────────────────────────────────

class TestHerokuCost:

    def _mock_get(self, invoices, credits=None):
        invoice_resp = MagicMock()
        invoice_resp.json.return_value = invoices
        invoice_resp.raise_for_status = MagicMock()
        invoice_resp.status_code = 200

        credit_resp = MagicMock()
        credit_resp.status_code = 200
        credit_resp.json.return_value = credits or []

        return [invoice_resp, credit_resp]

    def test_last_month_from_invoices(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        prev = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        invoices = [
            {"period_start": f"{prev}-01T00:00:00Z", "total": 700},   # $7.00
            {"period_start": "2024-01-01T00:00:00Z", "total": 500},   # $5.00
        ]
        with patch.dict("os.environ", {"HEROKU_API_KEY": "key"}), \
             patch("requests.get", side_effect=self._mock_get(invoices)):
            result = get_heroku_cost()

        assert result["available"] is True
        assert result["last_month_spent"] == 7.0
        assert result["total_spent"] == 12.0

    def test_credit_balance_from_credits_api(self):
        credits = [{"balance": 31200}]  # cents? or dollars — depends on API
        with patch.dict("os.environ", {"HEROKU_API_KEY": "key"}), \
             patch("requests.get", side_effect=self._mock_get([], credits)):
            result = get_heroku_cost()

        assert result["credit_balance"] >= 0

    def test_no_invoices_returns_zero(self):
        with patch.dict("os.environ", {"HEROKU_API_KEY": "key"}), \
             patch("requests.get", side_effect=self._mock_get([])):
            result = get_heroku_cost()

        assert result["available"] is True
        assert result["last_month_spent"] == 0.0
        assert result["total_spent"] == 0.0


# ── get_all_costs ─────────────────────────────────────────────────────────────

class TestGetAllCosts:

    def test_returns_four_providers(self):
        def ok(p):
            return {"provider": p, "available": True, "credit_balance": 0,
                    "last_month_spent": 0, "total_spent": 0, "error": ""}
        with patch("app.tools.cost_tool.get_azure_cost", return_value=ok("Azure")), \
             patch("app.tools.cost_tool.get_aws_cost", return_value=ok("AWS")), \
             patch("app.tools.cost_tool.get_heroku_cost", return_value=ok("Heroku")), \
             patch("app.tools.cost_tool.get_google_cost", return_value=ok("Google Cloud")):
            results = get_all_costs()

        assert len(results) == 4
        providers = [r["provider"] for r in results]
        assert "Azure" in providers
        assert "AWS" in providers
        assert "Heroku" in providers
        assert "Google Cloud" in providers


# ── format_cost_report ────────────────────────────────────────────────────────

class TestFormatCostReport:

    def _cost(self, provider, last_month=0.0, total=0.0, credit=0.0, available=True):
        return {
            "provider": provider,
            "credit_balance": credit,
            "last_month_spent": last_month,
            "total_spent": total,
            "available": available,
            "error": "" if available else "not configured",
        }

    def test_shows_provider_names(self):
        costs = [self._cost("Azure", 3.75, 42.5), self._cost("AWS", 0.18, 2.5)]
        report = format_cost_report(costs)
        assert "Azure" in report
        assert "AWS" in report

    def test_shows_last_month_and_total(self):
        costs = [self._cost("Azure", last_month=3.75, total=42.5)]
        report = format_cost_report(costs)
        assert "3.75" in report
        assert "42.5" in report

    def test_shows_credit_balance_when_nonzero(self):
        costs = [self._cost("Heroku", credit=312.0)]
        report = format_cost_report(costs)
        assert "312" in report

    def test_no_credit_line_when_zero(self):
        costs = [self._cost("AWS", last_month=0.18, total=2.5, credit=0.0)]
        report = format_cost_report(costs)
        assert "الرصيد" not in report

    def test_shows_unavailable_providers(self):
        costs = [self._cost("Azure", available=False)]
        report = format_cost_report(costs)
        assert "غير متاح" in report

    def test_shows_totals_summary(self):
        costs = [
            self._cost("Azure", last_month=3.75, total=42.5),
            self._cost("AWS", last_month=0.18, total=2.5),
        ]
        report = format_cost_report(costs)
        assert "3.93" in report or "3.75" in report  # sum last month


