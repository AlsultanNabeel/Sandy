import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.integrations.google_oauth_env import user_oauth_token_json_raw, UNIFIED_SCOPES
from app.utils.google_oauth_errors import (
    GoogleOAuthReconnectNeeded,
    maybe_raise_reconnect,
)
from app.utils.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.utils.time import USER_TZ
from app.utils.user_profiles import active_profile_allows_privileged_access

import threading as _threading

_calendar_cb = CircuitBreaker(
    name="google_calendar", failure_threshold=5, recovery_timeout=60.0
)

OAUTH_CALENDAR_SCOPES = UNIFIED_SCOPES

_CALENDAR_SERVICE = None
_CALENDAR_SERVICE_LOCK = _threading.Lock()


def calendar_id() -> str:
    """Primary calendar for API calls — must be set via environment (no hardcoded default)."""
    cid = (os.getenv("GOOGLE_CALENDAR_ID") or "").strip()
    if not cid:
        raise RuntimeError(
            "GOOGLE_CALENDAR_ID is not set. Set it to your Google Calendar email or calendar ID."
        )
    return cid


def _sandy_private_props(event: dict) -> dict:
    return (event.get("extendedProperties") or {}).get("private") or {}


def _is_sandy_reminder_event(event: dict) -> bool:
    props = _sandy_private_props(event)
    return props.get("sandy_type") == "reminder"


def _is_sandy_telegram_ping_event(event: dict) -> bool:
    """Calendar rows Sandy uses to trigger Telegram sends (reminders + follow-ups)."""
    props = _sandy_private_props(event)
    return props.get("sandy_type") in {"reminder", "event_followup"}


def _is_sandy_followup_anchor(event: dict) -> bool:
    return _sandy_private_props(event).get("sandy_type") == "event_followup"


def _user_visible_calendar_entry(e: dict) -> bool:
    """Hide Sandy-only rows from user-facing agendas (reminders + Telegram follow-up anchors)."""
    d = (e.get("description") or "").strip()
    if d.startswith("Reminder created by Sandy:"):
        return False
    if _is_sandy_followup_anchor(e):
        return False
    return True


def _extract_sandy_task_id(description: str) -> str:
    marker = "[SANDY_TASK_ID:"
    if marker not in description:
        return ""
    return description.split(marker, 1)[1].split("]", 1)[0].strip()


def _reset_calendar_service() -> None:
    """Invalidate the cached service (call on auth errors so next request rebuilds it)."""
    global _CALENDAR_SERVICE
    with _CALENDAR_SERVICE_LOCK:
        _CALENDAR_SERVICE = None


def _build_calendar_service() -> Any:
    """Construct a new Calendar API service (no caching; called under lock)."""
    from googleapiclient.discovery import build

    token_raw = user_oauth_token_json_raw()
    if token_raw:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(
            json.loads(token_raw), OAUTH_CALENDAR_SCOPES
        )
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                print("[Calendar] refreshing OAuth token")
                try:
                    creds.refresh(Request())
                except Exception as refresh_err:
                    maybe_raise_reconnect(refresh_err)
                    raise
            else:
                raise RuntimeError(
                    "Google Calendar OAuth token is invalid and cannot be refreshed. "
                    "Set GOOGLE_USER_TOKEN_JSON (or GOOGLE_CALENDAR_TOKEN_JSON) with a valid refresh_token."
                )
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    from google.oauth2 import service_account

    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(creds_json), scopes=OAUTH_CALENDAR_SCOPES
        )
    else:
        key_path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        if not key_path:
            raise RuntimeError(
                "Google Calendar credentials missing: set GOOGLE_USER_TOKEN_JSON (OAuth user), "
                "or GOOGLE_CREDENTIALS_JSON / GOOGLE_APPLICATION_CREDENTIALS (service account)."
            )
        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=OAUTH_CALENDAR_SCOPES
        )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _get_calendar_service() -> Any:
    """Return a cached Calendar service, building it on first call (thread-safe)."""
    if not active_profile_allows_privileged_access():
        raise PermissionError("هذا خاص بنبيل 😊")

    global _CALENDAR_SERVICE
    with _CALENDAR_SERVICE_LOCK:
        if _CALENDAR_SERVICE is None:
            _CALENDAR_SERVICE = _build_calendar_service()
        return _CALENDAR_SERVICE


