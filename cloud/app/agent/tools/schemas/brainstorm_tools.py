"""أدوات العصف الذهني / التخطيط.

ساندي تستعملها لتدير جلسة عصف: بدء · إضافة نقطة · إنهاء (تلخّص+تحفظ) ·
عرض الخطط المحفوظة. سلوك "شريكة التفكير" نفسه مُحقَن في soul.py طول الجلسة.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

logger = logging.getLogger(__name__)


def _chat_id(ctx: "DispatchContext") -> str:
    return str((ctx.state or {}).get("chat_id", "") or "")


def brainstorm_start(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يبدأ جلسة عصف ذهني حول موضوع."""
    from app.features import brainstorm

    topic = str(args.get("topic") or "").strip()
    chat_id = _chat_id(ctx)
    if not chat_id:
        return {"handled": True, "reply": "ما قدرت أحدد المحادثة."}
    doc = brainstorm.start_session(chat_id, topic)
    if not doc:
        return {"handled": True, "reply": "ما قدرت أبدأ الجلسة حالياً."}
    return {
        "handled": True,
        "reply": (
            f"تمام، بلّشنا عصف ذهني عن «{doc['topic']}» 🧠\n"
            "أنا هون أنظّم أفكارك مش أعملها بدالك. خلينا نبدأ — "
            "شو أول إشي ببالك نزبطه؟ لما نخلص قول «لخّصي» وبطلّعلك خطة كاملة، "
            "ولو بدك تلغي قول «ألغي العصف»."
        ),
    }


def brainstorm_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يسجّل نقطة/عنصر ذكره المستخدم ضمن جلسة العصف النشطة."""
    from app.features import brainstorm

    point = str(args.get("point") or "").strip()
    chat_id = _chat_id(ctx)
    n = brainstorm.add_point(chat_id, point)
    if n == 0:
        return {"handled": True, "reply": "ما في جلسة عصف نشطة — قول «خلينا نعصف ذهني عن...» نبدأ."}
    return {"handled": True, "reply": f"سجّلتها ✅ ({n} نقطة لهلأ). كمّل."}


def brainstorm_cancel(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يلغي جلسة العصف النشطة بدون حفظ خطة."""
    from app.features import brainstorm

    if brainstorm.cancel_session(_chat_id(ctx)):
        return {"handled": True, "reply": "تمام، ألغيت جلسة العصف 👍 رجعنا عادي."}
    return {"handled": True, "reply": "ما في جلسة عصف نشطة أصلاً."}


