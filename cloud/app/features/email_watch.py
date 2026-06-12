"""Important-email watch: alert Telegram only for mail that matters.

Every few minutes the scheduler calls check_new_important_emails():
  1. pull unread inbox messages
  2. drop ones we've already judged (Mongo sandy_email_seen — every message
     gets judged exactly once, alert or not)
  3. one batched model call classifies the new ones
  4. Telegram alert for the important ones only

Newsletters, promos and notification noise stay silent. Failures fail soft:
no Mongo → skip (better silent than spammy on re-judged mail).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

_COLL = "sandy_email_seen"
_mongo_db = None


def init_email_watch(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع."""
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index("seen_at", background=True)
        print("[EmailWatch] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[EmailWatch] index skipped: {e}")


def _unseen(emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if _mongo_db is None:
        return []
    ids = [e["id"] for e in emails if e.get("id")]
    if not ids:
        return []
    seen = {d["_id"] for d in _mongo_db[_COLL].find({"_id": {"$in": ids}}, {"_id": 1})}
    return [e for e in emails if e.get("id") and e["id"] not in seen]


def _mark_seen(emails: List[Dict[str, Any]]) -> None:
    if _mongo_db is None or not emails:
        return
    now = datetime.now(timezone.utc)
    for e in emails:
        try:
            _mongo_db[_COLL].replace_one(
                {"_id": e["id"]}, {"_id": e["id"], "seen_at": now}, upsert=True
            )
        except Exception:
            pass


def _classify_important(emails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One model call; returns the subset judged important (with a reason)."""
    listing = "\n".join(
        f"{i}. من: {e.get('sender','')} | الموضوع: {e.get('subject','')} | "
        f"مقتطف: {e.get('snippet','')}"
        for i, e in enumerate(emails)
    )
    prompt = (
        "أنت مصنّف بريد. حدد أي الرسائل التالية مهمة وتستحق تنبيه صاحبها فوراً "
        "(شخصية، عمل حقيقي، مواعيد، فواتير مستحقة، أمن حساب). "
        "النشرات والإعلانات والإشعارات الآلية ليست مهمة.\n"
        'أرجع JSON فقط: {"important": [{"index": 0, "reason": "سبب بكلمتين"}]}\n\n'
        + listing
    )
    try:
        from app.integrations.azure_intent_client import AzureIntentClient

        raw = AzureIntentClient()._generate_with_gemini(
            prompt,
            response_mime_type="application/json",
            max_output_tokens=300,
            temperature=0.1,
        )
        data = json.loads(raw or "{}")
        out = []
        for item in data.get("important", []):
            idx = int(item.get("index", -1))
            if 0 <= idx < len(emails):
                e = dict(emails[idx])
                e["reason"] = str(item.get("reason", "") or "").strip()
                out.append(e)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[EmailWatch] classify failed: {e}")
        return []


def check_new_important_emails(send_message_fn=None, user_chat_id=None):
    """Scheduler entry point. Same contract style as the reminder checker."""
    try:
        if not send_message_fn or not user_chat_id or _mongo_db is None:
            return None
        from app.features.gmail import get_unread_emails

        emails = get_unread_emails(max_results=15)
        fresh = _unseen(emails)
        if not fresh:
            return None
        # Judge once, remember forever — even the unimportant ones.
        _mark_seen(fresh)

        important = _classify_important(fresh)
        for e in important:
            sender = (e.get("sender", "") or "").split("<", 1)[0].strip()
            reason = f" — {e['reason']}" if e.get("reason") else ""
            text = (
                f"📨 إيميل مهم{reason}\n"
                f"من: {sender}\n"
                f"الموضوع: {e.get('subject', '(بدون عنوان)')}\n"
                f"المعاينة: {e.get('snippet', '')}"
            )
            try:
                send_message_fn(int(user_chat_id), text, parse_mode=None)
            except Exception as send_err:
                print(f"[EmailWatch] alert send failed: {send_err}")

        return f"Alerted {len(important)} of {len(fresh)} new" if important else None
    except PermissionError:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[EmailWatch] check failed: {e}")
        return None