def _execute_calendar_request_with_retry(request, label: str, attempts: int = 3) -> Any:
    """Execute a Calendar API request with exponential backoff and circuit breaker."""
    import google.auth.exceptions

    last_error: Exception = Exception("unknown")

    for attempt in range(1, attempts + 1):
        try:
            return _calendar_cb.call(request.execute, num_retries=2)
        except CircuitOpenError as e:
            print(f"[Calendar] circuit open for {label}")
            raise RuntimeError(
                f"Google Calendar unavailable (circuit open): {e}"
            ) from e
        except google.auth.exceptions.RefreshError as e:
            print(f"[Calendar] token refresh failed, resetting service: {e}")
            _reset_calendar_service()
            maybe_raise_reconnect(e)
            raise
        except Exception as e:
            last_error = e
            print(f"[Calendar] {label} attempt {attempt}/{attempts} failed: {e}")
            if attempt < attempts:
                delay = min(2**attempt * 0.5, 8.0)
                time.sleep(delay)

    raise last_error


def add_calendar_followup_ping(
    *,
    parent_event_id: str,
    parent_summary: str,
    ping_start_iso: str,
    minutes_duration: int = 2,
) -> Dict[str, Any]:
    """
    Schedule an invisible-ish calendar anchor so check_reminders can send Telegram
    “هل خلص الموعد؟” after the main event ends.
    """
    _md = datetime.fromisoformat(ping_start_iso.replace("Z", "+00:00"))
    if _md.tzinfo is None:
        _md = _md.replace(tzinfo=USER_TZ)
    ping_end_iso = (_md + timedelta(minutes=max(1, minutes_duration))).isoformat()
    return add_calendar_event(
        title=f"📋 متابعة: {(parent_summary or 'موعد').strip()}"[:140],
        start_iso=ping_start_iso,
        end_iso=ping_end_iso,
        description="Sandy: event_followup_ping anchor (not shown to Google popups ideally).",
        location="",
        attendees=None,
        color_id="",
        reminder_minutes=0,
        recurrence="",
        add_meet=False,
        sandy_telegram_private={
            "sandy_type": "event_followup",
            "sandy_parent_event_id": str(parent_event_id or "").strip(),
            "sandy_parent_summary": (parent_summary or "").strip(),
        },
    )


