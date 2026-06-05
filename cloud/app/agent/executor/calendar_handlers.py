from datetime import datetime, timedelta
from typing import Any, Dict


from app.utils.time import USER_TZ
from app.utils.arabic_days import WEEKDAY_TO_AR_NAME
from app.agent.pending import create_pending_action
from app.agent.deep_context import record_last_action
from app.agent.conflict_resolution import run_conflict_check_after_calendar_add
from app.agent.conflict_resolution import (
    create_conflict_inline_markup,
    stash_conflict_resolution,
)

from app.features.google_calendar import (
    add_calendar_event,
    find_calendar_event_by_title,
    list_events_for_date_range,
    list_upcoming_events,
    parse_reminder_time_ai,
)
from app.utils.user_profiles import active_profile_is_owner

# Date-range helpers for the list view.


def _day_range(base: datetime):
    s = base.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    e = base.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
    return s, e


def _week_range_this(base: datetime):
    """today 00:00 → coming Sunday 23:59"""
    days_to_sun = (6 - base.weekday()) % 7
    sun = base + timedelta(days=days_to_sun)
    return (
        base.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        sun.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
    )


def _week_range_next(base: datetime):
    """next Monday 00:00 → next Sunday 23:59"""
    days_to_mon = (7 - base.weekday()) % 7 or 7
    mon = base + timedelta(days=days_to_mon)
    sun = mon + timedelta(days=6)
    return (
        mon.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        sun.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
    )


def _weekend_range(base: datetime):
    """coming Friday 00:00 → Saturday 23:59"""
    days_to_fri = (4 - base.weekday()) % 7
    fri = base + timedelta(days=days_to_fri)
    sat = fri + timedelta(days=1)
    return (
        fri.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        sat.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
    )


def _human_header(min_dt: datetime, max_dt: datetime):
    if min_dt.date() == max_dt.date():
        day = WEEKDAY_TO_AR_NAME[min_dt.weekday()]
        label = min_dt.strftime(f"{day} %d/%m")
        return (
            f"📅 مواعيد {label}:",
            f"يومك {label} فاضي ✨ وقت ذهبي للتركيز أو الراحة!",
        )
    from_str = f"{WEEKDAY_TO_AR_NAME[min_dt.weekday()]} {min_dt.strftime('%d/%m')}"
    to_str = f"{WEEKDAY_TO_AR_NAME[max_dt.weekday()]} {max_dt.strftime('%d/%m')}"
    return f"📅 المواعيد من {from_str} إلى {to_str}:", "ما في مواعيد في هذه الفترة."


def _handle_delete_multi(params, *, session, session_file, mongo_db, save_session_fn):
    titles = params.get("titles", []) or []
    if not titles:
        return {
            "handled": True,
            "reply": "ما عرفت شو المواعيد اللي بدك تحذفها، عدد أسماءهم.",
        }
    session["pending_action"] = create_pending_action(
        {
            "type": "calendar",
            "action": "confirm_delete_multi",
            "titles": titles,
        }
    )
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    names_text = "\n".join(f"• {t}" for t in titles)
    return {"handled": True, "reply": f"متأكد بدك تحذف هالمواعيد؟\n{names_text}"}


def _handle_delete_range(params, *, session, session_file, mongo_db, save_session_fn):
    title_hint = str(params.get("title_hint", "")).strip()
    start_date = str(params.get("start_date_iso", "")).strip()
    end_date = str(params.get("end_date_iso", "")).strip()

    if not start_date or not end_date:
        return {"handled": True, "reply": "حدد النطاق الزمني — متى تبدأ ومتى تنتهي؟"}

    session["pending_action"] = create_pending_action(
        {
            "type": "calendar",
            "action": "confirm_delete_range",
            "title_hint": title_hint,
            "start_date_iso": start_date,
            "end_date_iso": end_date,
        }
    )
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    hint_text = f" لـ '{title_hint}'" if title_hint else ""
    return {
        "handled": True,
        "reply": f"متأكد بدك تحذف كل المواعيد{hint_text} من {start_date} لـ {end_date}؟",
    }


