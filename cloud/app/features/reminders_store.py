"""Native reminder store — MongoDB, no external provider.

Replaces the Google Calendar "invisible event" hack: reminders used to be
calendar events with private props that a poller scraped back out. Now they
are plain Mongo documents and the same poller contract reads them directly.

Collection: sandy_reminders
  {_id, text, remind_at (datetime UTC), recurrence ("RRULE:FREQ=..." or ""),
   kind ("reminder" | "event_followup"), parent_summary, linked_task_id,
   send_state ("pending" | "sending" | "sent" | "failed"),
   created_at, sent_at, last_error}

Recurring reminders never reach "sent": after each delivery remind_at advances
to the next RRULE occurrence and the state returns to "pending".

Return contracts mirror the old google_calendar functions so the executor
handlers keep working with an import swap:
  add_reminder / update_reminder → {"success": bool, "error": ...}
  check_due_reminders            → "Sent N reminder(s)" or None
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

_COLL = "sandy_reminders"
_mongo_db = None

# How far back the due-check looks. A dyno restart can skip a minute-cron tick
# or two; anything older than this window is stale enough to drop silently.
_LOOKBACK_MIN = 15


def init_reminders_store(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع."""
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index(
            [("send_state", 1), ("remind_at", 1)], background=True
        )
        print("[RemindersStore] ready")
    except Exception as e:  # noqa: BLE001
        print(f"[RemindersStore] index skipped: {e}")


def is_available() -> bool:
    return _mongo_db is not None


def _coll():
    return _mongo_db[_COLL] if _mongo_db is not None else None


def _require_owner() -> None:
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=USER_TZ) if dt.tzinfo is None else dt
    except Exception:
        return None


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _as_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Mongo returns naive datetimes that are actually UTC — fix that."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _next_occurrence(rrule_str: str, dtstart: datetime, after: datetime) -> Optional[datetime]:
    """Next RRULE occurrence strictly after `after`. None when the rule ends."""
    try:
        from dateutil.rrule import rrulestr

        rule_body = rrule_str.split("RRULE:", 1)[-1]
        # rrule wants dtstart and after on the same awareness; keep all UTC.
        rule = rrulestr(rule_body, dtstart=_to_utc(dtstart))
        nxt = rule.after(_to_utc(after))
        return nxt
    except Exception as e:
        print(f"[RemindersStore] rrule parse failed ({rrule_str}): {e}")
        # Pragmatic fallback for the three simple shapes Sandy actually emits.
        upper = rrule_str.upper()
        step = None
        if "FREQ=DAILY" in upper:
            step = timedelta(days=1)
        elif "FREQ=WEEKLY" in upper:
            step = timedelta(weeks=1)
        elif "FREQ=MONTHLY" in upper:
            step = timedelta(days=30)
        if step is None:
            return None
        nxt = _to_utc(dtstart)
        after_utc = _to_utc(after)
        while nxt <= after_utc:
            nxt += step
        return nxt


