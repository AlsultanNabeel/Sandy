"""ميزة العصف الذهني / التخطيط.

ساندي تصير شريكة تفكير: تختار هدف، تنظّم الأفكار خطوة خطوة، تجمّع النقاط،
وبالآخر تطلّع خطة كاملة تتحفظ في Mongo
عشان ترجعلها لاحقاً.

التخزين: مجموعة sandy_brainstorms:
  {chat_id, topic, status: active|done, points[], plan_text,
   started_at, finished_at}

بدون Mongo كل دالة بترجّع فاضي بهدوء.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_COLL = "sandy_brainstorms"
_PENDING = "sandy_bs_pending"
_mongo_db = None


def _extract_summary(plan_text: str) -> str:
    """ملخص سطرين: سطر «> ملخص:»، وإلا قسم «الهدف»، وإلا أول سطر مش عنوان. ~180 حرف."""
    lines = [ln.strip() for ln in (plan_text or "").splitlines()]
    # 1) سطر الملخص الصريح (blockquote)
    for ln in lines:
        low = ln.lstrip("> ").strip()
        if low.startswith("ملخص"):
            return low.split(":", 1)[-1].strip()[:180] or low[:180]
    # 2) قسم الهدف
    goal: List[str] = []
    in_goal = False
    for ln in lines:
        if ln.startswith("## "):
            if in_goal:
                break
            in_goal = "هدف" in ln
            continue
        if in_goal and ln:
            goal.append(ln)
    if goal:
        return " ".join(goal)[:180]
    # 3) أول سطر مش عنوان
    for ln in lines:
        if ln and not ln.startswith("#") and not ln.startswith(">"):
            return ln[:180]
    return ""


def init_brainstorm(mongo_db) -> None:
    """يُستدعى مرّة عند الإقلاع."""
    global _mongo_db
    _mongo_db = mongo_db
    if mongo_db is None:
        return
    try:
        mongo_db[_COLL].create_index([("chat_id", 1), ("status", 1)], background=True)
        mongo_db[_COLL].create_index([("chat_id", 1), ("finished_at", -1)], background=True)
        logger.info("[brainstorm] ready")
    except Exception as e:  # noqa: BLE001
        logger.debug("[brainstorm] index skipped: %s", e)


def is_available() -> bool:
    return _mongo_db is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# الجلسة النشطة
def get_active(chat_id: Any) -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        return _mongo_db[_COLL].find_one({"chat_id": str(chat_id), "status": "active"})
    except Exception:  # noqa: BLE001
        return None


def start_session(chat_id: Any, topic: str) -> Optional[Dict[str, Any]]:
    """يبدأ جلسة عصف جديدة (يلغي أي جلسة نشطة سابقة)."""
    if not is_available():
        return None
    cid = str(chat_id)
    # أي جلسة نشطة سابقة → ملغاة
    _mongo_db[_COLL].update_many(
        {"chat_id": cid, "status": "active"},
        {"$set": {"status": "abandoned", "finished_at": _now()}},
    )
    doc = {
        "chat_id": cid,
        "topic": (topic or "").strip() or "جلسة عصف",
        "status": "active",
        "points": [],
        "plan_text": "",
        "started_at": _now(),
        "finished_at": "",
    }
    res = _mongo_db[_COLL].insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


def add_point(chat_id: Any, point: str) -> int:
    """يضيف نقطة للجلسة النشطة. يرجّع عدد النقاط الكلي (0 لو ما في جلسة)."""
    point = (point or "").strip()
    active = get_active(chat_id)
    if not active or not point:
        return 0
    _mongo_db[_COLL].update_one(
        {"_id": active["_id"]},
        {"$push": {"points": {"text": point, "at": _now()}}},
    )
    return len(active.get("points", [])) + 1


def cancel_session(chat_id: Any) -> bool:
    active = get_active(chat_id)
    if not active:
        return False
    _mongo_db[_COLL].update_one(
        {"_id": active["_id"]},
        {"$set": {"status": "abandoned", "finished_at": _now()}},
    )
    return True


# التلخيص والحفظ
def _synthesize_plan(
    topic: str, points: List[str], create_chat_completion_fn, conversation: str = ""
) -> str:
    """LLM → خطة عربية منظّمة جاهزة للتنفيذ من النقاش + النقاط."""
    points_block = "\n".join(f"- {p}" for p in points) if points else "(ما في نقاط مسجّلة)"
    convo_block = f"\nنصّ النقاش (الأهم — استخرجي منه القرارات والتفاصيل):\n{conversation}\n" if conversation else ""
    prompt = (
        f"الموضوع: {topic}\n"
        f"{convo_block}\n"
        f"النقاط المسجّلة:\n{points_block}\n\n"
        "حوّلي هذا لخطة عربية **كاملة ومنظّمة وجاهزة للتنفيذ** بصيغة markdown:\n"
        "# عنوان الخطة\n"
        "> ملخص: (سطر-سطرين يوضّحوا الخطة عن شو بالضبط — **إلزامي**، مباشرة تحت العنوان)\n"
        "## الهدف (سطر-سطرين)\n"
        "## الخطوات (مرقّمة 1. 2. 3.، كل خطوة واضحة وقابلة للتنفيذ)\n"
        "## عناصر أجهّزها (كل عنصر بصيغة تشيك ليست: ابدئي السطر بـ `- [ ] `)\n"
        "## ملاحظات (اختياري)\n"
        "اكتبي بالعامية الفلسطينية الواضحة، خطوات عملية مش كلام عام. "
        "استعملي `- [ ] ` للعناصر اللي بدها تأشير، و`1.` للخطوات المرقّمة، و`-` للنقاط العادية. "
        "بدون مقدمات قبل العنوان."
    )
    try:
        resp = create_chat_completion_fn(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1400,
            prefer_azure=True,
        )
        out = (resp.choices[0].message.content or "").strip()
        if out:
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[brainstorm] synthesize failed: %s", e)
    # fallback بسيط بدون LLM
    return (
        f"# خطة: {topic}\n\n## النقاط\n" + (points_block or "- (لا نقاط)")
    )


def finish_session(
    chat_id: Any, create_chat_completion_fn
) -> Optional[Tuple[str, str, str]]:
    """يلخّص الجلسة النشطة → خطة ويحفظها في Mongo. يرجّع (الخطة، "", الموضوع) أو None."""
    active = get_active(chat_id)
    if not active:
        return None
    topic = active.get("topic", "جلسة عصف")
    points = [p.get("text", "") for p in active.get("points", []) if p.get("text")]

    # نصّ النقاش من STM — هو الأساس للتلخيص (مش بس النقاط المسجّلة)
    conversation = ""
    try:
        from app.agent.graph.graph import load_stm
        cid = str(chat_id)
        msgs = load_stm(cid, cid)
        lines = []
        for m in msgs:
            who = "نبيل" if m.get("role") == "user" else "ساندي"
            txt = (m.get("content") or "").strip()
            if txt:
                lines.append(f"{who}: {txt}")
        conversation = "\n".join(lines)[-4000:]
    except Exception as e:  # noqa: BLE001
        logger.debug("[brainstorm] stm load skipped: %s", e)

    plan_text = _synthesize_plan(topic, points, create_chat_completion_fn, conversation)

    _mongo_db[_COLL].update_one(
        {"_id": active["_id"]},
        {"$set": {
            "status": "done",
            "plan_text": plan_text,
            "summary": _extract_summary(plan_text),
            "finished_at": _now(),
        }},
    )
    # الرابط الفاضي بظل بالعقد القديم للتوافق (كان رابط Notion قبل ما نشيله).
    return plan_text, "", topic


# التعديل (مبني على فهم الخطة الحالية)
def _revise_plan(current_plan: str, change: str, create_chat_completion_fn) -> str:
    """LLM يعيد كتابة الخطة كاملة بعد تطبيق التعديل المطلوب فقط — بدون اختراع."""
    prompt = (
        "هاي الخطة الحالية كاملة:\n\n"
        "-----\n"
        f"{current_plan}\n"
        "-----\n\n"
        f"التعديل المطلوب: {change}\n\n"
        "أعيدي كتابة **الخطة كاملة** بصيغة markdown مع تطبيق التعديل المطلوب **فقط**. "
        "قواعد صارمة:\n"
        "• حافظي على كل البنود والأقسام الموجودة زي ما هي بالضبط، عدا اللي طلب يتغيّر.\n"
        "• لا تخترعي بنود أو تفاصيل مش مذكورة، ولا تحذفي إشي ما طُلب حذفه.\n"
        "• لو التعديل إضافة → ضيفيها بالقسم المناسب. لو حذف → احذفي بس المطلوب. لو تغيير → غيّري بس المطلوب.\n"
        "• **حدّثي سطر «> ملخص:» ليعكس الخطة بعد التعديل** (لو التعديل غيّر فحوى الخطة).\n"
        "• نفس البنية والعناوين. بدون مقدمات قبل العنوان."
    )
    try:
        resp = create_chat_completion_fn(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1600,
            prefer_azure=True,
        )
        out = (resp.choices[0].message.content or "").strip()
        if out:
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[brainstorm] revise failed: %s", e)
    return current_plan  # فشل التعديل → نرجّع الأصل بدون تخريب


def update_plan(
    chat_id: Any, query: str, change: str, create_chat_completion_fn
) -> Optional[Tuple[str, str, str]]:
    """يعدّل خطة محفوظة بناءً على فهم محتواها الحالي. يرجّع (الخطة الجديدة، الرابط، الموضوع)."""
    p = get_plan(chat_id, query)
    if not p:
        return None
    current = p.get("plan_text", "")
    if not current:
        return None
    revised = _revise_plan(current, change, create_chat_completion_fn)

    _mongo_db[_COLL].update_one(
        {"_id": p["_id"]},
        {"$set": {"plan_text": revised, "summary": _extract_summary(revised),
                  "updated_at": _now()}},
    )
    return revised, "", p.get("topic", "")


# الاسترجاع
def list_plans(chat_id: Any, limit: int = 10) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    try:
        cur = _mongo_db[_COLL].find(
            {"chat_id": str(chat_id), "status": "done"}
        ).sort("finished_at", -1).limit(limit)
        return list(cur)
    except Exception:  # noqa: BLE001
        return []


def delete_plan(chat_id: Any, query: str) -> Tuple[bool, str]:
    """يحذف خطة محفوظة من الذاكرة. يرجّع (تمّ؟، الموضوع/رسالة)."""
    p = get_plan(chat_id, query)
    if not p:
        return False, "ما لقيت خطة بهالوصف."
    _mongo_db[_COLL].delete_one({"_id": p["_id"]})
    return True, str(p.get("topic", "الخطة"))


_AR_NORM = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "ـ": "", "ء": "",
})


def _norm(text: str) -> str:
    """تطبيع للمطابقة: حروف صغيرة + توحيد الألف/الهمزة/التاء المربوطة + إزالة التشكيل."""
    import re as _re
    t = str(text or "").lower().translate(_AR_NORM)
    t = _re.sub(r"[ً-ْ]", "", t)  # تشكيل
    return t


# تأكيد قبل التعديل/الحذف
def _set_pending(chat_id: Any, op: str, plan_id: Any, change: str = "") -> None:
    if not is_available():
        return
    _mongo_db[_PENDING].replace_one(
        {"_id": str(chat_id)},
        {"_id": str(chat_id), "op": op, "plan_id": plan_id, "change": change, "at": _now()},
        upsert=True,
    )


def get_pending(chat_id: Any) -> Optional[Dict[str, Any]]:
    if not is_available():
        return None
    try:
        return _mongo_db[_PENDING].find_one({"_id": str(chat_id)})
    except Exception:  # noqa: BLE001
        return None


def _clear_pending(chat_id: Any) -> None:
    if is_available():
        _mongo_db[_PENDING].delete_one({"_id": str(chat_id)})


def propose_action(
    chat_id: Any, query: str, op: str, change: str = ""
) -> Optional[Dict[str, Any]]:
    """يلاقي الخطة المقصودة ويخزّن عملية معلّقة (delete/edit) للتأكيد. يرجّع الخطة أو None."""
    p = get_plan(chat_id, query)
    if not p:
        return None
    _set_pending(chat_id, op, p["_id"], change)
    return p


def confirm_pending(
    chat_id: Any, create_chat_completion_fn
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """ينفّذ العملية المعلّقة. يرجّع (op, نتيجة) أو None لو ما في معلّق."""
    pend = get_pending(chat_id)
    if not pend:
        return None
    _clear_pending(chat_id)
    try:
        doc = _mongo_db[_COLL].find_one({"_id": pend["plan_id"]})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        return pend.get("op", ""), {"ok": False, "topic": ""}
    topic = doc.get("topic", "الخطة")
    if pend["op"] == "delete":
        _mongo_db[_COLL].delete_one({"_id": doc["_id"]})
        return "delete", {"ok": True, "topic": topic}
    if pend["op"] == "edit":
        revised = _revise_plan(doc.get("plan_text", ""), pend.get("change", ""),
                               create_chat_completion_fn)
        _mongo_db[_COLL].update_one(
            {"_id": doc["_id"]},
            {"$set": {"plan_text": revised, "summary": _extract_summary(revised),
                      "updated_at": _now()}},
        )
        return "edit", {"ok": True, "topic": topic, "plan_text": revised}
    return pend.get("op", ""), {"ok": False, "topic": topic}


def get_plan(chat_id: Any, query: str) -> Optional[Dict[str, Any]]:
    """يرجّع أفضل خطة مطابقة للوصف (يبحث بالعنوان + المحتوى مع تطبيع عربي).

    query فاضي → الأحدث. لا تطابق → None (ما نرجّع خطة غلط — مهم للحذف/التعديل).
    """
    plans = list_plans(chat_id, limit=50)
    if not plans:
        return None
    q = _norm(query).strip()
    if not q:
        return plans[0]
    # توكنات لها معنى (طول ≥ 2)، نتجاهل كلمات حشو شائعة
    stop = {"خطه", "خطة", "ال", "عن", "تاع", "تبع", "بتاع", "حق"}
    tokens = [t for t in q.split() if len(t) >= 2 and t not in stop]
    if not tokens:
        return plans[0]  # وصف عام بس (مثلاً "الخطة") → الأحدث
    best, best_score = None, 0
    for p in plans:
        hay = _norm(p.get("topic", "") + " " + p.get("plan_text", ""))
        score = sum(1 for tok in tokens if tok in hay)
        if score > best_score:
            best, best_score = p, score
    return best if best_score > 0 else None