def _handle_list(params):
    now = datetime.now(USER_TZ)

    time_min_raw = str(params.get("time_min", "")).strip()
    time_max_raw = str(params.get("time_max", "")).strip()

    if time_min_raw and time_max_raw:
        events = list_events_for_date_range(time_min_raw, time_max_raw, max_results=10)
        try:
            min_dt = datetime.fromisoformat(
                time_min_raw.replace("Z", "+00:00")
            ).astimezone(USER_TZ)
            max_dt = datetime.fromisoformat(
                time_max_raw.replace("Z", "+00:00")
            ).astimezone(USER_TZ)
            header, empty_msg = _human_header(min_dt, max_dt)
        except Exception:
            header, empty_msg = "📅 المواعيد:", "ما في مواعيد في هذه الفترة."
    else:
        query = str(params.get("query", "")).strip().lower()

        if query in {"today", "اليوم", "النهارده"}:
            s, e = _day_range(now)
            events = list_events_for_date_range(s, e, max_results=10)
            day_l = WEEKDAY_TO_AR_NAME[now.weekday()]
            header = f"📅 مواعيد اليوم ({day_l}):"
            empty_msg = f"يومك اليوم ({day_l}) خالي من المواعيد ✨ وقت مثالي للتركيز!"

        elif query in {"tomorrow", "بكرا", "بكره", "الغد", "غدا", "غداً"}:
            tom = now + timedelta(days=1)
            s, e = _day_range(tom)
            events = list_events_for_date_range(s, e, max_results=10)
            day_l = WEEKDAY_TO_AR_NAME[tom.weekday()]
            header = f"📅 مواعيد بكرا ({day_l}):"
            empty_msg = f"بكرا ({day_l}) ما في مواعيد مسجلة 📅"

        elif query == "day_after_tomorrow":
            dat = now + timedelta(days=2)
            s, e = _day_range(dat)
            events = list_events_for_date_range(s, e, max_results=10)
            day_l = WEEKDAY_TO_AR_NAME[dat.weekday()]
            header = f"📅 مواعيد بعد بكرا ({day_l}):"
            empty_msg = f"بعد بكرا ({day_l}) فاضي تماماً ✨"

        elif query in {"week", "هالأسبوع", "هالاسبوع", "الأسبوع", "الاسبوع"}:
            s, e = _week_range_this(now)
            events = list_events_for_date_range(s, e, max_results=15)
            header = "📅 مواعيد هالأسبوع (إلى الأحد):"
            empty_msg = "هالأسبوع ما في مواعيد ✨ وقت ذهبي للإنجاز!"

        elif query in {
            "next_week",
            "الأسبوع القادم",
            "الاسبوع الجاي",
            "الاسبوع الجايي",
        }:
            s, e = _week_range_next(now)
            events = list_events_for_date_range(s, e, max_results=15)
            header = "📅 مواعيد الأسبوع القادم (الاثنين → الأحد):"
            empty_msg = "الأسبوع القادم فاضي تماماً ✨"

        elif query in {"weekend", "الويكند", "ويكند", "نهاية الأسبوع"}:
            s, e = _weekend_range(now)
            events = list_events_for_date_range(s, e, max_results=10)
            header = "📅 مواعيد الويكند (ج-س):"
            empty_msg = "الويكند فاضي ✨ استمتع بوقتك!"

        else:
            events = list_upcoming_events(max_results=5)
            header = "📅 المواعيد القادمة:"
            empty_msg = "ما في مواعيد قادمة في التقويم."

    if events:
        lines = [header]
        for e in events:
            start = (e.get("start", {}) or {}).get("dateTime", "")
            summary = e.get("summary", "")
            event_id = e.get("id", "")
            loc = str(e.get("location", "") or "").strip()
            try:
                dt_obj = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if dt_obj.tzinfo is not None:
                    dt_obj = dt_obj.astimezone(USER_TZ)
                day_ar = WEEKDAY_TO_AR_NAME[dt_obj.weekday()]
                dt = dt_obj.strftime(f"{day_ar} %d/%m %I:%M %p")
            except Exception:
                dt = start
            line = f"• {summary} — {dt} (ID: {event_id})"
            if loc:
                loc_short = loc if len(loc) <= 72 else loc[:69] + "…"
                line += f"\n   📍 {loc_short}"
            lines.append(line)
        return {"handled": True, "reply": "\n".join(lines)}

    return {"handled": True, "reply": empty_msg}


def _handle_delete(params, *, session, session_file, mongo_db, save_session_fn):
    title = str(params.get("title", "الموعد")).strip()
    session["pending_action"] = create_pending_action(
        {
            "type": "calendar",
            "action": "delete",
            "title": title,
        }
    )
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {"handled": True, "reply": f"متأكد بدك تحذف '{title}'؟"}