def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Mongo doc → the dict shape the handlers and the web UI already expect."""
    remind_at = _as_aware_utc(doc.get("remind_at"))
    return {
        "id": doc.get("_id", ""),
        "text": doc.get("text", "") or "",
        "remind_at": remind_at.astimezone(USER_TZ).isoformat() if remind_at else "",
        "is_recurring": bool(doc.get("recurrence")),
        "recurrence": doc.get("recurrence", "") or "",
        "task_id": doc.get("linked_task_id", "") or "",
        "kind": doc.get("kind", "reminder") or "reminder",
        "send_state": doc.get("send_state", "pending"),
    }


# ─── Reads ────────────────────────────────────────────────────────────────────

def load_reminders(max_results: int = 50) -> List[Dict[str, Any]]:
    """Upcoming (not yet sent) reminders, soonest first."""
    try:
        _require_owner()
        coll = _coll()
        if coll is None:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_LOOKBACK_MIN)
        docs = (
            coll.find(
                {
                    "send_state": {"$in": ["pending", "sending", "failed"]},
                    "remind_at": {"$gte": cutoff},
                }
            )
            .sort("remind_at", 1)
            .limit(max_results)
        )
        return [_normalize(d) for d in docs]
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] load failed: {e}")
        return []


# Same data, the name the web API used against Google Calendar.
def list_sandy_reminders(max_results: int = 50) -> List[Dict[str, Any]]:
    return load_reminders(max_results=max_results)


# ─── Writes ───────────────────────────────────────────────────────────────────

def add_reminder(
    text: str,
    remind_at_iso: str,
    recurrence: str = "",
    linked_task_id: str = "",
    kind: str = "reminder",
    parent_summary: str = "",
) -> Dict[str, Any]:
    try:
        _require_owner()
        coll = _coll()
        if coll is None:
            return {"success": False, "error": "no_store"}

        text = str(text or "").strip()
        if not text:
            return {"success": False, "error": "empty_text"}

        remind_dt = _parse_iso(remind_at_iso)
        if remind_dt is None:
            return {"success": False, "error": "bad_datetime"}
        if remind_dt <= datetime.now(USER_TZ):
            return {"success": False, "error": "past_datetime"}

        doc = {
            "_id": uuid.uuid4().hex,
            "text": text,
            "remind_at": _to_utc(remind_dt),
            "recurrence": str(recurrence or "").strip(),
            "kind": kind or "reminder",
            "parent_summary": str(parent_summary or "").strip(),
            "linked_task_id": str(linked_task_id or "").strip(),
            "send_state": "pending",
            "created_at": datetime.now(timezone.utc),
            "sent_at": None,
            "last_error": "",
        }
        coll.insert_one(doc)
        print(f"[RemindersStore] reminder created: {text} @ {remind_at_iso}")
        return {"success": True, "id": doc["_id"]}
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] create failed: {e}")
        return {"success": False, "error": str(e)}


def update_reminder(
    reminder_id: str,
    title: str = "",
    start_iso: str = "",
    recurrence: Optional[str] = None,
) -> Dict[str, Any]:
    """Empty title/start_iso means "leave unchanged"; recurrence=None too."""
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not reminder_id:
            return {"success": False, "error": "missing"}

        updates: Dict[str, Any] = {}
        if title:
            updates["text"] = str(title).strip()
        if start_iso:
            new_dt = _parse_iso(start_iso)
            if new_dt is None:
                return {"success": False, "error": "bad_datetime"}
            if new_dt <= datetime.now(USER_TZ):
                return {"success": False, "error": "past_datetime"}
            updates["remind_at"] = _to_utc(new_dt)
            updates["send_state"] = "pending"
            updates["sent_at"] = None
        if recurrence is not None:
            updates["recurrence"] = str(recurrence).strip()
        if not updates:
            return {"success": False, "error": "nothing_to_update"}

        r = coll.update_one({"_id": reminder_id}, {"$set": updates})
        if r.matched_count == 0:
            return {"success": False, "error": "not_found"}
        return {"success": True}
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] update failed: {e}")
        return {"success": False, "error": str(e)}


def snooze_reminder(reminder_id: str, minutes: int = 30) -> Dict[str, Any]:
    """Push a reminder forward from now (works on sent ones too — that's the point)."""
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not reminder_id:
            return {"success": False, "error": "missing"}
        new_dt = datetime.now(USER_TZ) + timedelta(minutes=max(1, int(minutes)))
        r = coll.update_one(
            {"_id": reminder_id},
            {
                "$set": {
                    "remind_at": _to_utc(new_dt),
                    "send_state": "pending",
                    "sent_at": None,
                }
            },
        )
        if r.matched_count == 0:
            return {"success": False, "error": "not_found"}
        return {"success": True, "new_iso": new_dt.isoformat()}
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] snooze failed: {e}")
        return {"success": False, "error": str(e)}


