"""Detect revoked/expired Google OAuth refresh tokens and surface a clear user message."""

from __future__ import annotations


class GoogleOAuthReconnectNeeded(RuntimeError):
    """Raised when refresh_token is invalid, expired, or revoked (OAuth invalid_grant)."""

    DEFAULT_MSG = (
        "انتهت صلاحية ربط Google أو أُلغي. من جهازك المحلي شغّل: "
        "python scripts/refresh_google_token.py "
        "ثم انسخ JSON التوكن إلى المتغيّر GOOGLE_USER_TOKEN_JSON في Heroku Config Vars وأعد النشر. "
        "تحقق أيضاً من Google Account → Security → Third-party access إذا أزلت صلاحية التطبيق."
    )


def is_invalid_grant_error(exc: BaseException) -> bool:
    """True if Google OAuth returned invalid_grant (revoked/expired refresh token)."""
    parts: list[str] = [f"{type(exc).__name__}", str(exc)]
    args = getattr(exc, "args", ())
    for a in args:
        if isinstance(a, dict):
            parts.append(str(a.get("error", "")))
            parts.append(str(a.get("error_description", "")))
        else:
            parts.append(str(a))
    blob = " ".join(parts).lower()
    return "invalid_grant" in blob


def maybe_raise_reconnect(exc: BaseException) -> None:
    """If exc is invalid_grant, raise GoogleOAuthReconnectNeeded; otherwise do nothing."""
    if is_invalid_grant_error(exc):
        raise GoogleOAuthReconnectNeeded(
            GoogleOAuthReconnectNeeded.DEFAULT_MSG
        ) from exc


def user_message_for_google_auth_failure(exc: BaseException) -> str:
    """Arabic user-facing line for Gmail/API failures."""
    if isinstance(exc, GoogleOAuthReconnectNeeded):
        return str(exc)
    if is_invalid_grant_error(exc):
        return GoogleOAuthReconnectNeeded.DEFAULT_MSG
    return f"تعذّر تنفيذ البريد: {exc}"
