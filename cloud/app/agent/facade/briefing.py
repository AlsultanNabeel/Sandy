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
    from app.features.google_tasks import load_tasks
    from app.features.google_calendar import list_events_for_date_range
    from app.features.weather import get_weather, format_weather_for_prompt

    now = datetime.now(USER_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    oauth_note = ""
    try:
        tasks = load_tasks(mongo_db=mongo_db, tasks_file=tasks_file)
        calendar_events = list_events_for_date_range(today_start, today_end, max_results=10)
    except GoogleOAuthReconnectNeeded as e:
        oauth_note = str(e)
        tasks = []
        calendar_events = []

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
    for e in calendar_events:
        start = (e.get("start", {}) or {}).get("dateTime") or (e.get("start", {}) or {}).get("date") or ""
        summary = (e.get("summary", "") or "").strip()
        label = ""
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(USER_TZ)
                label = dt.strftime("%I:%M %p")
            except Exception:
                label = start
        cal_lines.append(f"- {summary or 'موعد'} @ {label}" if label else f"- {summary or 'موعد'}")

    data_block = f"""الطقس: {weather_raw}
المزاج المرصود: {mood}
المهام النشطة ({len(active_tasks)}):
{chr(10).join(tasks_lines) if tasks_lines else "لا توجد مهام"}
مواعيد التقويم اليوم:
{chr(10).join(cal_lines) if cal_lines else "لا توجد مواعيد"}"""

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
- اذكري مواعيد التقويم لو في
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
    cal_block = "\n".join(cal_lines) if cal_lines else "ما في مواعيد"
    return (
        f"صباح الخير ☀️\n\n"
        f"🌤 {weather_raw}\n\n"
        f"📋 مهامك:\n{tasks_block}\n\n"
        f"📅 اليوم:\n{cal_block}"
    )