def add_calendar_event(
    title: str,
    start_iso: str,
    end_iso: Optional[str] = None,
    description: str = "",
    location: str = "",
    attendees: list = None,
    color_id: str = "",
    reminder_minutes: int = 30,
    recurrence: str = "",
    add_meet: bool = False,
    sandy_telegram_private: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")

        service = _get_calendar_service()

        cairo_tz = USER_TZ
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=cairo_tz)
        else:
            start_dt = start_dt.astimezone(cairo_tz)
        start_iso = start_dt.isoformat()

        is_sandy_reminder = description.startswith("Reminder created by Sandy:")
        if start_dt <= datetime.now(cairo_tz):
            return {"success": False, "error": "past_datetime"}

        if end_iso:
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=cairo_tz)
            else:
                end_dt = end_dt.astimezone(cairo_tz)
        else:
            end_dt = start_dt + (
                timedelta(minutes=1) if is_sandy_reminder else timedelta(hours=1)
            )
        end_iso = end_dt.isoformat()

        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": "Africa/Cairo"},
            "end": {"dateTime": end_iso, "timeZone": "Africa/Cairo"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": reminder_minutes},
                ],
            },
        }

        if is_sandy_reminder:
            private_props = {
                "sandy_type": "reminder",
                "sandy_send_state": "pending",
                "sandy_created_at": datetime.now(timezone.utc).isoformat(),
            }

            linked_task_id = _extract_sandy_task_id(description)
            if linked_task_id:
                private_props["sandy_task_id"] = linked_task_id

            event["extendedProperties"] = {"private": private_props}

        elif sandy_telegram_private:
            merged = dict(sandy_telegram_private)
            merged.setdefault("sandy_send_state", "pending")
            merged.setdefault(
                "sandy_created_at", datetime.now(timezone.utc).isoformat()
            )
            et = merged.get("sandy_type") or ""
            if et == "event_followup":
                event["extendedProperties"] = {"private": merged}
                event["transparency"] = "transparent"

        if is_sandy_reminder:
            event["transparency"] = "transparent"
        if location:
            event["location"] = location

        if attendees:
            event["attendees"] = [{"email": a} for a in attendees]

        if color_id:
            event["colorId"] = color_id

        if recurrence:
            event["recurrence"] = [recurrence]

        kwargs = {"calendarId": calendar_id(), "body": event}
        if add_meet:
            import uuid

            event["conferenceData"] = {
                "createRequest": {"requestId": str(uuid.uuid4())}
            }
            kwargs["conferenceDataVersion"] = 1

        result = _execute_calendar_request_with_retry(
            service.events().insert(**kwargs),
            "add_calendar_event",
        )
        print(f"[Calendar] event created: {result.get('htmlLink')}")
        return {
            "success": True,
            "event_id": result.get("id"),
            "link": result.get("htmlLink"),
        }

    except PermissionError:
        raise
    except GoogleOAuthReconnectNeeded:
        raise  # write paths must prompt reconnect like the read paths do
    except Exception as e:
        print(f"[Calendar] failed: {e}")
        return {"success": False, "error": str(e)}


def list_upcoming_events(max_results: int = 5) -> list:
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        filtered_events = []
        page_token = None
        fetch_size = min(max_results * 3, 50)

        while True:
            events_result = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=now,
                    maxResults=fetch_size,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "list_upcoming_events",
            )

            events = events_result.get("items", [])

            for event in events:
                if not _user_visible_calendar_entry(event):
                    continue

                filtered_events.append(event)
                if len(filtered_events) >= max_results:
                    print(
                        f"[Calendar] found {len(filtered_events)} upcoming calendar events"
                    )
                    return filtered_events

            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

        print(f"[Calendar] found {len(filtered_events)} upcoming calendar events")
        return filtered_events

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] failed: {e}")
        return []


def list_events_for_date_range(
    start_iso: str, end_iso: str, max_results: int = 20
) -> list:
    """Return non-reminder calendar events within [start_iso, end_iso]."""
    try:
        service = _get_calendar_service()
        cairo_tz = USER_TZ

        def _to_utc_z(value: str, *, end_of_day: bool) -> str:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                if "T" not in value:
                    dt = dt.replace(
                        hour=23 if end_of_day else 0,
                        minute=59 if end_of_day else 0,
                        second=59 if end_of_day else 0,
                        tzinfo=cairo_tz,
                    )
                else:
                    dt = dt.replace(tzinfo=cairo_tz)
            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        t_min = _to_utc_z(start_iso, end_of_day=False)
        t_max = _to_utc_z(end_iso, end_of_day=True)

        results = []
        page_token = None

        while True:
            resp = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=t_min,
                    timeMax=t_max,
                    maxResults=min(max_results * 2, 50),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "list_events_for_date_range",
            )
            for e in resp.get("items", []):
                if _user_visible_calendar_entry(e):
                    results.append(e)
                    if len(results) >= max_results:
                        return results
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return results

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] list_events_for_date_range failed: {e}")
        return []


