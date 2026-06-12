"""أدوات الحياة اليومية: تسوق + عادات + مصاريف.

Schemas + adapters لـ ToolRegistry — نفس نمط brainstorm_tools: الـ handler
يستدعي الـ store مباشرة بدون معالجات وسيطة. التسجيل في setup.py يخلّيها
متاحة تلقائياً من تيليجرام والويب وقناة الصوت.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext


# ── التسوق ───────────────────────────────────────────────────────────────────

def shopping_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.shopping_store import add_items

    items = args.get("items")
    if not items:
        single = str(args.get("item", "")).strip()
        items = [single] if single else []
    if not items:
        return {"handled": True, "reply": "شو بدك أضيف عالقائمة؟"}
    n = add_items([str(x) for x in items])
    if n == 0:
        return {"handled": True, "reply": "كلهم موجودين عالقائمة أصلاً 🛒"}
    return {"handled": True, "reply": f"🛒 ضفت {n} عالقائمة." if n > 1 else f"🛒 ضفت «{items[0]}»."}


def shopping_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.shopping_store import list_items

    items = list_items()
    if not items:
        return {"handled": True, "reply": "قائمة التسوق فاضية 🛒"}
    lines = [f"{i}. {x['text']}" for i, x in enumerate(items, 1)]
    return {"handled": True, "reply": "🛒 قائمة التسوق:\n" + "\n".join(lines)}


def shopping_check(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.shopping_store import check_item

    name = check_item(str(args.get("item", "")))
    if not name:
        return {"handled": True, "reply": "ما لقيت هالعنصر بالقائمة."}
    return {"handled": True, "reply": f"✅ شطبت «{name}» — مبروك الشراء!"}


def shopping_remove(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.shopping_store import remove_item

    name = remove_item(str(args.get("item", "")))
    if not name:
        return {"handled": True, "reply": "ما لقيت هالعنصر بالقائمة."}
    return {"handled": True, "reply": f"🗑 حذفت «{name}» من القائمة."}


# ── العادات ──────────────────────────────────────────────────────────────────

def habit_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.habits_store import add_habit

    name = str(args.get("name", "")).strip()
    if not name:
        return {"handled": True, "reply": "شو اسم العادة؟"}
    if add_habit(name):
        return {"handled": True, "reply": f"💪 سجلت عادة «{name}» — منبلش من اليوم!"}
    return {"handled": True, "reply": "هالعادة موجودة أصلاً أو الاسم فاضي."}


def habit_checkin(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.habits_store import checkin

    r = checkin(str(args.get("name", "")))
    if not r.get("ok"):
        return {"handled": True, "reply": "ما لقيت هالعادة — بدك أضيفها؟"}
    streak = r.get("streak", 1)
    if r.get("already"):
        return {"handled": True, "reply": f"مسجلة اليوم أصلاً ✅ — سلسلتك {streak} يوم 🔥"}
    cheer = " 🔥🔥" if streak >= 7 else " 🔥" if streak >= 3 else ""
    return {"handled": True, "reply": f"✅ «{r['name']}» — سلسلتك صارت {streak} يوم{cheer}"}


def habit_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.habits_store import list_habits

    habits = list_habits()
    if not habits:
        return {"handled": True, "reply": "ما في عادات مسجلة — قلّي «ضيفي عادة ...» ومنبدأ 💪"}
    lines = []
    for h in habits:
        mark = "✅" if h["done_today"] else "⬜"
        lines.append(f"{mark} {h['name']} — سلسلة {h['streak']} يوم")
    return {"handled": True, "reply": "💪 عاداتك:\n" + "\n".join(lines)}


# ── المصاريف ─────────────────────────────────────────────────────────────────

def expense_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.expenses_store import add_expense

    try:
        amount = float(args.get("amount", 0))
    except Exception:
        amount = 0
    if amount <= 0:
        return {"handled": True, "reply": "قديش المبلغ؟"}
    note = str(args.get("note", "")).strip()
    category = str(args.get("category", "")).strip()
    if add_expense(amount, note=note, category=category):
        label = note or category or ""
        return {"handled": True, "reply": f"💸 سجلت {amount:g}" + (f" — {label}" if label else "")}
    return {"handled": True, "reply": "ما قدرت أسجل المصروف."}


def expense_summary(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.expenses_store import month_summary

    days = int(args.get("days", 30) or 30)
    s = month_summary(days=days)
    if s["count"] == 0:
        return {"handled": True, "reply": "ما في مصاريف مسجلة بهالفترة 💸"}
    lines = [f"💸 مصاريف آخر {days} يوم: {s['total']:g} ({s['count']} عملية)"]
    for cat, total in list(s["by_category"].items())[:6]:
        lines.append(f"- {cat}: {total:g}")
    return {"handled": True, "reply": "\n".join(lines)}


# ── اليوميات ─────────────────────────────────────────────────────────────────

def journal_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.journal_store import add_entry

    text = str(args.get("text", "")).strip()
    if not text:
        return {"handled": True, "reply": "شو بدك أدوّن؟"}
    if add_entry(text):
        return {"handled": True, "reply": "📔 دوّنتها."}
    return {"handled": True, "reply": "ما قدرت أدوّن."}


def journal_show(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.journal_store import entries_for, recent_entries

    date = str(args.get("date", "")).strip()
    items = entries_for(date) if date else recent_entries(limit=10)
    if not items:
        return {"handled": True, "reply": "ما في تدوينات 📔"}
    lines = [f"- ({x['date']}) {x['text']}" for x in items]
    return {"handled": True, "reply": "📔 اليوميات:\n" + "\n".join(lines)}


def journal_search(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.journal_store import search_entries

    q = str(args.get("query", "")).strip()
    if not q:
        return {"handled": True, "reply": "عن شو أفتش باليوميات؟"}
    items = search_entries(q)
    if not items:
        return {"handled": True, "reply": f"ما لقيت شي عن «{q}» باليوميات."}
    lines = [f"- ({x['date']}) {x['text']}" for x in items[:8]]
    return {"handled": True, "reply": f"📔 لقيت عن «{q}»:\n" + "\n".join(lines)}


# ── القراءة ──────────────────────────────────────────────────────────────────

def book_add(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import add_book

    r = add_book(
        str(args.get("title", "")),
        status=str(args.get("status", "reading")),
        total_pages=int(args.get("total_pages", 0) or 0),
        cover_url=str(args.get("cover_url", "")),
        current_page=int(args.get("current_page", 0) or 0),
    )
    if r.get("ok"):
        return {"handled": True, "reply": f"📚 سجلت كتاب «{r['title']}»."}
    if r.get("error") == "exists":
        return {"handled": True, "reply": "هالكتاب مسجل أصلاً 📚"}
    return {"handled": True, "reply": "شو اسم الكتاب؟"}


def book_list(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import list_books

    status = str(args.get("status", "")).strip()
    books = list_books(status=status)
    if not books:
        return {"handled": True, "reply": "ما في كتب مسجلة 📚"}
    label = {"reading": "📖", "done": "✅", "wishlist": "🔖"}
    lines = []
    for b in books:
        prog = ""
        if b["total_pages"]:
            prog = f" — صفحة {b['current_page']} من {b['total_pages']}"
        elif b["current_page"]:
            prog = f" — صفحة {b['current_page']}"
        lines.append(f"{label.get(b['status'], '📚')} {b['title']}{prog}")
    return {"handled": True, "reply": "📚 كتبك:\n" + "\n".join(lines)}


def book_status(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import set_book_status

    r = set_book_status(str(args.get("title", "")), str(args.get("status", "")))
    if r.get("ok"):
        s = str(args.get("status", ""))
        word = {"done": "مكتمل 🎉", "reading": "قيد القراءة 📖", "wishlist": "عالقائمة 🔖"}.get(s, s)
        return {"handled": True, "reply": f"«{r['title']}» صار {word}"}
    return {"handled": True, "reply": "ما لقيت الكتاب أو الحالة غير صالحة."}


def reading_start(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import start_session

    r = start_session(str(args.get("title", "")))
    if r.get("ok"):
        return {
            "handled": True,
            "reply": f"📖 بلشنا — «{r['title']}» من صفحة {r['start_page']}. قراءة ممتعة! "
                     f"(قول «توقف مؤقت» للاستراحة أو «وقفت» للإنهاء)",
        }
    if r.get("error") == "already_active":
        return {"handled": True, "reply": "في جلسة قراءة شغالة أصلاً — قول «وقفت» لتسكيرها أول."}
    return {"handled": True, "reply": "شو الكتاب اللي بدك تقراه؟ (سمّيه وأنا بسجله)"}


def reading_pause(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import pause_session, resume_session

    if args.get("resume"):
        r = resume_session()
        if r.get("ok"):
            return {"handled": True, "reply": "📖 رجعنا — كمل قراءة!"}
        return {"handled": True, "reply": "ما في جلسة موقوفة مؤقتاً."}
    r = pause_session()
    if r.get("ok"):
        return {"handled": True, "reply": "⏸ وقفت العداد — قول «كمل قراءة» لما ترجع."}
    if r.get("error") == "already_paused":
        return {"handled": True, "reply": "هي أصلاً موقوفة مؤقتاً ⏸"}
    return {"handled": True, "reply": "ما في جلسة قراءة شغالة."}


def reading_stop(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.reading_store import stop_session

    page = args.get("page")
    r = stop_session(end_page=int(page) if page is not None else None)
    if not r.get("ok"):
        return {"handled": True, "reply": "ما في جلسة قراءة شغالة."}
    if r.get("needs_page"):
        return {"handled": True, "reply": "وين وصلت؟ قلي رقم الصفحة 📖"}
    msg = f"📖 سكّرت الجلسة — قريت {r['pages']} صفحة بـ {r['minutes']} دقيقة."
    if r.get("finished_book"):
        msg += f"\n🎉🎉 وخلّصت «{r['title']}» كله — مبرووك!"
    elif r.get("total_pages"):
        msg += f"\nوصلت صفحة {r['current_page']} من {r['total_pages']}."
    return {"handled": True, "reply": msg}


# ── التركيز ──────────────────────────────────────────────────────────────────

def focus_start(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.focus_store import start_focus

    r = start_focus(
        minutes=int(args.get("minutes", 25) or 25),
        label=str(args.get("label", "")),
    )
    if r.get("ok"):
        return {
            "handled": True,
            "reply": f"🎯 جلسة تركيز {r['minutes']} دقيقة بلشت — "
                     f"وجه الروبوت معك، وبنبهك لما تخلص. ركّز!",
        }
    if r.get("error") == "already_active":
        return {"handled": True, "reply": "في جلسة تركيز شغالة أصلاً — قول «خلصت» أو «الغي التركيز»."}
    return {"handled": True, "reply": "ما قدرت أبلش الجلسة."}


def focus_stop(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.focus_store import stop_focus

    completed = not bool(args.get("cancel"))
    r = stop_focus(completed=completed)
    if not r.get("ok"):
        return {"handled": True, "reply": "ما في جلسة تركيز شغالة."}
    if completed:
        return {"handled": True, "reply": f"🎉 برافو! ركزت {r['minutes']} دقيقة" + (f" على {r['label']}" if r.get("label") else "") + "."}
    return {"handled": True, "reply": "ألغيت جلسة التركيز — ولا يهمك."}


def focus_check(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.features.focus_store import focus_status

    s = focus_status()
    if not s.get("active"):
        return {"handled": True, "reply": "ما في جلسة تركيز شغالة 🎯"}
    return {
        "handled": True,
        "reply": f"🎯 جلسة شغالة — ضايل {s['remaining_min']} دقيقة من أصل {s['minutes']}.",
    }


LIFE_TOOLS = [
    {
        "name": "shopping_add",
        "description": "أضف عنصر أو أكثر لقائمة التسوق — «ضيفي حليب عالتسوق»",
        "parameters": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "عنصر واحد"},
                "items": {"type": "array", "items": {"type": "string"}, "description": "عدة عناصر دفعة واحدة"},
            },
            "required": [],
        },
        "handler": shopping_add,
    },
    {
        "name": "shopping_list",
        "description": "اعرض قائمة التسوق الحالية",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": shopping_list,
    },
    {
        "name": "shopping_check",
        "description": "اشطب عنصر من قائمة التسوق (انشترى) — «اشتريت الحليب»",
        "parameters": {
            "type": "object",
            "properties": {"item": {"type": "string", "description": "اسم العنصر"}},
            "required": ["item"],
        },
        "handler": shopping_check,
    },
    {
        "name": "shopping_remove",
        "description": "احذف عنصر من قائمة التسوق بدون شراء — «شيلي الحليب من القائمة»",
        "parameters": {
            "type": "object",
            "properties": {"item": {"type": "string", "description": "اسم العنصر"}},
            "required": ["item"],
        },
        "handler": shopping_remove,
    },
    {
        "name": "habit_add",
        "description": "أضف عادة يومية جديدة للتتبع — «ضيفي عادة الرياضة»",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "اسم العادة"}},
            "required": ["name"],
        },
        "handler": habit_add,
    },
    {
        "name": "habit_checkin",
        "description": "سجل إنجاز عادة اليوم — «تمرنت اليوم» / «صليت» / «قريت»",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "اسم العادة"}},
            "required": ["name"],
        },
        "handler": habit_checkin,
    },
    {
        "name": "habit_list",
        "description": "اعرض العادات وسلاسل الإنجاز — «وين وصلت بعاداتي»",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": habit_list,
    },
    {
        "name": "expense_add",
        "description": "سجل مصروف — «صرفت عشرين على غدا». المبلغ إجباري",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "المبلغ"},
                "note": {"type": "string", "description": "على شو (غدا، بنزين...)"},
                "category": {"type": "string", "description": "تصنيف اختياري: أكل/مواصلات/فواتير/ترفيه/أخرى"},
            },
            "required": ["amount"],
        },
        "handler": expense_add,
    },
    {
        "name": "expense_summary",
        "description": "ملخص المصاريف — «قديش صرفت هالشهر»",
        "parameters": {
            "type": "object",
            "properties": {"days": {"type": "number", "description": "الفترة بالأيام (افتراضي 30)"}},
            "required": [],
        },
        "handler": expense_summary,
    },
    {
        "name": "journal_add",
        "description": "دوّن باليوميات — «دوني إني رحت عالطبيب اليوم»",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "نص التدوينة"}},
            "required": ["text"],
        },
        "handler": journal_add,
    },
    {
        "name": "journal_show",
        "description": "اعرض اليوميات — «شو دونتيلي اليوم/مبارح»",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "تاريخ YYYY-MM-DD اختياري — بدونه آخر التدوينات"}},
            "required": [],
        },
        "handler": journal_show,
    },
    {
        "name": "journal_search",
        "description": "فتش باليوميات — «إيمتى آخر مرة رحت عالطبيب؟»",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "كلمة البحث"}},
            "required": ["query"],
        },
        "handler": journal_search,
    },
    {
        "name": "book_add",
        "description": "سجل كتاب — «ضيفي كتاب العادات الذرية 300 صفحة». status: reading|done|wishlist",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "اسم الكتاب"},
                "status": {"type": "string", "description": "reading (افتراضي) | done | wishlist (ناوي يقراه)"},
                "total_pages": {"type": "number", "description": "عدد الصفحات الكلي (اختياري)"},
                "current_page": {"type": "number", "description": "الصفحة الحالية لو بلش فيه (اختياري)"},
                "cover_url": {"type": "string", "description": "رابط صورة الغلاف (اختياري)"},
            },
            "required": ["title"],
        },
        "handler": book_add,
    },
    {
        "name": "book_list",
        "description": "اعرض الكتب — «شو كتبي» / «شو قيد القراءة». فلتر اختياري: reading|done|wishlist",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string", "description": "reading | done | wishlist — فاضي للكل"}},
            "required": [],
        },
        "handler": book_list,
    },
    {
        "name": "book_status",
        "description": "غيّر حالة كتاب — «خلصت كتاب كذا» (done) / «حطيه بقائمة القراءة» (wishlist)",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "اسم الكتاب"},
                "status": {"type": "string", "description": "reading | done | wishlist"},
            },
            "required": ["title", "status"],
        },
        "handler": book_status,
    },
    {
        "name": "reading_start",
        "description": "ابدأ جلسة قراءة — «بديت أقرا» / «بدي أقرا كتاب كذا». بدون اسم بكمل بآخر كتاب قيد القراءة",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "اسم الكتاب (اختياري)"}},
            "required": [],
        },
        "handler": reading_start,
    },
    {
        "name": "reading_pause",
        "description": "توقف مؤقت أو استئناف للقراءة — «توقف مؤقت» / «كمل قراءة» (resume=true)",
        "parameters": {
            "type": "object",
            "properties": {"resume": {"type": "boolean", "description": "true للاستئناف بعد توقف مؤقت"}},
            "required": [],
        },
        "handler": reading_pause,
    },
    {
        "name": "reading_stop",
        "description": "أنهِ جلسة القراءة — «وقفت». بدون رقم صفحة ساندي بتسأل «وين وصلت؟» وبعدها نادِها مع page",
        "parameters": {
            "type": "object",
            "properties": {"page": {"type": "number", "description": "رقم الصفحة اللي وصلها"}},
            "required": [],
        },
        "handler": reading_stop,
    },
    {
        "name": "focus_start",
        "description": "ابدأ جلسة تركيز — «بدي أركز ساعة عالدراسة». الروبوت بساير الجلسة وبنبهك لما تخلص",
        "parameters": {
            "type": "object",
            "properties": {
                "minutes": {"type": "number", "description": "المدة بالدقائق (افتراضي 25)"},
                "label": {"type": "string", "description": "على شو التركيز (اختياري)"},
            },
            "required": [],
        },
        "handler": focus_start,
    },
    {
        "name": "focus_stop",
        "description": "أنهِ جلسة التركيز — «خلصت» (إنجاز واحتفال) أو «الغي التركيز» (cancel=true)",
        "parameters": {
            "type": "object",
            "properties": {"cancel": {"type": "boolean", "description": "true للإلغاء بدون احتفال"}},
            "required": [],
        },
        "handler": focus_stop,
    },
    {
        "name": "focus_check",
        "description": "حالة جلسة التركيز — «قديش ضايل؟»",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": focus_check,
    },
]
