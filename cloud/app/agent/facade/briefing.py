"""Briefing helpers for Sandy facade."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List

from app.utils.time import USER_TZ
from app.utils.google_oauth_errors import GoogleOAuthReconnectNeeded


def should_send_briefing(memory: Dict[str, Any], user_message: str) -> bool:
    now = datetime.now(USER_TZ)
    hour = now.hour
    msg_lower = user_message.strip().lower()
    briefing_triggers = [
        "ملخص يومي", "briefing", "daily briefing", "morning briefing",
        "الملخص اليومي", "ملخص صباحي", "الموجز اليومي",
        "ملخصي الصباح", "الملخص الصباح", "البريفلنج", "بريفلنج",
        "ملخص الصبح", "ملخصي اليوم",
    ]
    if any(t in msg_lower for t in briefing_triggers):
        return True
    if hour < 6 or hour >= 11:
        return False
    triggers = ["شو الأوضاع اليوم", "شو الاوضاع اليوم", "شو اوضاع اليوم", "شو الأوضاع"]
    if not any(t in msg_lower for t in triggers):
        return False
    state = memory.get("sandy_state", {})
    last_date = state.get("last_briefing_date", "")
    today = now.strftime("%Y-%m-%d")
    return last_date != today


_SHOPPING_PREFIXES = re.compile(
    r"^(اشتري|اشتر|شراء|شري|جيب|جيبي|ابعت|ابعث)\s+(ال)?", re.UNICODE
)


def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[\s​]+", " ", text)
    text = re.sub(r"[ًٌٍَُِّْ]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")
    text = _SHOPPING_PREFIXES.sub("", text).strip()
    return text


def _dedup(items: List[Dict]) -> List[Dict]:
    seen: set[str] = set()
    result = []
    for t in items:
        key = _normalize(t.get("text") or "")
        if key and key not in seen:
            seen.add(key)
            result.append(t)
    return result


def build_morning_briefing(*, memory: Dict[str, Any], mongo_db, tasks_file) -> str:
    from app.features.tasks_store import load_tasks
    from app.features.reminders_store import load_reminders
    from app.features.weather import get_weather, format_weather_for_prompt

    now = datetime.now(USER_TZ)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    oauth_note = ""
    try:
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
    except GoogleOAuthReconnectNeeded as e:
        oauth_note = str(e)
        tasks = []
    # Today's reminders take the slot calendar events used to fill.
    todays_reminders = []
    try:
        for r in load_reminders(max_results=50):
            try:
                dt = datetime.fromisoformat(r.get("remind_at", ""))
                if dt <= today_end:
                    todays_reminders.append(r)
            except Exception:
                continue
    except Exception:
        todays_reminders = []

    # Unread inbox — count + the top few, so the briefing can mention them.
    unread_lines: List[str] = []
    unread_count = 0
    try:
        from app.features.gmail import get_unread_emails

        unread = get_unread_emails(max_results=10)
        unread_count = len(unread)
        for e in unread[:3]:
            sender = (e.get("sender", "") or "").split("<", 1)[0].strip()
            unread_lines.append(f"- {sender}: {e.get('subject', '(بدون عنوان)')}")
    except Exception:
        pass

    city = str(memory.get("sandy_state", {}).get("home_city", "") or "").strip() or "October City"
    weather_raw = format_weather_for_prompt(get_weather(city))
    mood = str(memory.get("sandy_state", {}).get("mood", "neutral")).strip()

    # Raw data block the model writes the briefing from.
    active_tasks = _dedup([t for t in tasks if not t.get("done")])
    tasks_lines = []
    for t in active_tasks:
        text = (t.get("text") or "").strip()
        raw_due = str(t.get("due_at") or t.get("due") or "").strip()
        due_label = ""
        if raw_due:
            try:
                dt = datetime.fromisoformat(raw_due.replace("Z", "+00:00")).astimezone(USER_TZ)
                due_label = f" (موعد: {dt.strftime('%a %d/%m %I:%M %p')})"
            except Exception:
                pass
        tasks_lines.append(f"- {text}{due_label}")

    cal_lines = []
    for r in todays_reminders:
        text = (r.get("text", "") or "").strip()
        label = ""
        try:
            dt = datetime.fromisoformat(r.get("remind_at", "")).astimezone(USER_TZ)
            label = dt.strftime("%I:%M %p")
        except Exception:
            pass
        prefix = "🔁 " if r.get("is_recurring") else ""
        cal_lines.append(
            f"- {prefix}{text or 'تذكير'} @ {label}" if label else f"- {prefix}{text or 'تذكير'}"
        )

    data_block = f"""الطقس: {weather_raw}
المزاج المرصود: {mood}
المهام النشطة ({len(active_tasks)}):
{chr(10).join(tasks_lines) if tasks_lines else "لا توجد مهام"}
تذكيرات اليوم:
{chr(10).join(cal_lines) if cal_lines else "لا توجد تذكيرات"}
إيميلات غير مقروءة ({unread_count}):
{chr(10).join(unread_lines) if unread_lines else "لا يوجد"}"""

    if oauth_note:
        data_block += f"\nتحذير OAuth: {oauth_note}"

    from app.config import SANDY_PERSONALITY
    prompt = f"""{SANDY_PERSONALITY}

اكتبي ملخص صباحي مختصر وطبيعي لنبيل (ذكر) بناءً على البيانات أدناه فقط.