def find_calendar_event_by_title(title: str) -> Dict[str, Any]:
    """Find an upcoming non-reminder event by partial title match."""
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        events = []
        page_token = None
        while True:
            resp = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=now,
                    maxResults=100,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "find_calendar_event_by_title",
            )
            batch = [
                e for e in resp.get("items", []) if _user_visible_calendar_entry(e)
            ]
            events.extend(batch)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        matched = [e for e in events if title.lower() in e.get("summary", "").lower()]
        if not matched:
            return {"found": False, "error": f"ما لقيت موعد باسم قريب من '{title}'"}
        if len(matched) > 1:
            return {
                "found": False,
                "ambiguous": True,
                "matches": [
                    {"id": e.get("id"), "summary": e.get("summary")}
                    for e in matched[:5]
                ],
            }
        e = matched[0]
        return {
            "found": True,
            "event_id": e.get("id"),
            "summary": e.get("summary"),
            "start": (e.get("start", {}) or {}).get("dateTime")
            or (e.get("start", {}) or {}).get("date")
            or "",
            "end": (e.get("end", {}) or {}).get("dateTime")
            or (e.get("end", {}) or {}).get("date")
            or "",
            "location": e.get("location", ""),
        }

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] find_calendar_event_by_title failed: {e}")
        return {"found": False, "error": str(e)}


def delete_calendar_event_by_title(title: str) -> Dict[str, Any]:
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        events = []
        page_token = None

        while True:
            events_result = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=now,
                    maxResults=100,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "delete_calendar_event_by_title_list",
            )

            batch = [
                e
                for e in events_result.get("items", [])
                if _user_visible_calendar_entry(e)
            ]
            events.extend(batch)

            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

        matched_events = [
            e for e in events if title.lower() in e.get("summary", "").lower()
        ]

        if not matched_events:
            return {"success": False, "error": f"ما لقيت موعد باسم قريب من '{title}'"}

        if len(matched_events) > 1:
            return {
                "success": False,
                "error": f"في أكثر من موعد مطابق لـ '{title}'",
                "ambiguous": True,
                "matches": [e.get("summary", "") for e in matched_events[:5]],
            }

        event_to_delete = matched_events[0]
        _execute_calendar_request_with_retry(
            service.events().delete(
                calendarId=calendar_id(), eventId=event_to_delete["id"]
            ),
            "delete_calendar_event_by_title",
        )
        return {
            "success": True,
            "title": event_to_delete.get("summary"),
            "deleted_count": 1,
        }

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_calendar_events_by_titles(titles: list) -> Dict[str, Any]:
    """احذف قائمة مواعيد بالأسماء — كل title يُبحث عنه ويُحذف مستقلاً"""
    results = []
    for title in titles:
        r = delete_calendar_event_by_title(title)
        results.append({"title": title, **r})
    success_count = sum(1 for r in results if r.get("success"))
    return {
        "success": success_count > 0,
        "deleted_count": success_count,
        "results": results,
    }