def brainstorm_finish(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينهي الجلسة النشطة، يلخّص كل شي بخطة كاملة، ويحفظها."""
    from app.features import brainstorm

    chat_id = _chat_id(ctx)
    result = brainstorm.finish_session(chat_id, ctx.create_chat_completion_fn)
    if not result:
        return {"handled": True, "reply": "ما في جلسة عصف نشطة ألخّصها."}
    plan_text, notion_url, topic = result
    tail = f"\n\n🔗 محفوظة بـ Notion: {notion_url}" if notion_url else "\n\n(محفوظة عندي — Notion مش مضبوط بعد)"
    return {"handled": True, "reply": f"{plan_text}{tail}"}


def brainstorm_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض قائمة خطط العصف المحفوظة مع ملخص كل وحدة."""
    from app.features import brainstorm

    plans = brainstorm.list_plans(_chat_id(ctx), limit=10)
    if not plans:
        return {"handled": True, "reply": "ما في خطط محفوظة لسا 🧠"}
    lines = []
    for i, p in enumerate(plans, 1):
        date = (p.get("finished_at") or "")[:10]
        summary = (p.get("summary") or "").strip()
        line = f"{i}. *{p.get('topic', 'خطة')}* ({date})"
        if summary:
            line += f"\n   ↳ {summary}"
        if p.get("notion_url"):
            line += f"\n   🔗 {p['notion_url']}"
        lines.append(line)
    return {"handled": True, "reply": "📋 خططك المحفوظة:\n\n" + "\n\n".join(lines)}


def brainstorm_show(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يعرض خطة محفوظة بالاسم/الموضوع."""
    from app.features import brainstorm

    query = str(args.get("query") or "").strip()
    p = brainstorm.get_plan(_chat_id(ctx), query)
    if not p:
        return {"handled": True, "reply": "ما لقيت خطة بهالوصف 🧠"}
    plan_text = p.get("plan_text") or "(الخطة فاضية)"
    tail = f"\n\n🔗 {p['notion_url']}" if p.get("notion_url") else ""
    return {"handled": True, "reply": f"{plan_text}{tail}"}


def _confirm_line(p: Dict[str, Any]) -> str:
    summary = (p.get("summary") or "").strip()
    s = f"قصدك خطة «{p.get('topic', '')}»"
    if summary:
        s += f" — {summary}"
    return s


def brainstorm_delete(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح حذف خطة ويطلب تأكيد بالملخص (التنفيذ عبر brainstorm_confirm)."""
    from app.features import brainstorm

    query = str(args.get("query") or "").strip()
    p = brainstorm.propose_action(_chat_id(ctx), query, "delete")
    if not p:
        return {"handled": True, "reply": "ما لقيت خطة بهالوصف 🧠"}
    return {"handled": True, "reply": f"{_confirm_line(p)}؟\nأحذفها نهائياً؟ قوليلي «آه احذفيها» ✅"}


def brainstorm_edit(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """يقترح تعديل خطة ويطلب تأكيد بالملخص (التنفيذ عبر brainstorm_confirm)."""
    from app.features import brainstorm

    query = str(args.get("query") or "").strip()
    change = str(args.get("change") or "").strip()
    if not change:
        return {"handled": True, "reply": "شو التعديل اللي بدك إياه بالخطة؟"}
    p = brainstorm.propose_action(_chat_id(ctx), query, "edit", change)
    if not p:
        return {"handled": True, "reply": "ما لقيت خطة أعدّلها بهالوصف 🧠"}
    return {"handled": True, "reply": f"{_confirm_line(p)}؟\nالتعديل: {change}\nأنفّذه؟ قوليلي «آه» ✅"}


def brainstorm_confirm(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    """ينفّذ آخر عملية حذف/تعديل معلّقة بعد تأكيد المستخدم."""
    from app.features import brainstorm

    result = brainstorm.confirm_pending(_chat_id(ctx), ctx.create_chat_completion_fn)
    if not result:
        return {"handled": True, "reply": "ما في عملية معلّقة أأكّدها."}
    op, data = result
    if not data.get("ok"):
        return {"handled": True, "reply": "ما قدرت ألاقي الخطة — يمكن انحذفت."}
    if op == "delete":
        return {"handled": True, "reply": f"حذفت خطة «{data['topic']}» ✅ (انأرشفت بـNotion كمان)"}
    tail = f"\n\n🔗 {data['notion_url']}" if data.get("notion_url") else ""
    return {"handled": True, "reply": f"عدّلت خطة «{data['topic']}» ✅\n\n{data.get('plan_text', '')}{tail}"}


BRAINSTORM_TOOLS = [
    {
        "name": "brainstorm_start",
        "description": (
            "ابدئي جلسة عصف ذهني/تخطيط لما المستخدم يقول «خلينا نعصف ذهني عن...» أو "
            "«بدي أنظّم أفكاري حول...» أو «ساعديني أخطّط لـ...». حطّي الموضوع في topic. "
            "في الجلسة إنتِ شريكة تفكير: تنظّمي مش تنفّذي."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "موضوع/هدف جلسة العصف"},
            },
            "required": ["topic"],
        },
        "handler": brainstorm_start,
    },
    {
        "name": "brainstorm_add",
        "description": (
            "سجّلي نقطة/عنصر مهم ذكره المستخدم خلال جلسة العصف (مثلاً «عندي مهارة كذا أضيفها» "
            "أو «ذكّريني أبعت كذا»). استعمليها كل ما يطلع عنصر جديد عشان ما ينساه التلخيص."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "point": {"type": "string", "description": "النقطة/العنصر كما ذكره المستخدم"},
            },
            "required": ["point"],
        },
        "handler": brainstorm_add,
    },
    {
        "name": "brainstorm_finish",
        "description": (
            "أنهي جلسة العصف ولخّصي كل شي بخطة كاملة جاهزة للتنفيذ + احفظيها. "
            "استعمليها لما يقول «لخّصي» أو «خلصنا» أو «طلّعيلي الخطة»."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": brainstorm_finish,
    },
    {
        "name": "brainstorm_cancel",
        "description": (
            "ألغي جلسة العصف النشطة بدون حفظ. استعمليها لما يقول «ألغي العصف» أو "
            "«بطّلي» أو «نسّي الموضوع» أو «اطلعي من وضع العصف»."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": brainstorm_cancel,
    },
    {
        "name": "brainstorm_list",
        "description": "اعرضي قائمة خطط العصف المحفوظة لما يسأل «شو خططي؟» أو «الخطط المحفوظة».",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": brainstorm_list,
    },
    {
        "name": "brainstorm_show",
        "description": "اعرضي خطة محفوظة بالموضوع/الوصف لما يطلب «ورجيني خطة كذا».",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "موضوع/وصف الخطة المطلوبة"},
            },
            "required": ["query"],
        },
        "handler": brainstorm_show,
    },
    {
        "name": "brainstorm_edit",
        "description": (
            "عدّلي خطة محفوظة لما يطلب تغيير/إضافة/حذف فيها (مثلاً «ضيف بند كذا لخطة "
            "اللينكدإن» أو «احذفي خطوة كذا»). بتفهمي الخطة الحالية وتعدّلي المطلوب بس."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "موضوع/وصف الخطة المراد تعديلها"},
                "change": {"type": "string", "description": "التعديل المطلوب (إضافة/حذف/تغيير)"},
            },
            "required": ["query", "change"],
        },
        "handler": brainstorm_edit,
    },
    {
        "name": "brainstorm_delete",
        "description": (
            "اقترحي حذف خطة محفوظة لما يطلب «احذفي خطة كذا». ما بتحذف فوراً — "
            "بترجّع تأكيد بملخص الخطة، والتنفيذ بعد ما يأكّد عبر brainstorm_confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "موضوع/وصف الخطة المراد حذفها"},
            },
            "required": ["query"],
        },
        "handler": brainstorm_delete,
    },
    {
        "name": "brainstorm_confirm",
        "description": (
            "نفّذي آخر عملية حذف/تعديل معلّقة بعد ما يأكّد المستخدم (مثلاً «آه»، «أكّدي»، "
            "«آه احذفيها»، «تمام نفّذي»). استعمليها فقط لو في عملية اقترحتيها قبل شوي."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": brainstorm_confirm,
    },
]