def _handle_update(
    params,
    *,
    session,
    session_file,
    mongo_db,
    save_session_fn,
    create_chat_completion_fn,
):
    event_id = str(params.get("event_id", "")).strip()
    title_hint = str(params.get("title", "")).strip()

    if not event_id:
        if not title_hint:
            return {
                "handled": True,
                "reply": "أعطني اسم الموعد أو ID الموعد اللي بدك تعدله — اطلب قائمة المواعيد أول.",
            }
        found = find_calendar_event_by_title(title_hint)
        if not found.get("found"):
            if found.get("ambiguous"):
                matches = "\n".join(
                    f"• {m['summary']}" for m in found.get("matches", [])[:5]
                )
                return {
                    "handled": True,
                    "reply": f"لقيت أكثر من موعد مطابق:\n{matches}\nاكتب الاسم بشكل أوضح.",
                }
            return {
                "handled": True,
                "reply": f"❌ {found.get('error', 'ما لقيت الموعد.')}",
            }
        event_id = found["event_id"]

    pending_payload = {
        "type": "calendar",
        "action": "confirm_update",
        "event_id": event_id,
    }
    for key in (
        "title",
        "start_iso",
        "end_iso",
        "location",
        "description",
        "reminder_minutes",
    ):
        val = params.get(key)
        if val is not None and str(val).strip():
            pending_payload[key] = val

    if not pending_payload.get("start_iso"):
        time_text = str(params.get("time_text", "")).strip()
        if time_text:
            parsed_cal = parse_reminder_time_ai(
                time_text, create_chat_completion_fn, return_json=True
            )
            if isinstance(parsed_cal, dict):
                if parsed_cal.get("success"):
                    pending_payload["start_iso"] = parsed_cal.get("remind_at_iso") or ""
                elif parsed_cal.get("suggested_iso"):
                    try:
                        sdt = datetime.fromisoformat(
                            parsed_cal.get("suggested_iso").replace("Z", "+00:00")
                        )
                        if sdt.tzinfo is not None:
                            sdt = sdt.astimezone(USER_TZ)
                        confirm_text = sdt.strftime("%d/%m/%Y %I:%M %p")
                    except Exception:
                        confirm_text = parsed_cal.get("suggested_iso")
                    title_display = params.get("title") or title_hint or event_id
                    session["pending_action"] = create_pending_action(
                        {
                            "type": "calendar",
                            "action": "confirm_update_with_time",
                            "event_id": event_id,
                            "suggested_start_iso": parsed_cal.get("suggested_iso"),
                            "title_display": title_display,
                            "confirmation_status": "pending",
                        }
                    )
                    save_session_fn(
                        session, session_file=session_file, mongo_db=mongo_db
                    )
                    return {
                        "handled": True,
                        "reply": f"ما فهمت الوقت بدقّة. تقصد تعدّل الموعد ليوم {confirm_text}?",
                    }
            else:
                if parsed_cal:
                    pending_payload["start_iso"] = parsed_cal

    title_display = params.get("title") or title_hint or event_id
    session["pending_action"] = create_pending_action(pending_payload)
    save_session_fn(session, session_file=session_file, mongo_db=mongo_db)
    return {"handled": True, "reply": f"متأكد بدك تعدل '{title_display}'؟"}


