"""E3 — FC tool: schedule_future_message.

Sandy تستقبل: "ذكريني بعد سنة بهذا الكلام" → تحفظ رسالة + تاريخ تسليم.
عند أول رد بعد تاريخ التسليم → soul_node يحقن النص ليُذكر في الرد.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


_NUMS_AR = {"يوم": 1, "اسبوع": 7, "أسبوع": 7, "شهر": 30, "شهور": 30, "سنة": 365, "سنوات": 365}


def _parse_when(when_str: str) -> datetime | None:
    """يحاول تفسير 'بعد X يوم/شهر/سنة' أو ISO date."""
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}", when_str):
            return datetime.fromisoformat(when_str.replace("Z", ""))
    except Exception:
        pass

    m = re.search(r"بعد\s+(\d+)\s+(يوم|اسبوع|أسبوع|شهر|شهور|سنة|سنوات)", when_str)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        days = _NUMS_AR.get(unit, 1) * amount
        return datetime.utcnow() + timedelta(days=days)

    if "بعد سنة" in when_str:
        return datetime.utcnow() + timedelta(days=365)
    if "بعد شهر" in when_str:
        return datetime.utcnow() + timedelta(days=30)
    if "بعد اسبوع" in when_str or "بعد أسبوع" in when_str:
        return datetime.utcnow() + timedelta(days=7)

    return None


def schedule_message_to_self(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحفظ رسالة مجدولة للمستقبل."""
    text = str(args.get("text") or args.get("message") or "").strip()
    when_str = str(args.get("when") or args.get("deliver_at") or "").strip()

    if not text:
        return {"handled": True, "reply": "شو الرسالة اللي تبي توصلك بالمستقبل؟"}
    if not when_str:
        return {"handled": True, "reply": "متى تبي توصلك الرسالة؟ (مثلاً: بعد سنة، 2027-05-16)"}

    deliver_at = _parse_when(when_str)
    if not deliver_at:
        return {"handled": True, "reply": "ما فهمت الموعد. جرب: 'بعد سنة' أو تاريخ مثل '2027-05-16'."}

    if ctx.mongo_db is None:
        return {"handled": True, "reply": "ما قدرت أوصل للذاكرة الآن، حاول لاحقاً."}

    chat_id = str((ctx.state or {}).get("chat_id", "default"))
    user_id = str((ctx.state or {}).get("user_id", "default"))

    from app.agent.future_messages import schedule_future_message
    ok = schedule_future_message(chat_id, user_id, text, deliver_at, ctx.mongo_db)
    if not ok:
        return {"handled": True, "reply": "صار خطأ بالحفظ، جرب مرة ثانية."}

    return {
        "handled": True,
        "reply": f"تمام، حفظت الرسالة لنفسك 💌\nرح أذكرك فيها يوم {deliver_at.strftime('%Y/%m/%d')}",
    }


FUTURE_MESSAGE_TOOLS = [
    {
        "name": "schedule_message_to_self",
        "description": "احفظي رسالة من المستخدم لنفسه في المستقبل (مثلاً: 'ذكريني بعد سنة بأن X')",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "نص الرسالة"},
                "when": {"type": "string", "description": "متى تُسلَّم — 'بعد سنة' أو تاريخ ISO مثل 2027-05-16"},
            },
            "required": ["text", "when"],
        },
        "handler": schedule_message_to_self,
    },
]