قواعد صارمة:
- ابدئي بـ"صباح الخير ☀️" بشكل عفوي شامي
- اذكري الطقس بجملة واحدة خفيفة
- اجمعي المشتريات الموجودة في البيانات بجملة واحدة — لا تخترعي مشتريات من عندك
- اذكري المهام الأخرى بإيجاز
- اذكري تذكيرات اليوم لو في
- اذكري عدد الإيميلات غير المقروءة وأهمها بجملة لو في
- اختمي قبل الجملة الشخصية باقتراح ذكي لترتيب اليوم بسطر أو سطرين (شو يبدأ فيه وليش)
- اختمي بجملة واحدة شخصية شامية مختلفة كل يوم (مذكر)
- لا تكتبي قوائم منقطة ولا عناوين رسمية
- الطول الكلي: ٥-٨ أسطر فقط

البيانات (هاي هي فقط — لا تضيفي شي من عندك):
{data_block}"""

    try:
        from app.integrations.azure_intent_client import AzureIntentClient
        client = AzureIntentClient()
        result = client._generate_with_gemini(
            prompt,
            response_mime_type="text/plain",
            max_output_tokens=300,
            temperature=0.85,
        )
        if result:
            return result
    except Exception:
        pass

    # Fallback when the model call fails: plain structured text.
    tasks_block = "\n".join(tasks_lines[:6]) if tasks_lines else "ما في مهام"
    cal_block = "\n".join(cal_lines) if cal_lines else "ما في تذكيرات"
    mail_block = f"📬 {unread_count} إيميل غير مقروء" if unread_count else ""
    return (
        f"صباح الخير ☀️\n\n"
        f"🌤 {weather_raw}\n\n"
        f"📋 مهامك:\n{tasks_block}\n\n"
        f"⏰ تذكيرات اليوم:\n{cal_block}"
        + (f"\n\n{mail_block}" if mail_block else "")
    )


def build_evening_summary(*, mongo_db, tasks_file) -> str:
    """ملخص المساء: شو خلص اليوم وشو ناطر بكرة. نص جاهز للإرسال أو "" لو ما في شي يُقال."""
    from datetime import timedelta

    from app.features.tasks_store import load_tasks, load_completed_tasks
    from app.features.reminders_store import load_reminders

    now = datetime.now(USER_TZ)
    today = now.date()
    tomorrow_end = datetime(
        today.year, today.month, today.day, 23, 59, 59, tzinfo=USER_TZ
    ) + timedelta(days=1)

    done_today: List[str] = []
    try:
        for t in load_completed_tasks(mongo_db=mongo_db, tasks_file=tasks_file):
            try:
                c = datetime.fromisoformat(str(t.get("completed_at") or ""))
                if c.astimezone(USER_TZ).date() == today:
                    done_today.append(t.get("text", ""))
            except Exception:
                continue
    except Exception:
        pass

    due_tomorrow: List[str] = []
    try:
        for t in load_tasks(mongo_db=mongo_db, tasks_file=tasks_file):
            raw = str(t.get("due_at") or t.get("due") or "")
            try:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                d = d.replace(tzinfo=USER_TZ) if d.tzinfo is None else d.astimezone(USER_TZ)
                if d.date() == today + timedelta(days=1):
                    due_tomorrow.append(t.get("text", ""))
            except Exception:
                continue
    except Exception:
        pass

    rem_tomorrow: List[str] = []
    try:
        for r in load_reminders(max_results=50):
            try:
                d = datetime.fromisoformat(r.get("remind_at", ""))
                if today < d.astimezone(USER_TZ).date() <= tomorrow_end.date():
                    rem_tomorrow.append(r.get("text", ""))
            except Exception:
                continue
    except Exception:
        pass

    if not done_today and not due_tomorrow and not rem_tomorrow:
        return ""

    parts = ["مساء الخير 🌙"]
    if done_today:
        parts.append(
            f"✅ أنجزت اليوم {len(done_today)}:\n" + "\n".join(f"- {x}" for x in done_today[:6])
        )
    if due_tomorrow:
        parts.append("📋 بكرة مستحق:\n" + "\n".join(f"- {x}" for x in due_tomorrow[:6]))
    if rem_tomorrow:
        parts.append("⏰ تذكيرات بكرة:\n" + "\n".join(f"- {x}" for x in rem_tomorrow[:6]))
    if not done_today:
        parts.append("ما سجلت إنجازات اليوم — بكرة فرصة جديدة 💙")
    return "\n\n".join(parts)


def build_weekly_stats(*, mongo_db, tasks_file) -> str:
    """إحصائية الأسبوع: المنجز مقابل المتراكم. "" لو ما في بيانات."""
    from datetime import timedelta

    from app.features.tasks_store import load_tasks, load_completed_tasks

    now = datetime.now(USER_TZ)
    week_ago = now - timedelta(days=7)

    done_week = 0
    try:
        for t in load_completed_tasks(mongo_db=mongo_db, tasks_file=tasks_file):
            try:
                c = datetime.fromisoformat(str(t.get("completed_at") or ""))
                if c.astimezone(USER_TZ) >= week_ago:
                    done_week += 1
            except Exception:
                continue
    except Exception:
        pass

    active = overdue = 0
    try:
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        active = len(tasks)
        today = now.date().isoformat()
        for t in tasks:
            raw = str(t.get("due_at") or t.get("due") or "")[:10]
            if raw and raw < today:
                overdue += 1
    except Exception:
        pass

    if done_week == 0 and active == 0:
        return ""

    lines = ["📊 إحصائية أسبوعك:"]
    lines.append(f"✅ أنجزت {done_week} مهمة هالأسبوع")
    lines.append(f"📋 ضايل {active} مهمة نشطة" + (f" (منها {overdue} متأخرة ⚠️)" if overdue else ""))
    if done_week >= 5:
        lines.append("أسبوع قوي — برافو 👏")
    elif overdue > 3:
        lines.append("في تراكم — بدك مساعدة نرتبهم بكرة الصبح؟")
    return "\n".join(lines)
