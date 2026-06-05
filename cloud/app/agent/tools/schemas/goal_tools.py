"""#3 — Goal Tracking: تتبع أهداف المستخدم.

Sandy تحفظ الأهداف، تتابع تقدمها، وتذكّر بها استباقياً.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)

_COLL = "sandy_goals"


def _goals_db(ctx: "DispatchContext"):
    return ctx.mongo_db[_COLL] if ctx.mongo_db is not None else None


def goal_set(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يحفظ هدفاً جديداً."""
    text = str(args.get("goal") or args.get("text") or "").strip()
    if not text:
        return {"handled": True, "reply": "شو الهدف اللي تبي تحققه؟"}

    coll = _goals_db(ctx)
    chat_id = str((ctx.state or {}).get("chat_id", "default"))
    user_id = str((ctx.state or {}).get("user_id", "default"))
    deadline = str(args.get("deadline") or "").strip() or None

    if coll is not None:
        coll.insert_one({
            "chat_id": chat_id,
            "user_id": user_id,
            "text": text,
            "deadline": deadline,
            "status": "active",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })

    deadline_str = f" (الموعد: {deadline})" if deadline else ""
    return {"handled": True, "reply": f"سجّلت هدفك: {text}{deadline_str} 🎯\nبتابعك عليه!"}


def goal_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض الأهداف النشطة."""
    coll = _goals_db(ctx)
    if coll is None:
        return {"handled": True, "reply": "ما عندي أهداف محفوظة بعد."}

    chat_id = str((ctx.state or {}).get("chat_id", "default"))
    status_filter = str(args.get("status") or "active")

    docs = list(coll.find(
        {"chat_id": chat_id, "status": status_filter},
        {"_id": 1, "text": 1, "deadline": 1, "status": 1},
        sort=[("created_at", 1)],
        limit=10,
    ))

    if not docs:
        label = "مكتملة" if status_filter == "done" else "نشطة"
        return {"handled": True, "reply": f"ما في أهداف {label} حالياً."}

    lines = []
    for i, d in enumerate(docs, 1):
        deadline = f" ← {d['deadline']}" if d.get("deadline") else ""
        lines.append(f"{i}. {d['text']}{deadline}")

    header = "🎯 *أهدافك النشطة:*\n" if status_filter == "active" else "✅ *أهدافك المكتملة:*\n"
    return {"handled": True, "reply": header + "\n".join(lines)}


def goal_done(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يُكمّل هدفاً."""
    coll = _goals_db(ctx)
    if coll is None:
        return {"handled": True, "reply": "ما قدرت أوصل للأهداف."}

    chat_id = str((ctx.state or {}).get("chat_id", "default"))
    goal_text = str(args.get("goal") or args.get("text") or "").strip()

    if not goal_text:
        return {"handled": True, "reply": "أي هدف خلصت منه؟"}

    result = coll.find_one_and_update(
        {"chat_id": chat_id, "status": "active", "text": {"$regex": goal_text[:50], "$options": "i"}},
        {"$set": {"status": "done", "updated_at": datetime.now(timezone.utc)}},
    )

    if result:
        return {"handled": True, "reply": f"🎉 يييي! خلصت من هدف: *{result['text']}*\nأنا فخورة فيك!"}
    return {"handled": True, "reply": "ما لقيت هالهدف بين أهدافك النشطة. حاول بكلمة أخرى؟"}


GOAL_TOOLS = [
    {
        "name": "goal_set",
        "description": "سجّلي هدفاً جديداً للمستخدم وتابعيه",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "نص الهدف"},
                "deadline": {"type": "string", "description": "الموعد النهائي (اختياري، مثل: 2026-06-01)"},
            },
            "required": ["goal"],
        },
        "handler": goal_set,
    },
    {
        "name": "goal_list",
        "description": "اعرضي أهداف المستخدم النشطة أو المكتملة",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "active (افتراضي) | done"},
            },
            "required": [],
        },
        "handler": goal_list,
    },
    {
        "name": "goal_done",
        "description": "احتفلي وسجّلي اكتمال هدف",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "نص الهدف أو جزء منه"},
            },
            "required": ["goal"],
        },
        "handler": goal_done,
    },
]
