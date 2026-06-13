import base64
import json
import os
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import html
import re

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from app.integrations.google_oauth_env import (
    user_oauth_client_json_raw,
    user_oauth_token_json_raw,
    UNIFIED_SCOPES,
)
from app.utils.google_oauth_errors import maybe_raise_reconnect
from app.utils.user_profiles import active_profile_allows_privileged_access

# Use unified scopes from google_oauth_env.py
SCOPES = UNIFIED_SCOPES

_CLOUD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# Gmail and the old Tasks integration share one Google OAuth project, so these
# files historically carried the TASKS_ prefix. Prefer the Gmail-specific names;
# fall back to the legacy TASKS names so existing Heroku config keeps working.
TOKEN_FILE = (
    os.getenv("GMAIL_TOKEN_FILE")
    or os.getenv("GOOGLE_TASKS_TOKEN_FILE")
    or os.path.join(_CLOUD_DIR, "google-tasks-token.json")
)
CREDS_FILE = (
    os.getenv("GMAIL_OAUTH_FILE")
    or os.getenv("GOOGLE_TASKS_OAUTH_FILE")
    or os.path.join(_CLOUD_DIR, "google-tasks-oauth.json")
)

_GMAIL_SERVICE = None
_GMAIL_LOCK = threading.Lock()


def _clean_snippet(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:150]


def _build_gmail_credentials() -> Credentials:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    creds = None
    using_env_token = False

    token_raw = user_oauth_token_json_raw()
    if token_raw:
        creds = Credentials.from_authorized_user_info(json.loads(token_raw), SCOPES)
        using_env_token = True
    elif os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as refresh_err:
                maybe_raise_reconnect(refresh_err)
                raise
            if not using_env_token:
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
        else:
            client_raw = user_oauth_client_json_raw()
            if client_raw:
                flow = InstalledAppFlow.from_client_config(
                    json.loads(client_raw), SCOPES
                )
            elif os.path.exists(CREDS_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            else:
                raise RuntimeError(
                    "Gmail OAuth is not configured. "
                    "Set GOOGLE_USER_TOKEN_JSON (or GOOGLE_TASKS_TOKEN_JSON) and "
                    "GOOGLE_USER_OAUTH_JSON, or place google-tasks-oauth.json / google-tasks-token.json under cloud/."
                )

            if (
                os.getenv("RAILWAY_ENVIRONMENT")
                or os.getenv("RAILWAY_PROJECT_ID")
                or os.getenv("DYNO")  # Heroku
            ):
                raise RuntimeError(
                    "Gmail cannot run interactive OAuth on a managed platform. "
                    "Provide GOOGLE_USER_TOKEN_JSON with refresh_token and required scopes."
                )

            creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    return creds


def get_gmail_service():
    global _GMAIL_SERVICE
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    if _GMAIL_SERVICE is not None:
        return _GMAIL_SERVICE

    with _GMAIL_LOCK:
        if _GMAIL_SERVICE is not None:
            return _GMAIL_SERVICE

        creds = _build_gmail_credentials()
        _GMAIL_SERVICE = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return _GMAIL_SERVICE


def format_inbox_digest(emails: list, *, numbering_start: int = 1) -> str:
    """Readable Arabic inbox block for Telegram (numbered lines, separators)."""
    if not emails:
        return ""

    lines: list[str] = []
    n = len(emails)
    lines.append(f"📬 رسائل غير مقروءة ({n})")
    lines.append("")
    sep = "─" * 28

    for i, e in enumerate(emails):
        idx = numbering_start + i
        snd = html.unescape(str(e.get("sender", "") or "")).strip()
        snd_short = snd.split("<", 1)[0].strip() or snd
        subj_raw = html.unescape(str(e.get("subject", "") or "").strip())
        subj_disp = "(بدون عنوان)" if not subj_raw else subj_raw
        snip = str(e.get("snippet", "") or "").strip()

        lines.append(sep)
        lines.append(f"▸ {idx} — المرسل")
        lines.append(f"    {snd_short}")
        lines.append("")
        lines.append(f"   الموضوع  {subj_disp}")
        if snip:
            lines.append("")
            lines.append(f"   المعاينة  {snip}")
        lines.append("")

    lines.append(sep)
    tip = (
        "· آخر واحد = رقم أعلى؛ «اللي قبلو» قبل الأخير؛ «الي قبل الي قبل» الرسالة قبلها."
        " يمكن تقول كمان «رد على آخر واحد» مع نص الرد بعدها."
    )
    lines.append(tip)

    return "\n".join(lines).strip()


def get_unread_emails(max_results=10):
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    service = get_gmail_service()
    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], q="is:unread", maxResults=max_results)
        .execute()
    )
    messages = results.get("messages", [])
    emails = []
    for msg in messages:
        detail = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        emails.append(
            {
                "id": msg["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "snippet": _clean_snippet(detail.get("snippet", "")),
            }
        )
    return emails


def list_inbox_emails(max_results: int = 15):
    """Recent inbox messages (read AND unread) for the web tab.

    Each item: {id, sender, subject, snippet, date_iso, unread}.
    """
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    from datetime import datetime, timezone as _tz

    service = get_gmail_service()
    results = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=max_results)
        .execute()
    )
    emails = []
    for msg in results.get("messages", []):
        detail = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        date_iso = ""
        try:
            ms = int(detail.get("internalDate", "0"))
            if ms:
                date_iso = datetime.fromtimestamp(ms / 1000, tz=_tz.utc).isoformat()
        except Exception:
            pass
        emails.append(
            {
                "id": msg["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "snippet": _clean_snippet(detail.get("snippet", "")),
                "date_iso": date_iso,
                "unread": "UNREAD" in (detail.get("labelIds") or []),
            }
        )
    return emails


def get_email_body(email_id: str, max_chars: int = 6000) -> str:
    """Plain-text body of one message (walks parts, decodes, strips HTML)."""
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    service = get_gmail_service()
    detail = (
        service.users().messages().get(userId="me", id=email_id, format="full").execute()
    )

    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")
        except Exception:
            return ""

    def _walk(part) -> list:
        chunks = []
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        if mime == "text/plain" and body.get("data"):
            chunks.append(_decode(body["data"]))
        elif mime == "text/html" and body.get("data") and not chunks:
            raw = _decode(body["data"])
            raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
            raw = re.sub(r"<[^>]+>", " ", raw)
            chunks.append(html.unescape(raw))
        for sub in part.get("parts", []) or []:
            chunks.extend(_walk(sub))
        return chunks

    text = "\n".join(c for c in _walk(detail.get("payload", {}) or {}) if c.strip())
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars] or _clean_snippet(detail.get("snippet", ""))