def _handle_add(
    params,
    user_message,
    *,
    session,
    session_file,
    mongo_db,
    tasks_file,
    save_session_fn,
):
    title = str(params.get("title", user_message)).strip()
    start_iso = str(params.get("start_iso", "")).strip()
    if not start_iso:
        return {"handled": True, "reply": "متى الموعد بالضبط؟"}

    result = add_calendar_event(
        title=title,
        start_iso=start_iso,
        end_iso=params.get("end_iso") or None,
        description=params.get("description", ""),
        location=params.get("location", ""),
        attendees=params.get("attendees", []),
        color_id=params.get("color_id", ""),
        reminder_minutes=int(params.get("reminder_minutes", 30)),
        recurrence=params.get("recurrence", ""),
        add_meet=bool(params.get("add_meet", False)),
    )
    reply = (
        f"✅ تم إضافة '{title}' على التقويم."
        if result.get("success")
        else "ما قدرت أضيف الموعد على التقويم."
    )
    reply_markup = None
    if result.get("success"):
        record_last_action(
            session,
            "calendar_added",
            summary=title,
            refs={
                "title": title,
                "start_iso": start_iso,
                "event_id": result.get("event_id"),
            },
        )
        conflict_result = run_conflict_check_after_calendar_add(
            event_id=str(result.get("event_id", "") or ""),
            title=title,
            start_iso=start_iso,
            end_iso=str(params.get("end_iso") or ""),
            description=str(params.get("description", "") or ""),
            mongo_db=mongo_db,
            tasks_file=tasks_file,
        )
        if isinstance(conflict_result, str):
            conflict_alert = conflict_result
            suggestions = []
        else:
            conflict_alert = str((conflict_result or {}).get("alert_text", "") or "")
            suggestions = list((conflict_result or {}).get("suggestions") or [])
        if conflict_alert:
            reply = f"{reply}\n\n⚠️ {conflict_alert}"
            if suggestions and str(result.get("event_id", "") or "").strip():
                conflict_id = stash_conflict_resolution(
                    session,
                    event_id=str(result.get("event_id", "") or ""),
                    title=title,
                    suggestions=suggestions,
                )
                reply_markup = create_conflict_inline_markup(conflict_id, suggestions)
                save_session_fn(session, session_file=session_file, mongo_db=mongo_db)

    tg_fu = params.get("telegram_follow_up")
    if tg_fu is None:
        tg_fu = params.get("telegram_followup")
    wants_followup = tg_fu is True or str(tg_fu).strip().lower() in {
        "1",
        "true",
        "yes",
        "نعم",
        "اه",
        "أه",
        "اهم",
        "ايوه",
        "aywa",
    }
    if result.get("success") and wants_followup:
        try:
            after_m = params.get("follow_up_after_minutes", 30)
            after_min = int(float(after_m)) if after_m is not None else 30
        except (TypeError, ValueError):
            after_min = 30

        parent_id = str(result.get("event_id") or "").strip()
        end_raw = params.get("end_iso")
        end_dt = None
        if end_raw:
            try:
                end_dt = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=USER_TZ)
                else:
                    end_dt = end_dt.astimezone(USER_TZ)
            except Exception:
                end_dt = None
        if end_dt is None:
            try:
                st = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                if st.tzinfo is None:
                    st = st.replace(tzinfo=USER_TZ)
                else:
                    st = st.astimezone(USER_TZ)
                end_dt = st + timedelta(hours=1)
            except Exception:
                end_dt = None

        if parent_id and end_dt is not None:
            ping_at = end_dt + timedelta(minutes=max(0, after_min))
            from app.features.google_calendar import add_calendar_followup_ping

            ping_res = add_calendar_followup_ping(
                parent_event_id=parent_id,
                parent_summary=title,
                ping_start_iso=ping_at.isoformat(),
            )
            if ping_res.get("success"):
                reply = (
                    f"{reply}\n\n📱 رح ابعتلك على التلغرام بعد الموقع الزمني "
                    f"({ping_at.strftime('%d/%m %H:%M')}) متابعة: هل خلص الموعد؟"
                )
            else:
                reply = f"{reply}\n\n(تنبيه: الموعد موجود لكن لم أجدد جدولة المتابعة على التلغرام.)"
        else:
            reply = (
                f"{reply}\n\n(تذكير المتابعة التلغرام لم يُضبط — تأكدي من وقت نهاية "
                "الموعد لو حابّة أتابع آلياً بعدها.)"
            )

    response = {"handled": True, "reply": reply}
    if reply_markup is not None:
        response["reply_markup"] = reply_markup
    return response


def handle_calendar_action(
    params: Dict[str, Any],
    *,
    user_message: str,
    normalized_user_message: str,
    session: Dict[str, Any],
    session_file,
    mongo_db,
    tasks_file,
    create_chat_completion_fn,
    save_session_fn,
) -> Dict[str, Any]:
    if not active_profile_is_owner():
        return {"handled": True, "reply": "التقويم خاص بنبيل 😊"}

    calendar_action = str(params.get("action", "add")).strip().lower()
    if calendar_action not in {
        "add",
        "delete",
        "delete_multi",
        "delete_range",
        "update",
        "list",
    }:
        return {"handled": True, "reply": "نوع إجراء التقويم غير صالح."}

    _common = dict(
        session=session,
        session_file=session_file,
        mongo_db=mongo_db,
        save_session_fn=save_session_fn,
    )

    if calendar_action == "delete_multi":
        return _handle_delete_multi(params, **_common)
    if calendar_action == "delete_range":
        return _handle_delete_range(params, **_common)
    if calendar_action == "list":
        return _handle_list(params)
    if calendar_action == "delete":
        return _handle_delete(params, **_common)
    if calendar_action == "update":
        return _handle_update(
            params, create_chat_completion_fn=create_chat_completion_fn, **_common
        )
    # add
    return _handle_add(params, user_message, tasks_file=tasks_file, **_common)