def complete_reminder(reminder_id: str) -> bool:
    """Owner tapped "done" — close it out, recurring or not."""
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not reminder_id:
            return False
        r = coll.update_one(
            {"_id": reminder_id},
            {
                "$set": {
                    "send_state": "sent",
                    "recurrence": "",
                    "sent_at": datetime.now(timezone.utc),
                }
            },
        )
        return r.matched_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] complete failed: {e}")
        return False


def delete_reminder(reminder_id: str) -> bool:
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not reminder_id:
            return False
        return coll.delete_one({"_id": reminder_id}).deleted_count > 0
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] delete failed: {e}")
        return False


def delete_sandy_reminder_by_task_id(task_id: str) -> int:
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not task_id:
            return 0
        return coll.delete_many({"linked_task_id": task_id}).deleted_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] delete by task failed: {e}")
        return 0


def delete_all_sandy_reminders() -> int:
    try:
        _require_owner()
        coll = _coll()
        if coll is None:
            return 0
        r = coll.delete_many({})
        print(f"[RemindersStore] deleted all reminders: {r.deleted_count}")
        return r.deleted_count
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] delete all failed: {e}")
        return 0


# ─── The minute poller ───────────────────────────────────────────────────────

def check_due_reminders(
    send_message_fn=None,
    user_chat_id=None,
    keyboard_builder=None,
):
    """Claim each due reminder atomically, deliver it, then finalize.

    keyboard_builder(reminder_dict) → a Telegram reply_markup (snooze/done
    buttons) or None. Built by the runtime so this module stays free of
    telebot imports.
    """
    try:
        _require_owner()
        coll = _coll()
        if coll is None or not send_message_fn or not user_chat_id:
            return None

        now = datetime.now(timezone.utc)
        window = {
            "$gte": now - timedelta(minutes=_LOOKBACK_MIN),
            "$lte": now + timedelta(minutes=1),
        }

        sent_count = 0
        while True:
            # Atomic claim: pending → sending. Two dynos can race; only one wins.
            doc = coll.find_one_and_update(
                {"send_state": "pending", "remind_at": window},
                {
                    "$set": {
                        "send_state": "sending",
                        "claimed_at": now,
                    }
                },
            )
            if doc is None:
                break

            norm = _normalize(doc)
            if norm["kind"] == "event_followup":
                pt = doc.get("parent_summary") or norm["text"]
                message_text = (
                    f"📋 متابعة سكرتارية:\n«{pt}»\n"
                    f"خلص الموعد وتقدري توثّقي؟ (ردّي: خلص / لسه / تأجيل)"
                )
            else:
                message_text = f"🔔 تذكير: {norm['text']}"

            kb = None
            if keyboard_builder is not None:
                try:
                    kb = keyboard_builder(norm)
                except Exception as e:
                    print(f"[RemindersStore] keyboard build failed: {e}")

            try:
                send_message_fn(
                    int(user_chat_id), message_text, parse_mode=None, reply_markup=kb
                )
                print(f"[RemindersStore] sent: {message_text}")
            except Exception as e:
                coll.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "send_state": "failed",
                            "last_error": f"{type(e).__name__}: {e}",
                        }
                    },
                )
                continue

            recurrence = doc.get("recurrence", "") or ""
            if recurrence:
                base = _as_aware_utc(doc.get("remind_at")) or now
                nxt = _next_occurrence(recurrence, base, max(base, now))
                if nxt:
                    coll.update_one(
                        {"_id": doc["_id"]},
                        {
                            "$set": {
                                "remind_at": nxt,
                                "send_state": "pending",
                                "sent_at": now,
                            }
                        },
                    )
                    sent_count += 1
                    continue
                # Rule exhausted — fall through and close it out.

            coll.update_one(
                {"_id": doc["_id"]},
                {"$set": {"send_state": "sent", "sent_at": now}},
            )
            sent_count += 1

        return f"Sent {sent_count} reminder(s)" if sent_count else None
    except PermissionError:
        raise
    except Exception as e:
        print(f"[RemindersStore] check failed: {e}")
        return None