def mark_email_read(email_id: str) -> bool:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")
    try:
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except Exception as e:
        print(f"[Gmail] mark read failed: {e}")
        return False


def archive_email(email_id: str) -> bool:
    """Remove from INBOX (Gmail's archive)."""
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")
    try:
        service = get_gmail_service()
        service.users().messages().modify(
            userId="me", id=email_id, body={"removeLabelIds": ["INBOX", "UNREAD"]}
        ).execute()
        return True
    except Exception as e:
        print(f"[Gmail] archive failed: {e}")
        return False


def gmail_preview_for_session(emails: list) -> dict:
    """Trimmed payloads safe to persist under session[\"gmail_last_list\"]."""
    out = []
    for e in emails or []:
        eid = str(e.get("id", "") or "").strip()
        if not eid:
            continue
        out.append(
            {
                "id": eid,
                "sender": str(e.get("sender", "") or ""),
                "subject": str(e.get("subject", "") or ""),
                "snippet": str(e.get("snippet", "") or ""),
            }
        )
    return {"messages": out}


def send_email(to: str, subject: str, body: str):
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    service = get_gmail_service()
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return True


def create_gmail_draft(to: str, subject: str, body: str) -> str:
    """Save email as a Gmail draft. Returns the draft ID."""
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    service = get_gmail_service()
    msg = MIMEMultipart()
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    return draft.get("id", "")


def reply_to_email(email_id: str, body: str):
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    service = get_gmail_service()
    original = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=email_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Message-ID"],
        )
        .execute()
    )
    headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}
    msg = MIMEMultipart()
    msg["To"] = headers.get("From", "")
    msg["Subject"] = "Re: " + headers.get("Subject", "")
    msg["In-Reply-To"] = headers.get("Message-ID", "")
    msg["References"] = headers.get("Message-ID", "")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return True
