"""Hardware + Research + Image + Utility tools — schemas + adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from app.agent.tools.dispatcher import DispatchContext

def _NOOP_SAVE(*a, **kw): return None


def _call_dispatch(action_type: str, params: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    from app.agent.executor.dispatch import execute_operational_action
    return execute_operational_action(
        action_type=action_type,
        params=params,
        user_message=ctx.user_message,
        normalized_user_message=ctx.normalized_message,
        session=ctx.session,
        session_file=None,
        mongo_db=ctx.mongo_db,
        tasks_file=None,
        create_chat_completion_fn=ctx.create_chat_completion_fn,
        save_session_fn=_NOOP_SAVE,
    )


# Hardware

def hardware_face(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    expression = args.get("expression", "neutral")
    print(f"[hardware_face] called with expression={expression!r} args={args}")
    return _call_dispatch("hardware", {"command": "face_change", "value": expression}, ctx)

def hardware_servo(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("hardware", {"command": "servo_move", "value": args.get("angle", 90)}, ctx)

def hardware_buzzer(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("hardware", {"command": "buzzer", "value": args.get("pattern", "beep")}, ctx)

def hardware_snapshot(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    prompt = args.get("prompt", "صفي اللي بتشوفيه بإيجاز")
    mode = (args.get("mode") or "image").strip().lower()
    if mode not in ("image", "describe", "both"):
        mode = "image"
    payload = {"command": "snapshot", "prompt": prompt, "mode": mode}
    if args.get("angle") is not None:
        payload["angle"] = args["angle"]
    return _call_dispatch("hardware", payload, ctx)

def hardware_distance(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("hardware", {"command": "distance"}, ctx)

def hardware_room_scan(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    prompt = args.get("prompt", "صفي الغرفة كاملة من هذه الزوايا")
    return _call_dispatch("hardware", {"command": "room_scan", "prompt": prompt}, ctx)

def hardware_track_object(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    target = args.get("target", "الشخص")
    return _call_dispatch("hardware", {"command": "track_object", "target": target}, ctx)


# Research

def research_web(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("research", args, ctx)

def research_places(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("places", args, ctx)


# Image

def image_generate(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("image", {"action": "generate", **args}, ctx)

def image_describe(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("image", {"action": "describe", **args}, ctx)

def image_edit(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("image_edit", {"prompt": args.get("prompt", "")}, ctx)


# Utility

def get_time(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("time", {}, ctx)

def get_weather(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("weather", args, ctx)

def cost_report(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("cost", args, ctx)

def github_info(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("github", args, ctx)

def heroku_info(args: Dict[str, Any], ctx: "DispatchContext") -> Dict[str, Any]:
    return _call_dispatch("heroku", args, ctx)


# Schemas

OTHER_TOOLS = [
    # Hardware
    {
        "name": "hardware_face",
        "description": "غيّر تعبير/مزاج وجه الروبوت. مرّر expression بالقيمة المناسبة: ابتسمي→happy, ضحكة كبيرة→big_happy, حزينة→sad, ابكي→cry, غاضبة→angry, متفاجئة→surprised, فضولية→curious, فكري→think, نعسانة→sleepy, ملولة→bored, تثاءبي→yawn, متعاطفة→empathetic, متحمسة→excited, خجولة→shy, محتارة→confused, لطيفة→cute, غمزة→wink, بوسة→kiss, قلوب بعينيك→heart_eyes, تنبهي→alert, هادئة→calm, محبة→love",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "enum": [
                        "happy", "big_happy", "sad", "angry", "surprised", "curious",
                        "think", "sleepy", "bored", "yawn", "asleep", "excited", "shy",
                        "confused", "cute", "empathetic", "love", "cry", "wink", "kiss",
                        "heart_eyes", "alert", "calm", "smirk", "idle"
                    ],
                    "description": "اسم المزاج المطلوب"
                }
            },
            "required": ["expression"],
        },
        "handler": hardware_face,
    },
    {
        "name": "hardware_servo",
        "description": "حرّك السيرفو بزاوية محددة",
        "parameters": {
            "type": "object",
            "properties": {"angle": {"type": "integer", "description": "0-180 درجة"}},
            "required": [],
        },
        "handler": hardware_servo,
    },
    {
        "name": "hardware_buzzer",
        "description": "شغّل نغمة من البازر. القيم: startup, wake, sleep, alert, sad, error, stop",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "enum": ["startup", "wake", "sleep", "alert", "sad", "error", "stop"],
                    "description": "اسم النغمة"
                }
            },
            "required": ["pattern"],
        },
        "handler": hardware_buzzer,
    },
    {
        "name": "hardware_snapshot",
        "description": (
            "التقاط صورة من ESP32-CAM. لازم تحدد `mode` بدقة. القاعدة:\n"
            "• `image` ⇐ المستخدم بدو **يشوف** الصورة. أمثلة: 'شو شايفة قدامك'، 'شو شايفة'، 'صوّري'، 'صوّر'، 'ابعتيلي صورة'، 'خذي صورة'، 'وريني'، 'وريني شو قدامك'. **'شو شايفة قدامك' = image دائماً** لأنه بدو يشوف. الكلمة 'شايفة' بحد ذاتها مش تعني وصف نصي.\n"
            "• `describe` ⇐ المستخدم طلب **وصف نصي** صراحة بدون رؤية الصورة. أمثلة: 'اوصفيلي شو قدامك'، 'وصفيلي اللي قدامك'، 'احكيلي شو في الغرفة'، 'صفيلي'. الفعل 'وصف/يصف/يحكي' لازم يكون ظاهر.\n"
            "• `both` ⇐ المستخدم طلب الاثنين صراحة. أمثلة: 'صوّري واوصفي'، 'صورة + وصف'، 'ابعتيلي صورة ووصف'.\n"
            "ضع `angle` (٥-١٧٥) لو حدد زاوية. يمين=٣٠، وسط=٩٠، يسار=١٥٠."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["image", "describe", "both"],
                    "description": "نوع الرد: image=صورة فقط، describe=وصف فقط، both=الاثنين",
                },
                "prompt": {
                    "type": "string",
                    "description": "السؤال أو التوجيه لتحليل الصورة (مستخدم فقط في describe/both)",
                },
                "angle": {
                    "type": "integer",
                    "description": "زاوية السيرفو ٥-١٧٥ — تُضبط قبل التصوير",
                },
            },
            "required": ["mode"],
        },
        "handler": hardware_snapshot,
    },
    {
        "name": "hardware_distance",
        "description": "اقرأ المسافة الحالية من حساس HC-SR04 (cm). استدعِ هذا الـ tool عند أي سؤال عن قراءة المسافة/البُعد/كم cm/فيه شي قريب.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": hardware_distance,
    },
    {
        "name": "hardware_room_scan",
        "description": "مسح الغرفة كاملة — Sandy تلف السيرفو إلى عدة زوايا (٣٠/٦٠/٩٠/١٢٠/١٥٠)، تأخذ صورة بكل زاوية، وترسلها كلها إلى Vision لتحليل موحّد. استدعي عند: 'امسحي الغرفة', 'شو في الغرفة', 'فحصي اللي حواليك', 'دوّري شوفي'.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "سؤال أو تركيز للتحليل (اختياري)"},
            },
            "required": [],
        },
        "handler": hardware_room_scan,
    },
    {
        "name": "hardware_track_object",
        "description": "تتبّع جسم/شخص — Sandy تصوّر، تحدد موقعه (يمين/يسار/وسط)، تحرّك السيرفو نحوه، ثم تأكد بصورة ثانية. استدعي عند: 'اتبعيني', 'دوري على وجهي', 'لاحقي حركتي'.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "الهدف (وجه/شخص/جسم محدد)"},
            },
            "required": [],
        },
        "handler": hardware_track_object,
    },
    # Research
    {
        "name": "research_web",
        "description": (
            "ابحث في الويب عن معلومات حقيقية وحديثة. "
            "استخدم هذه الأداة دائماً عندما: "
            "(١) المستخدم يقول 'ابحث' أو 'بحث' أو 'وين' أو 'شو آخر أخبار' أو 'أخبار', "
            "(٢) السؤال عن أحداث جارية أو أخبار أو أسعار أو تطورات حديثة, "
            "(٣) المعلومة تتغير مع الوقت ولا يمكن الإجابة من الذاكرة بدقة. "
            "لا تستخدم chat_respond بدلاً منها لأسئلة البحث."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "نص البحث"},
                "count": {"type": "integer", "description": "عدد النتائج (افتراضي 5)"},
            },
            "required": ["query"],
        },
        "handler": research_web,
    },
    {
        "name": "research_places",
        "description": "ابحث عن أماكن قريبة أو معلومات مكان",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "اسم المكان أو النوع"}},
            "required": ["query"],
        },
        "handler": research_places,
    },
    # Image
    {
        "name": "image_generate",
        "description": (
            "ولّد صورة جديدة بالذكاء الاصطناعي. استخدم هذه الأداة لما "
            "الأونر يطلب إنشاء صورة بأي من هذه الأفعال + كلمة 'صورة':\n"
            "- 'اعملي صورة لـ...' / 'اعمليلي صورة كذا'\n"
            "- 'حطي صورة عن...' / 'حطيلي صورة'\n"
            "- 'ضيفي صورة...' / 'ضيفيلي صورة'\n"
            "- 'افتحي صورة...' (لما السياق يدل على إنشاء)\n"
            "- 'ولّدي/ارسمي/صمّمي/جيبيلي صورة...'\n"
            "- طلب 'نسخة ثانية / variation' من صورة سابقة\n\n"
            "🚨 استدعي هاد الـ FC **دائماً** لطلبات إنشاء الصور — حتى لو "
            "STM فيه محادثة سابقة عن صورة. لا تردّي شات بدون استدعاء."
        ),
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "وصف الصورة المطلوبة"}},
            "required": ["prompt"],
        },
        "handler": image_generate,
    },
    {
        "name": "image_describe",
        "description": "وصف/تحليل صورة موجودة (المستخدم رفع صورة وسأل 'شو فيها'، 'اوصفها')",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "سؤال عن الصورة"}},
            "required": [],
        },
        "handler": image_describe,
    },
    {
        "name": "image_edit",
        "description": (
            "عدّل الصورة الأخيرة (Sandy ولّدها أو المستخدم رفعها). "
            "استخدم فقط لما المستخدم يطلب تعديل صريح: "
            "'خلّيها كذا', 'عدّل الصورة', 'غيّر لون/خلفية', 'شيل/زيد...'. "
            "لا تستخدمه لـ variation/نسخة ثانية — استخدم image_generate لهذه الحالات."
        ),
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string", "description": "وصف التعديل المطلوب"}},
            "required": ["prompt"],
        },
        "handler": image_edit,
    },
    # Utility
    {
        "name": "get_time",
        "description": "اعرض الوقت والتاريخ الحالي",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "handler": get_time,
    },
    {
        "name": "get_weather",
        "description": "اجلب حالة الطقس لمدينة",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "اسم المدينة"}},
            "required": [],
        },
        "handler": get_weather,
    },
    {
        "name": "cost_report",
        "description": "عرض تقرير تكاليف الخدمات (Azure/Heroku/OpenAI...)",
        "parameters": {
            "type": "object",
            "properties": {"provider": {"type": "string", "description": "all|azure|heroku|openai"}},
            "required": [],
        },
        "handler": cost_report,
    },
    {
        "name": "github_info",
        "description": "اعرض معلومات من GitHub (commits/issues/PRs)",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "commits|issues|pull_requests|stats"},
                "repo": {"type": "string", "description": "اسم الـ repo"},
            },
            "required": [],
        },
        "handler": github_info,
    },
    {
        "name": "heroku_info",
        "description": "اعرض حالة Heroku (logs/status/hours)",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string", "description": "logs|status|hours|diagnose"}},
            "required": [],
        },
        "handler": heroku_info,
    },
]