def delete_calendar_events_in_range(
    title_hint: str,
    start_date_iso: str,
    end_date_iso: str,
) -> Dict[str, Any]:
    """
    احذف كل occurrences لسلسلة معينة (أو أي موعد) ضمن نطاق زمني.
    title_hint: كلمة/اسم للبحث — فاضي = احذف كل اللي في النطاق.
    start_date_iso / end_date_iso: مثل '2025-05-01' أو ISO كامل.
    """
    try:
        service = _get_calendar_service()

        cairo_tz = USER_TZ

        def _to_utc_boundary(value: str, *, end_of_day: bool) -> str:
            raw = (value or "").strip()
            if not raw:
                raise ValueError("empty range boundary")

            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))

            if dt.tzinfo is None:
                if "T" in raw:
                    dt = dt.replace(tzinfo=cairo_tz)
                else:
                    dt = dt.replace(
                        hour=23 if end_of_day else 0,
                        minute=59 if end_of_day else 0,
                        second=59 if end_of_day else 0,
                        microsecond=999999 if end_of_day else 0,
                        tzinfo=cairo_tz,
                    )
            elif "T" not in raw:
                dt = dt.astimezone(cairo_tz).replace(
                    hour=23 if end_of_day else 0,
                    minute=59 if end_of_day else 0,
                    second=59 if end_of_day else 0,
                    microsecond=999999 if end_of_day else 0,
                )

            return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        t_min = _to_utc_boundary(start_date_iso, end_of_day=False)
        t_max = _to_utc_boundary(end_date_iso, end_of_day=True)

        events = []
        page_token = None

        while True:
            events_result = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=t_min,
                    timeMax=t_max,
                    maxResults=100,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "delete_calendar_events_in_range_list",
            )

            batch = [
                e
                for e in events_result.get("items", [])
                if _user_visible_calendar_entry(e)
            ]
            events.extend(batch)

            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

        # فلترة بالاسم لو مذكور
        if title_hint:
            hint_lower = title_hint.lower()
            events = [e for e in events if hint_lower in e.get("summary", "").lower()]

        if not events:
            return {
                "success": False,
                "error": "ما لقيت مواعيد في هالنطاق",
                "deleted_count": 0,
            }

        deleted = []
        for e in events:
            try:
                _execute_calendar_request_with_retry(
                    service.events().delete(calendarId=calendar_id(), eventId=e["id"]),
                    "delete_calendar_events_in_range_delete",
                )
                deleted.append(e.get("summary", e["id"]))
            except Exception as ex:
                print(f"[Calendar] failed to delete {e.get('summary')}: {ex}")

        return {
            "success": len(deleted) > 0,
            "deleted_count": len(deleted),
            "deleted_titles": deleted,
        }

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] range delete failed: {e}")
        return {"success": False, "error": str(e), "deleted_count": 0}


def update_calendar_event(
    event_id: str,
    title: str = "",
    start_iso: str = "",
    end_iso: str = "",
    location: str = "",
    description: str = "",
    reminder_minutes: int = None,
) -> Dict[str, Any]:
    try:
        service = _get_calendar_service()
        event = _execute_calendar_request_with_retry(
            service.events().get(calendarId=calendar_id(), eventId=event_id),
            "update_calendar_event_get",
        )

        if title:
            event["summary"] = title
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        cairo_tz = USER_TZ

        if start_iso:
            start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=cairo_tz)
            else:
                start_dt = start_dt.astimezone(cairo_tz)

            if start_dt <= datetime.now(cairo_tz):
                return {"success": False, "error": "past_datetime"}

            event["start"] = {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Africa/Cairo",
            }

        if end_iso:
            end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=cairo_tz)
            else:
                end_dt = end_dt.astimezone(cairo_tz)
            event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "Africa/Cairo"}
        if reminder_minutes is not None:
            event["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": reminder_minutes}],
            }

        result = _execute_calendar_request_with_retry(
            service.events().update(
                calendarId=calendar_id(), eventId=event_id, body=event
            ),
            "update_calendar_event",
        )
        print(f"[Calendar] event updated: {result.get('htmlLink')}")
        return {"success": True, "link": result.get("htmlLink")}

    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] update failed: {e}")
        return {"success": False, "error": str(e)}


def _format_calendar_dt_ar(value: str) -> str:
    if not value:
        return ""

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(USER_TZ)
        return dt.strftime("%d/%m %I:%M %p")
    except Exception:
        return value


