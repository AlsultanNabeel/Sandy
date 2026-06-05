"""Sentry error tracking initialization for Sandy Agent.

Captures production errors, Telegram interactions, and async task failures.
Never captures persona_snippet or sensitive Soul Vault data.
"""

import os
import logging
from typing import Optional
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

logger = logging.getLogger(__name__)


def init_sentry(app_env: str = None, app: Optional[object] = None) -> None:
    """Initialize Sentry with environment-aware configuration.

    Args:
        app_env: Environment name (dev/staging/production)
        app: Flask app instance (optional, for FlaskIntegration)
    """
    app_env = app_env or os.environ.get("APP_ENV", "development")
    sentry_dsn = os.environ.get("SENTRY_DSN")

    if not sentry_dsn:
        print("[Sentry] SENTRY_DSN not set. Error tracking disabled.", flush=True)
        return

    integrations = [
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ThreadingIntegration(propagate_hub=True),
    ]

    if app:
        integrations.append(FlaskIntegration())

    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=integrations,
        environment=app_env,
        traces_sample_rate=0.1 if app_env == "production" else 1.0,
        profiles_sample_rate=0.1 if app_env == "production" else 0.0,
        release=os.environ.get("APP_VERSION", "unknown"),
        ignore_errors=[
            KeyboardInterrupt,
            SystemExit,
        ],
        before_send=_sentry_before_send,
    )

    print(f"[Sentry] initialized for {app_env} environment", flush=True)


def _sentry_before_send(event: dict, hint: dict) -> Optional[dict]:
    """Filter sensitive data before sending to Sentry.

    Removes:
    - persona_snippet (Soul Vault)
    - soul_vault data
    - conversation context with user_id
    """
    if event.get("request"):
        # Redact POST body if contains persona/soul data
        body = event["request"].get("data", "")
        if isinstance(body, str):
            if "persona_snippet" in body or "soul_vault" in body:
                event["request"]["data"] = "[REDACTED: Persona/Soul data]"

    if event.get("extra"):
        # Remove user session data
        for key in ["persona_intensity", "persona_snippet", "soul_vault"]:
            event["extra"].pop(key, None)

    # Keep error context, but redact sensitive values
    if event.get("breadcrumbs"):
        for breadcrumb in event["breadcrumbs"]:
            if breadcrumb.get("data"):
                for sensitive_key in ["persona_snippet", "user_id", "chat_id"]:
                    if sensitive_key in breadcrumb["data"]:
                        breadcrumb["data"][sensitive_key] = "[REDACTED]"

    return event


def capture_exception(
    exc: Exception, context: dict = None, level: str = "error"
) -> None:
    """Capture exception with optional context (excludes persona data).

    Args:
        exc: Exception to capture
        context: Additional context dict (persona/soul data auto-filtered)
        level: Error level (error/warning/info)
    """
    with sentry_sdk.new_scope() as scope:
        if context:
            safe_context = {
                k: v
                for k, v in context.items()
                if k not in {"persona_snippet", "soul_vault", "persona_intensity"}
            }
            for key, value in safe_context.items():
                scope.set_extra(key, value)

        scope.set_level(level)
        sentry_sdk.capture_exception(exc)


def capture_message(message: str, level: str = "info", context: dict = None) -> None:
    """Capture log message with optional context.

    Args:
        message: Message to log
        level: Log level (debug/info/warning/error)
        context: Additional context dict
    """
    with sentry_sdk.new_scope() as scope:
        if context:
            safe_context = {
                k: v
                for k, v in context.items()
                if k not in {"persona_snippet", "soul_vault"}
            }
            for key, value in safe_context.items():
                scope.set_extra(key, value)

        scope.set_level(level)
        sentry_sdk.capture_message(message, level=level)
