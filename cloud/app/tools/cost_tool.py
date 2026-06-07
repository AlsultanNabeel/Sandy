"""
Sandy Cost Awareness Tool.

كل provider يرجع:
  - credit_balance  : الرصيد المتبقي (credits/platform balance)
  - last_month_spent: سحب الشهر الماضي
  - total_spent     : الإجمالي الكلي من بداية الاشتراك

Env vars:
  AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
  HEROKU_API_KEY
  GOOGLE_BILLING_SA_JSON, GOOGLE_CLOUD_BILLING_ACCOUNT_ID
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

_TIMEOUT = 15


def _prev_month_range():
    now = datetime.now(timezone.utc)
    first_this = now.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev.strftime("%Y-%m-%d"), last_prev.strftime("%Y-%m-%d")


def _ok(provider: str, credit: float, last_month: float, total: float) -> Dict[str, Any]:
    return {
        "provider": provider,
        "credit_balance": round(credit, 2),
        "last_month_spent": round(last_month, 2),
        "total_spent": round(total, 2),
        "available": True,
        "error": "",
    }


def _unavailable(provider: str, error: str) -> Dict[str, Any]:
    return {
        "provider": provider,
        "credit_balance": 0.0,
        "last_month_spent": 0.0,
        "total_spent": 0.0,
        "available": False,
        "error": error,
    }


# Azure

def get_azure_cost() -> Dict[str, Any]:
    sub_id = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
    tenant = os.getenv("AZURE_TENANT_ID", "").strip()
    client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "").strip()

    if not all([sub_id, tenant, client_id, client_secret]):
        return _unavailable("Azure", "credentials not configured")

    try:
        token_resp = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://management.azure.com/.default",
            },
            timeout=_TIMEOUT,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        def _query_cost(from_date: str, to_date: str) -> float:
            resp = requests.post(
                f"https://management.azure.com/subscriptions/{sub_id}"
                "/providers/Microsoft.CostManagement/query?api-version=2023-11-01",
                headers=headers,
                json={
                    "type": "ActualCost",
                    "timeframe": "Custom",
                    "timePeriod": {"from": from_date, "to": to_date},
                    "dataset": {
                        "granularity": "None",
                        "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
                    },
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            rows = resp.json().get("properties", {}).get("rows", [])
            return abs(float(rows[0][0])) if rows else 0.0

        now = datetime.now(timezone.utc)
        prev_start, prev_end = _prev_month_range()

        try:
            last_month = _query_cost(prev_start, prev_end)
        except Exception as e:
            print(f"[CostTool] Azure last_month query failed: {e}", flush=True)
            last_month = 0.0

        try:
            current_month = _query_cost(now.strftime("%Y-%m-01"), now.strftime("%Y-%m-%d"))
        except Exception as e:
            print(f"[CostTool] Azure current_month query failed: {e}", flush=True)
            current_month = 0.0

        try:
            total_start = (now.replace(day=1) - timedelta(days=395)).replace(day=1)
            total = _query_cost(total_start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        except Exception as e:
            print(f"[CostTool] Azure total query failed: {e}", flush=True)
            total = last_month + current_month

        credit_balance = 0.0
        try:
            cr = requests.get(
                f"https://management.azure.com/subscriptions/{sub_id}"
                "/providers/Microsoft.Consumption/credits?api-version=2021-10-01",
                headers=headers,
                timeout=_TIMEOUT,
            )
            if cr.status_code == 200:
                items = cr.json().get("value") or []
                if items:
                    bal = items[0].get("properties", {}).get("balanceSummary", {})
                    credit_balance = abs(float(bal.get("estimatedBalance", 0) or 0))
        except Exception:
            pass

        return _ok("Azure", credit_balance, last_month, total)

    except Exception as e:
        print(f"[CostTool] Azure error: {type(e).__name__}: {e}", flush=True)
        return _unavailable("Azure", str(e))


# AWS

def get_aws_cost() -> Dict[str, Any]:
    key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1").strip()

    if not all([key, secret]):
        return _unavailable("AWS", "credentials not configured")

    try:
        import boto3  # type: ignore

        client = boto3.client(
            "ce",
            region_name=region,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
        )

        def _query(start: str, end: str) -> float:
            resp = client.get_cost_and_usage(
                TimePeriod={"Start": start, "End": end},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
            )
            total = sum(
                abs(float(r["Total"]["UnblendedCost"]["Amount"]))
                for r in resp.get("ResultsByTime", [])
            )
            return total

        now = datetime.now(timezone.utc)
        prev_start, prev_end = _prev_month_range()
        last_month = _query(prev_start, prev_end)
        # Cost Explorer يدعم آخر 14 شهر كحد أقصى
        total_start = (now.replace(day=1) - timedelta(days=395)).replace(day=1)
        total = _query(total_start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))

        return _ok("AWS", 0.0, last_month, total)

    except ImportError:
        return _unavailable("AWS", "boto3 not installed")
    except Exception as e:
        return _unavailable("AWS", str(e))


# Heroku

def get_heroku_cost() -> Dict[str, Any]:
    api_key = os.getenv("HEROKU_API_KEY", "").strip()
    if not api_key:
        return _unavailable("Heroku", "HEROKU_API_KEY not configured")

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.heroku+json; version=3",
        }

        # الفواتير
        resp = requests.get(
            "https://api.heroku.com/account/invoices",
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        invoices = resp.json() if isinstance(resp.json(), list) else []

        now = datetime.now(timezone.utc)
        prev_period = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        last_month = 0.0
        total = 0.0
        for inv in invoices:
            amount = abs(float(inv.get("total", 0) or 0)) / 100  # cents → USD
            total += amount
            period = str(inv.get("period_start", ""))[:7]
            if period == prev_period:
                last_month = amount

        # الرصيد (Platform Credits)
        credit_balance = 0.0
        try:
            cr = requests.get(
                "https://api.heroku.com/account/credits",
                headers=headers,
                timeout=_TIMEOUT,
            )
            if cr.status_code == 200:
                credits = cr.json() if isinstance(cr.json(), list) else []
                print(f"[CostTool] Heroku credits raw: {credits}", flush=True)
                credit_balance = sum(
                    abs(float(c.get("balance", 0) or 0)) / 100 for c in credits
                )
        except Exception:
            pass

        return _ok("Heroku", credit_balance, last_month, total)

    except Exception as e:
        return _unavailable("Heroku", str(e))


# Google Cloud

def get_google_cost() -> Dict[str, Any]:
    sa_json = os.getenv("GOOGLE_BILLING_SA_JSON", "").strip()
    account_id = os.getenv("GOOGLE_CLOUD_BILLING_ACCOUNT_ID", "").strip()

    if not all([sa_json, account_id]):
        return _unavailable("Google Cloud", "credentials not configured")

    try:
        import json as _json
        from google.oauth2 import service_account
        import google.auth.transport.requests as _gtr

        sa_info = _json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-billing.readonly"],
        )
        creds.refresh(_gtr.Request())
        headers = {"Authorization": f"Bearer {creds.token}"}

        now = datetime.now(timezone.utc)
        prev_start, _ = _prev_month_range()

        # جرّب Budget API — يعطي forecastedSpend إذا في budgets مضبوطة
        last_month = 0.0
        total = 0.0
        source = "unavailable"

        try:
            from google.cloud import billing_budgets_v1
            budget_client = billing_budgets_v1.BudgetServiceClient(credentials=creds)
            budgets = list(budget_client.list_budgets(
                parent=f"billingAccounts/{account_id}"
            ))
            if budgets:
                for b in budgets:
                    forecast = getattr(b, "forecasted_spend", None)
                    if forecast:
                        last_month = float(forecast.units) + float(forecast.nanos) / 1e9
                source = "budgets"
                print(f"[CostTool] Google budgets found: {len(budgets)}", flush=True)
        except Exception as e:
            print(f"[CostTool] Google Budget API: {e}", flush=True)

        # fallback: Billing Reports v1beta
        if source == "unavailable":
            try:
                resp = requests.get(
                    f"https://cloudbilling.googleapis.com/v1beta/billingAccounts/{account_id}/reports",
                    headers=headers,
                    params={
                        "dateRange.startDate.year": int(prev_start[:4]),
                        "dateRange.startDate.month": int(prev_start[5:7]),
                        "dateRange.startDate.day": 1,
                        "dateRange.endDate.year": now.year,
                        "dateRange.endDate.month": now.month,
                        "dateRange.endDate.day": now.day,
                    },
                    timeout=_TIMEOUT,
                )
                print(f"[CostTool] Google reports: {resp.status_code} {resp.text[:300]}", flush=True)
                if resp.status_code == 200:
                    data = resp.json()
                    costs = data.get("monthlyReport", {}).get("invoicedCost", {})
                    last_month = abs(float(costs.get("units", 0) or 0))
                    total = last_month
                    source = "reports"
            except Exception as e:
                print(f"[CostTool] Google Reports API: {e}", flush=True)

        if source == "unavailable":
            return _unavailable("Google Cloud", "لا توجد بيانات — يحتاج BigQuery export أو Budget مضبوط")

        return _ok("Google Cloud", 0.0, last_month, total)

    except ImportError as e:
        return _unavailable("Google Cloud", f"missing library: {e}")
    except Exception as e:
        print(f"[CostTool] Google Cloud error: {type(e).__name__}: {e}", flush=True)
        return _unavailable("Google Cloud", str(e))


def get_all_costs() -> List[Dict[str, Any]]:
    return [get_azure_cost(), get_aws_cost(), get_heroku_cost(), get_google_cost()]


# Formatting

_PROVIDER_EMOJI = {
    "Azure": "☁️",
    "AWS": "🟠",
    "Heroku": "🟣",
    "Google Cloud": "🔵",
}

_MONTHS_AR = ["", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
              "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def format_cost_report(costs: List[Dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc)
    prev_start, _ = _prev_month_range()
    prev_month_ar = _MONTHS_AR[int(prev_start[5:7])]
    month_ar = _MONTHS_AR[now.month]

    lines = [f"💰 *تقرير الاستهلاك | {month_ar} {now.year}*\n"]

    total_last_month = 0.0
    total_all = 0.0
    total_credit = 0.0
    unavailable = []

    for c in costs:
        if not c.get("available"):
            unavailable.append(c["provider"])
            continue

        provider = c["provider"]
        emoji = _PROVIDER_EMOJI.get(provider, "🔵")
        credit = c["credit_balance"]
        last_month = c["last_month_spent"]
        total = c["total_spent"]

        total_last_month += last_month
        total_all += total
        total_credit += credit

        lines.append(f"{emoji} *{provider}*")
        if credit > 0:
            lines.append(f"┌ الرصيد المتبقي:    *${credit:,.2f}*")
            lines.append(f"├ سحب {prev_month_ar}:      *${last_month:,.2f}*")
            lines.append(f"└ الإجمالي الكلي:    *${total:,.2f}*")
        else:
            lines.append(f"┌ سحب {prev_month_ar}:      *${last_month:,.2f}*")
            lines.append(f"└ الإجمالي الكلي:    *${total:,.2f}*")
        lines.append("")

    if unavailable:
        names = "  |  ".join(
            f"{_PROVIDER_EMOJI.get(p, '⚪')} {p}" for p in unavailable
        )
        lines.append(f"⚪ *غير متاح:* {names}\n")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 سحب {prev_month_ar} الكلي:  *${total_last_month:,.2f}*")
    lines.append(f"📊 الإجمالي الكلي:       *${total_all:,.2f}*")
    if total_credit > 0:
        lines.append(f"💳 إجمالي الرصيد:        *${total_credit:,.2f}*")

    return "\n".join(lines)