def list_sandy_reminders(max_results: int = 100) -> list:
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        reminders = []
        seen_keys = set()
        page_token = None

        page_size = 100 if not max_results else min(max_results, 100)

        while True:
            events_result = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    timeMin=now,
                    maxResults=page_size,
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                ),
                "list_sandy_reminders",
            )

            for event in events_result.get("items", []):
                if not _is_sandy_reminder_event(event):
                    continue

                series_key = event.get("recurringEventId") or event.get("id", "")
                if series_key in seen_keys:
                    continue
                seen_keys.add(series_key)

                start = (
                    (event.get("start", {}) or {}).get("dateTime")
                    or (event.get("start", {}) or {}).get("date")
                    or ""
                )
                reminders.append(
                    {
                        "id": event.get("id", ""),
                        "text": event.get("summary", "") or "",
                        "remind_at": start,
                        "is_recurring": bool(
                            event.get("recurringEventId") or event.get("recurrence")
                        ),
                        "raw": event,
                    }
                )

                if max_results and len(reminders) >= max_results:
                    return reminders
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break

        return reminders

    except Exception as e:
        print(f"[Calendar] failed to list Sandy reminders: {e}")
        return [
            {
                "id": "",
                "text": "تعذر جلب التذكيرات من Google Calendar حاليًا. السيرفر لا يستطيع الوصول إلى الخدمة.",
                "remind_at": "",
                "raw": {"error": str(e)},
            }
        ]


def load_reminders():
    return list_sandy_reminders(max_results=100)


def delete_calendar_event_by_id(event_id: str) -> bool:
    """Delete a single calendar event by its id. Returns True on success.

    Used by the web reminders tab to remove a reminder the owner selected.
    """
    if not event_id:
        return False
    try:
        service = _get_calendar_service()
        _execute_calendar_request_with_retry(
            service.events().delete(calendarId=calendar_id(), eventId=event_id),
            "delete_calendar_event_by_id",
        )
        return True
    except GoogleOAuthReconnectNeeded:
        raise
    except Exception as e:
        print(f"[Calendar] delete_calendar_event_by_id failed: {e}")
        return False


def delete_sandy_reminder_by_task_id(task_id: str) -> int:
    if not task_id:
        return 0

    try:
        service = _get_calendar_service()
        reminders = list_sandy_reminders(max_results=0)
        deleted_count = 0

        for reminder in reminders:
            raw = reminder.get("raw", {}) or {}
            props = _sandy_private_props(raw)

            if props.get("sandy_task_id") != task_id:
                continue

            event_id = raw.get("recurringEventId") or reminder.get("id")
            if not event_id:
                continue

            _execute_calendar_request_with_retry(
                service.events().delete(calendarId=calendar_id(), eventId=event_id),
                "delete_all_sandy_reminders",
            )
            deleted_count += 1

        if deleted_count:
            print(
                f"[Calendar] deleted {deleted_count} reminder(s) linked to task {task_id}"
            )

        return deleted_count

    except Exception as e:
        print(f"[Calendar] failed to delete reminder by task id: {e}")
        return 0


def delete_all_sandy_reminders() -> int:
    """Delete ALL Sandy reminders (past + future) from Google Calendar."""
    try:
        service = _get_calendar_service()
        deleted_count = 0
        target_ids = set()
        page_token = None

        # Query without timeMin to include past reminders
        while True:
            result = _execute_calendar_request_with_retry(
                service.events().list(
                    calendarId=calendar_id(),
                    maxResults=100,
                    singleEvents=True,
                    pageToken=page_token,
                ),
                "delete_all_sandy_reminders_list",
            )
            for event in result.get("items", []):
                if not _is_sandy_reminder_event(event):
                    continue
                event_id = event.get("recurringEventId") or event.get("id", "")
                if event_id:
                    target_ids.add(event_id)
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        for event_id in target_ids:
            try:
                _execute_calendar_request_with_retry(
                    service.events().delete(calendarId=calendar_id(), eventId=event_id),
                    "delete_all_sandy_reminders_delete",
                )
                deleted_count += 1
            except Exception:
                pass

        print(f"[Calendar] deleted {deleted_count} Sandy reminders")
        return deleted_count

    except Exception as e:
        print(f"[Calendar] failed to delete Sandy reminders: {e}")
        return 0


def save_reminders(reminders):
    if reminders == []:
        delete_all_sandy_reminders()
        return

    print(
        "[Calendar] save_reminders ignored because Google Calendar is source of truth"
    )


# بتشيك إذا فيه تذكيرات لازم تنبعت وبتبعتها تلقائياً
def check_reminders(
    send_message_fn=None,
    user_chat_id=None,
):
    """Check Google Calendar Sandy reminders and send each occurrence once."""
    try:
        if not active_profile_allows_privileged_access():
            raise PermissionError("هذا خاص بنبيل 😊")

        service = _get_calendar_service()

        if not send_message_fn or not user_chat_id:
            return None

        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

        events_result = _execute_calendar_request_with_retry(
            service.events().list(
                calendarId=calendar_id(),
                timeMin=time_min,
                timeMax=time_max,
                maxResults=100,
                singleEvents=True,
                orderBy="startTime",
            ),
            "check_reminders_list",
        )

        sent_count = 0

        for event in events_result.get("items", []):
            if not _is_sandy_telegram_ping_event(event):
                continue

            props = dict(_sandy_private_props(event))
            send_state = props.get("sandy_send_state", "pending")

            if send_state in {"sending", "sent"}:
                continue

            event_id = event.get("id")
            if not event_id:
                continue

            props["sandy_send_state"] = "sending"
            props["sandy_send_claimed_at"] = now.isoformat()

            try:
                _execute_calendar_request_with_retry(
                    service.events().patch(
                        calendarId=calendar_id(),
                        eventId=event_id,
                        body={"extendedProperties": {"private": props}},
                    ),
                    "check_reminders_claim",
                )
            except Exception as e:
                print(f"[Reminder] failed to claim reminder before sending: {e}")
                continue

            summary = event.get("summary", "") or "بدون عنوان"
            sandy_t = props.get("sandy_type") or ""
            if sandy_t == "event_followup":
                pt = props.get("sandy_parent_summary") or summary
                message_text = (
                    f"📋 متابعة سكرتارية:\n«{pt}»\n"
                    f"خلص الموعد وتقدري توثّقي؟ (ردّي: خلص / لسه / تأجيل)"
                )
            else:
                message_text = f"🔔 تذكير: {summary}"

            try:
                send_message_fn(int(user_chat_id), message_text, parse_mode=None)
                print(f"[Reminder] sent from Calendar: {message_text}")
            except Exception as e:
                props["sandy_send_state"] = "failed"
                props["sandy_send_error"] = f"{type(e).__name__}: {e}"
                try:
                    _execute_calendar_request_with_retry(
                        service.events().patch(
                            calendarId=calendar_id(),
                            eventId=event_id,
                            body={"extendedProperties": {"private": props}},
                        ),
                        "check_reminders_mark_failed",
                    )
                except Exception as patch_error:
                    print(
                        f"[Reminder] ⚠️ Failed to mark reminder failed: {patch_error}"
                    )
                continue

            props["sandy_send_state"] = "sent"
            props["sandy_sent_at"] = datetime.now(timezone.utc).isoformat()

            try:
                _execute_calendar_request_with_retry(
                    service.events().patch(
                        calendarId=calendar_id(),
                        eventId=event_id,
                        body={"extendedProperties": {"private": props}},
                    ),
                    "check_reminders_finalize_sent",
                )
            except Exception as e:
                print(f"[Reminder] sent but failed to finalize sent state: {e}")

            sent_count += 1

        return f"Sent {sent_count} calendar reminder(s)" if sent_count else None

    except PermissionError:
        raise
    except Exception as e:
        print(f"[Scheduler] critical error: {e}")
        return None


# Re-exported for backward compatibility — implementation moved to calendar_time_parser.py
from app.features.calendar_time_parser import parse_reminder_time_ai  # noqa: F401, E402
