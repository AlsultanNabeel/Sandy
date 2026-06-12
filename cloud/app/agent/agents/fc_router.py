"""Function-calling routing for Sandy.

Builds a compact prompt from the current message, recent context, and any
pending state, then asks the model for structured JSON.

From the picked tool it derives intent and routing_hint, and carries through
mood, persona fields, confidence, and any multi-tool calls. If the model call
fails it tries GPT routing, then falls back to a safe default.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.agent.graph.state import SandyState, merge_state
from app.integrations.azure_intent_client import AzureIntentClient

logger = logging.getLogger(__name__)

# Handled by routing logic, not the ToolDispatcher.
_META_TOOL_NAMES = frozenset({
    "ask_clarification",
    "request_confirmation",
    "chat_respond",
    "chat_emotional",
    "pending_confirm",
    "pending_reject",
    "pending_select",
})

_FC_DEFAULT: Dict[str, Any] = {
    "function_call": {"name": "chat_respond", "args": {"type": "general"}},
    "mood": "neutral",
    "persona_intensity": "standard",
    "persona_snippet": "",
    "confidence": 0.7,
}

_FC_SYSTEM_TEMPLATE = """\
أنت محلل نية لمساعد Sandy. أرجع JSON فقط — لا نص خارجه.

أدوات ثابتة دائماً متاحة:
- chat_respond(type:str) — دردشة عامة أو لا يوجد طلب واضح
- chat_emotional() — دعم عاطفي (stress/frustration/sadness)
- ask_clarification(question:str) — confidence<0.7 في إرسال/تعديل فقط
- request_confirmation(summary:str) — للعمليات الخطيرة فقط عدا task_delete وtask_complete (هم يطلبون تأكيداً تلقائياً)
- pending_confirm() | pending_reject() | pending_select(choice:str) — الرد على pending نشط

قواعد:

🚨 قاعدة عُليا — لها أولوية على أي قاعدة أخرى:

أي رسالة فيها **فعل action + object واضح** → استدعِ الـ FC المخصص حصراً.
ممنوع تماماً chat_respond في هذه الحالات — حتى لو STM فيه محادثة مشابهة.

أفعال الـ action:
- إنشاء: اعملي/اعمليلي/سوّيلي/أنشئي/حطي/حطيلي/ضيفي/أضيفي/افتحي (بدون "من جديد")
- إغلاق: اقفلي/سكّري/بطّلي/أغلقي
- إعادة فتح: افتحي + "من جديد" / reopen / فعّلي
- تعليق: علّقي/ضيفي تعليق
- عرض: جيبيلي/اعرضيلي/اعرضي/وين/كم عندي
- إنشاء صورة: ولّدي/ارسمي/صمّمي + صورة
- تعديل: عدّلي/غيّري/صلّحي
- حذف: احذفي/امسحي/شيلي

Objects شائعة: issue, صورة, task/مهمة, تذكير/reminder, موعد/event,
ايميل/email, ملف/file, commit, repo, PR, تعليق/comment.

أمثلة محسومة (case-closed) — احفظها كنماذج تعلّم:

# مجال الـ issues
- "اعملي issue اسمه memory leak"     → github_create_issue
- "حطي issue عن R14"                  → github_create_issue
- "افتحي issue" / "افتحي issue جديد" / "اعملي issue" (بدون عنوان)
                                       → ask_clarification(question="شو اسم الـ issue؟")
- "اقفلي issue 8"                      → github_close_issue (ولو مغلق — الـ handler يحسم)
- "سكّري issue memory R14"            → github_close_issue (title resolution)
- "افتحي issue 5 من جديد"             → github_reopen_issue
- "علّقي على issue 5 بـ تجربة"       → github_comment_issue
- "جيبيلي الـ issues المفتوحة"        → github_issues

# مجال الصور
- "اعملي صورة لكلب يلعب"             → image_generate (ليس chat)
- "ولّدي صورة منظر طبيعي"            → image_generate
- "ارسميلي logo بسيط"                 → image_generate
- "عدّلي الصورة، شيلي الخلفية"        → image_edit (الـ object = صورة)
- "غيّري لون الصورة"                  → image_edit

# مجال المواعيد — صارت تذكيرات محلية (ما في تقويم خارجي)
- "اعملي موعد بكره الساعة 5"          → reminder_create (ليس image_edit حتى لو فعل "اعملي")
- "غيّري الموعد لبكره"                → reminder_update (الـ object = موعد، ليس صورة!)
- "احذفي الموعد"                       → reminder_delete
- "اعرضي مواعيدي"                     → reminder_list
# ⚠️ صياغات المواعيد الضمنية (بدون فعل صريح) — كلها → reminder_create
- "في عندي مقابلة الأربعاء"           → reminder_create
- "عندي مشوار يوم السبت"              → reminder_create
- "سجّلي عندك إنّي مشغول الإثنين"     → reminder_create (ليس memory_store — هذا حدث زمني)
- "احجزيلي وقت السبت 7"               → reminder_create

# مجال المهام
- "اعملي مهمة اشتري الحليب"           → task_create
- "ضيفي مهمة جديدة"                    → task_create
- 'ضيفي مهمة "ابعتي ايميل لأحمد"'      → task_create (المحتوى داخل "..." اسم المهمة فقط — ليس أمر فعلي)
- "اعرضي مهامي"                        → task_list
- "غيّري المهمة 3 لـ ..."             → task_update
- "خلّصت المهمة 3" / "كملت ..."        → task_complete
- "ارجعي المهمة 3 / خليها مش مكتملة"  → task_uncomplete
- "علّميها لسّى مش مخلّصة"             → task_uncomplete (انتبه لـ "مش"!)
- "احذفي مهامي المكتملة"              → task_delete

# مجال التذكير
- "ذكّريني أشتري حليب بكره"           → reminder_create
- "ضيفي تذكير الساعة 9"               → reminder_create
- "اعرضي تذكيراتي"                     → reminder_list
- "احذفي تذكير اليوم"                  → reminder_delete
# ⚠️ negation — "ما/مش/بطّلي + تذكير" = حذف
- "ما تذكّريني بشي"                    → reminder_delete (ليس chat)
- "بطّلي التذكير"                      → reminder_delete
- "الغي تذكير اليوم"                   → reminder_delete

# مجال الإيميل
- "اقرئي ايميلاتي"                     → email_read
- "اعرضي الإيميلات الجديدة"           → email_read
- "افتحي الـ inbox" / "افتحي البريد"  → email_read
- "ابعتي ايميل لأحمد عن X"            → email_send
- "ردّي على ايميل أحمد بـ ..."        → email_reply

# مجال الهاردوير
- **مهم:** أي طلب موجّه لـ Sandy لإظهار تعبير على وجهها (الشاشة) هو **hardware_face**
  وليس chat. عندها وجه فعلي على OLED. أمثلة: ابتسمي/ضحكي/اعملي وجه فرح/زعلي/
  ازعلي/غضبي/ابكي/تفاجئي/اغمزي/غمزي/اعطيني بوسة/قلوب بعينيك/تثاءبي/كوني خجولة/
  كوني نعسانة/كوني محتارة/كوني فضولية/فكري/تنبهي/متحمسة/متعاطفة/لطيفة/كيوت/حبي/بحبك/اهدي/هادئة
  → **hardware_face** (لازم تمرير expression — راجع enum)
- "لفي راسك ٩٠" / "حركي السيرفو" / "لفي يمين/شمال" → hardware_servo (angle)
  خريطة الزوايا الاتجاهية (الزاوية بين 5 و 175، المركز = 90):
    • "لفي يمين قليلاً" / "شوية يمين" / "شو اللي على يمينك" → angle=70
    • "لفي يمين" / "تطلعي يمين"                              → angle=40
    • "لفي اقصى اليمين"                                       → angle=5
    • "لفي شمال قليلاً" / "شوية يسار"                         → angle=110
    • "لفي شمال" / "تطلعي يسار"                               → angle=140
    • "لفي اقصى اليسار"                                       → angle=175
    • "ارجعي للوسط" / "للمنتصف"                               → angle=90
- "اعملي صوت تنبيه" / "صوت حزين"        → hardware_buzzer
- "تحركي للأمام/الخلف/يمين/يسار"        → hardware (base)
- كاميرا — لازم تحدد `mode`:
    • "شو شايفة قدامك" / "صوّري" / "خذي سناب" / "ابعتيلي صورة" / "وريني" → hardware_snapshot mode=image (صورة فقط بدون وصف)
    • "اوصفيلي شو قدامك" / "وصفيلي اللي قدامك" / "احكيلي شو في الغرفة" → hardware_snapshot mode=describe (نص فقط)
    • "صوّري واوصفي" / "شو شايفة قدامك اوصفيلي" / "صورة + وصف" → hardware_snapshot mode=both

# مجال البحث والمعلومات
- "ابحثي عن آخر أخبار X"               → research_web
- "وين مطعم قريب؟" / "places حولي"    → research_places
- "شو الطقس اليوم"                    → get_weather
- "كم الساعة"                          → get_time

# مجال الذاكرة
- "احفظي إنّي بحب القهوة"             → memory_store
- "شو تتذكري عني؟"                     → memory_recall

# الـ chat الحقيقي (لا fc)
- "كيفك اليوم؟"                        → chat_respond
- "حدّثيني عن نفسك"                    → chat_respond
- "تصبحي على خير"                      → chat_respond
- "شكراً يا حبيبتي"                    → chat_respond + persona=playful
- "ما عم أعرف شو الصح"                → chat_emotional (إذا mood ضاغط)
# 🚨 التحيات الخالصة دائماً chat_respond — حتى لو STM فيه طلب سابق فشل
- "مرحبا" / "مرحبتين" / "هلا" / "أهلين" / "سلام" / "السلام عليكم" → chat_respond
- "صباح الخير" / "مساء الخير" / "مساء النور"                       → chat_respond
- ❌ ممنوع تستخدم STM لاستنتاج "المستخدم يبي يكمل الطلب السابق" من تحية فقط

# 🎯 حالات الالتباس الشائعة (cross-domain)
- "غيّري الموعد" (object=موعد)         → reminder_update (ليس image_edit)
- "غيّري الصورة" (object=صورة)         → image_edit (ليس reminder_update)
- "احذفي المهمة"                       → task_delete (ليس reminder_delete)
- "احذفي الموعد"                       → reminder_delete (ليس task_delete)
- "اعملي issue" (object=issue)          → github_create_issue (ليس image_edit)
- "اعملي صورة" (object=صورة)           → image_generate (ليس chat)
- "افتحي issue 5 من جديد" (verb+suffix) → github_reopen_issue (ليس github_create)
- "افتحي issue اسمه X" (verb بدون suffix) → github_create_issue

🔴 ممنوعات صريحة:
- ❌ chat_respond إذا الرسالة action+object
- ❌ تخطّي الـ FC بحجة "حكينا عنه قبل قليل" — STM قديم بطبيعته
- ❌ افتراض إن الحالة ما تغيّرت — الـ handler يفحص live من GitHub/DB
- ❌ image_edit إذا الـ object هو issue/task/file (مش صورة)
- ❌ research_web إذا في FC مخصص للموضوع
- ❌ **إعادة طلب من STM**: لو الرسالة الحالية تحية/شات عام، ممنوع تستدعي FC بحجة "المستخدم يبي يكمل طلب سابق فشل". الرسالة الحالية حصراً.
- ❌ **multi-FC من STM**: ممنوع تجمع function_calls من رسالة سابقة + رسالة حالية. كل رسالة في STM **نُفّذت سابقاً** — حتى لو الرسالة الحالية تبدأ بـ "و" (واو عاطفة) فهي طلب جديد مستقل، مش استكمال.

⚠️ ask_clarification — استخدم بحذر شديد، فقط في حالة غموض كامل:

✅ يُستخدم فقط لما:
- الـ object غير موجود تماماً: "احذفي"، "اعملي"، "غيّري" بدون أي تكملة
- الـ object غامض جداً: "اعملي حاجة"، "ضيفيلي شي"

❌ ممنوع ask_clarification في الحالات التالية — استدعِ الـ FC مباشرةً:
- الـ object واضح حتى لو التفاصيل ناقصة:
  • "احذفي الموعد" → reminder_delete (الـ handler يسأل أي موعد)
  • "غيّري المهمة 3" → task_update (الـ handler يسأل عن التغيير)
  • "اعملي موعد بكره" → reminder_create (الـ handler يكمل التفاصيل)
  • "احذفي التذكير" → reminder_delete (الـ handler يحدد)
  • "غيّري الصورة" → image_edit (الـ handler يسأل التعديل المطلوب)
- التفاصيل ناقصة لكن النية واضحة → نادِ الـ FC، الـ handler يحل التفاصيل
- الرسالة فيها فعل + object → القاعدة العليا (FC حصرًا)

القاعدة: ask_clarification هي **الملاذ الأخير**، ليست الخيار الأول.

🔴 حالات MUST يستدعي الـ FC، حتى لو بدت ناقصة (لا تستخدم ask_clarification):
- "غيّري الموعد بكره" → reminder_update
- "غيّري الموعد لبكره الساعة 6" → reminder_update
- "ضيفي مهمة جديدة الجمعة" → task_create
- "غيّري الصورة" → image_edit
- "احذفي الموعد" → reminder_delete
- "احذفي التذكير" → reminder_delete

🚨 إحصائياً: نسبة استخدام ask_clarification يجب أن تكون أقل من 5٪.
إذا ترددت بين FC وask_clarification → اختر الـ FC دائماً.
الـ handler مصمّم للتعامل مع التفاصيل الناقصة بنفسه.

مسؤوليتك: الاستدعاء الصحيح. التعامل مع التكرار/التفاصيل الناقصة/idempotency: الـ handler.

──────────────────────────────────────────────

- pending_state نشط + رد قصير (تمام/لأ/رقم) → pending_confirm|pending_reject|pending_select
- pending_state نوعه clarification: اعتبر الرسالة الحالية إجابة للسؤال السابق. اقرأ "الرسالة الأصلية للمستخدم" واستنتج الـ FC المناسب، ثم استدعِه مع الرسالة الحالية كمعطى ناقص. أمثلة:
  • أصلية="افتحي issue" + حالية="memory leak" → github_create_issue(title="memory leak")
  • أصلية="ضيفي مهمة" + حالية="اشتري حليب" → task_create(title="اشتري حليب")
  • أصلية="ابعتي ايميل لأحمد" + حالية="عن الاجتماع" → email_send(to="أحمد", body="عن الاجتماع")
  ممنوع chat_respond في هذي الحالة.
- task_delete أو task_complete مع all=true → استدعِهم مباشرةً بـ all=true — لا تستخدم request_confirmation
- 'عندي اجتماع/موعد/لقاء/دكتور + وقت أو تاريخ' → reminder_create (ليس reminder_list)
- 'شو عندي/مواعيدي/برنامجي' → reminder_list
- 'ابحث/بحث/وين/شو آخر/أخبار/لوين وصل/شو صار' أو أي سؤال عن أحداث جارية أو معلومات تتغير مع الوقت → research_web (ليس chat_respond)
- لا يوجد طلب واضح → chat_respond
- مزاج ضاغط (stressed/frustrated): persona_intensity=empathetic + persona_snippet كسؤال لطيف (مثل "بدك تاخد نفس؟") — لا توجيه مباشر
- أسئلة فلسفية/وجودية/عن المعنى والحياة والكون → chat_respond + persona_intensity=empathetic
- المستخدم يشكر Sandy أو يثني عليها → chat_respond + persona_intensity=playful
- **الرسالة الحالية حصراً** تحتوي على طلبين مستقلين أو أكثر → استخدم function_calls (array) بدل function_call
  ⚠️ ممنوع تماماً: استخراج FC من رسائل STM السابقة وضمّها مع FC الرسالة الحالية. الرسائل السابقة **نُفّذت بالفعل** — الـ STM للـ context فقط.
  أمثلة على المنع:
  • STM: "ضيفي مهمة حليب" + الحالية: "وضيفي تذكير نوم" → function_call=reminder_create (واحد فقط — المهمة نُفّذت قبل)
  • STM: "اعرضي مهامي" + الحالية: "واحذفي رقم 3" → function_call=task_delete (واحد فقط)
  ✅ multi-FC الصحيحة: الرسالة الحالية وحدها فيها واو عاطفة + 2 verbs + 2 objects، مثل "ضيفي مهمة حليب واضبط تذكير الساعة 9"
- لغة الجسد الرقمية (E4) — حلل هذه الإشارات لتدقيق mood:
  · CAPS أو !!! متعددة أو "بسرعة" → urgency=high
  · رسائل قصيرة جداً بدون ايموجي ("تمام"، "اوكي") → mood ربما متحفظ/متعب
  · ايموجي كثيرة (3+) أو "ييي" أو "🎉" → mood=happy/excited
  · هاهاها أو هه أو 😂 → mood=playful + intensity=playful
  · "..." متكرر أو رسالة مقطّعة → mood=hesitant/قلق

schema لطلب واحد:
{{
  "function_call": {{"name": "<tool_name>", "args": {{<tool_args>}}}},
  "mood": "calm|stressed|frustrated|sad|happy|neutral",
  "sandy_face": "<اسم تعبير من القائمة أدناه — إلزامي>",
  "persona_intensity": "minimal|standard|empathetic|playful|formal",
  "persona_snippet": "<جملة واحدة قصيرة دافئة>",
  "confidence": <0.0-1.0>
}}

schema لطلبات متعددة:
{{
  "function_calls": [
    {{"name": "<tool1>", "args": {{<args1>}}}},
    {{"name": "<tool2>", "args": {{<args2>}}}}
  ],
  "mood": "calm|stressed|frustrated|sad|happy|neutral",
  "sandy_face": "<اسم تعبير من القائمة أدناه — إلزامي>",
  "persona_intensity": "minimal|standard|empathetic|playful|formal",
  "persona_snippet": "<جملة واحدة قصيرة دافئة>",
  "confidence": <0.0-1.0>
}}

sandy_face لازم يكون واحد من (اختر اللي يناسب رد Sandy العاطفي للمستخدم):
  happy, big_happy, sad, angry, surprised, curious, thinking, focused,
  sleepy, bored, excited, shy, confused, love, proud, worried, playful,
  silly, grumpy, hopeful, grateful, disappointed, alert, calm, idle

أمثلة:
  • المستخدم حزين → sandy_face=worried
  • المستخدم يشكر Sandy → sandy_face=grateful
  • المستخدم يقول "أحبك" → sandy_face=love
  • المستخدم يطلب تذكير → sandy_face=happy أو calm
  • سؤال غريب → sandy_face=confused أو curious
  • نكتة → sandy_face=playful أو silly

──────────────────────────────────────────────
أدوات هذا الـ specialist (متغيرة حسب النطاق):
{tool_catalog}"""


def _build_tool_catalog(declarations: List[Dict[str, Any]]) -> str:
    """Compact one-line-per-tool catalog to drop into the prompt."""
    lines = []
    for d in declarations:
        name = d.get("name", "")
        desc = d.get("description", "")
        params = d.get("parameters") or {}
        props = params.get("properties") or {}
        required = set(params.get("required") or [])

        parts = []
        for pname, pschema in props.items():
            ptype = str(pschema.get("type", "str"))[:3]
            suffix = "" if pname in required else "?"
            parts.append(f"{pname}{suffix}:{ptype}")

        args_str = ", ".join(parts)
        lines.append(f"- {name}({args_str}) — {desc}")
    return "\n".join(lines)


def _parse_fc_response(raw: str) -> Dict[str, Any]:
    """Parse the FC JSON, with a regex fallback and a safe default."""
    if not raw:
        return dict(_FC_DEFAULT)

    text = raw.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return dict(_FC_DEFAULT)

    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return dict(_FC_DEFAULT)

    result = dict(_FC_DEFAULT)

    # Multi-tool: function_calls array
    fcs_raw = parsed.get("function_calls")
    if fcs_raw and isinstance(fcs_raw, list) and len(fcs_raw) >= 2:
        valid_fcs = [
            {"name": str(f["name"]), "args": f.get("args") or {}}
            for f in fcs_raw
            if isinstance(f, dict) and f.get("name")
        ]
        if len(valid_fcs) >= 2:
            result["function_call"] = valid_fcs[0]  # backward compat
            result["function_calls"] = valid_fcs
            for field in ("mood", "sandy_face", "persona_intensity", "persona_snippet", "confidence"):
                if field in parsed:
                    result[field] = parsed[field]
            return result

    # Single tool: function_call
    fc = parsed.get("function_call")
    if not fc or not isinstance(fc, dict) or not fc.get("name"):
        return dict(_FC_DEFAULT)

    result["function_call"] = {
        "name": str(fc["name"]),
        "args": fc.get("args") or {},
    }
    for field in ("mood", "sandy_face", "persona_intensity", "persona_snippet", "confidence"):
        if field in parsed:
            result[field] = parsed[field]
    return result


def _fn_to_intent(name: str) -> str:
    """Map a tool name to an intent string the router/execute nodes expect."""
    _map = {
        "ask_clarification": "clarify.ask",
        "request_confirmation": "pending.confirm",
        "chat_respond": "chat.general",
        "chat_emotional": "chat.emotional_support",
        "pending_confirm": "pending.confirm",
        "pending_reject": "pending.reject",
        "pending_select": "pending.select_option",
        "cost_report": "cost",
        "task_list": "task.list",
    }
    if name in _map:
        return _map[name]
    # task_create → task.create  |  email_read → email.read
    return name.replace("_", ".", 1)


def _fn_to_routing_hint(name: str) -> str:
    if name == "ask_clarification":
        return "clarify"
    if name in {"request_confirmation", "pending_confirm", "pending_reject", "pending_select"}:
        return "pending_confirm"
    return "execute_direct"


def _build_user_prompt(state: SandyState) -> str:
    parts = [f"رسالة المستخدم: {state['message']}"]
    if state.get("conversation_history"):
        last = state["conversation_history"][-2:]
        history_text = "\n".join(
            f"{'المستخدم' if m['role'] == 'user' else 'Sandy'}: {m['content']}"
            for m in last
        )
        parts.append(f"\nآخر رسائل:\n{history_text}")
    if state.get("pending_state"):
        p = state["pending_state"]
        ctx = f"\nيوجد pending نشط: نوعه={p.get('type')} | action={p.get('action')}"
        if p.get("type") == "clarification":
            orig = (p.get("original_message") or "").strip()
            if orig:
                ctx += f"\nالرسالة الأصلية للمستخدم: '{orig}'"
        parts.append(ctx)
    return "\n".join(parts)


def route_with_fc(
    state: SandyState,
    declarations: List[Dict[str, Any]],
    agent_name: str = "specialist",
) -> SandyState:
    """Run FC routing and return state with function_call, intent, template,
    mood, and the rest. declarations is the tool catalog the model sees;
    agent_name feeds the Langfuse trace name and tags.
    """
    fc: Optional[Dict[str, Any]] = None
    intent = "chat.general"
    routing_hint = "execute_direct"
    requires_clarification = False
    clarification_q: Optional[str] = None
    parsed: Dict[str, Any] = {}

    try:
        client = AzureIntentClient()
        user_prompt = _build_user_prompt(state)
        tool_catalog = _build_tool_catalog(declarations)
        system = _FC_SYSTEM_TEMPLATE.format(tool_catalog=tool_catalog)
        try:
            from app.utils.feature_flags import get_active_flags
            ff_tags = get_active_flags()
        except ImportError:
            ff_tags = []
        pipeline = "multi_agent"
        _t_gemini = time.perf_counter()
        raw = client._generate_with_gemini(
            user_prompt,
            response_mime_type="application/json",
            system_instruction=system,
            langfuse_name=f"{agent_name}.intent_routing",
            langfuse_user_id=str(state.get("user_id") or ""),
            langfuse_session_id=str(state.get("chat_id") or ""),
            langfuse_tags=[agent_name, "fc-routing", f"pipeline:{pipeline}"] + ff_tags,
            langfuse_metadata={
                "node": agent_name,
                "pipeline": pipeline,
                "has_pending": bool(state.get("pending_state")),
                "has_image_state": bool(state.get("image_state")),
                "tool_catalog_size": len(declarations),
            },
        )
        logger.info(f"[fc_router] gemini_routing: {(time.perf_counter()-_t_gemini)*1000:.0f}ms")
        parsed = _parse_fc_response(raw)
        fc = parsed["function_call"]
        fn_name = fc["name"]

        intent = _fn_to_intent(fn_name)
        routing_hint = _fn_to_routing_hint(fn_name)
        requires_clarification = fn_name == "ask_clarification"
        clarification_q = fc["args"].get("question") if requires_clarification else None

        logger.debug(f"[fc_router] FC: {fn_name} → intent={intent}")

    except Exception as exc:
        logger.error(f"[fc_router] Azure intent failed: {exc}")
        try:
            from app.integrations.sentry_config import capture_exception
            capture_exception(exc, context={"node": "route_with_fc", "agent": agent_name, "message": state.get("message", "")[:100]})
        except Exception:
            pass

        # Try GPT routing before giving up to the safe default.
        try:
            from app.agent.model_fallback import route_with_gpt
            gpt_intent = route_with_gpt(state.get("message", ""))
            if gpt_intent and "task" in gpt_intent:
                intent = "task.create"
                routing_hint = "execute_direct"
            elif gpt_intent and gpt_intent in ("reminder", "email"):
                intent = f"{gpt_intent}.create"
                routing_hint = "execute_direct"
        except Exception:
            pass

        parsed = dict(_FC_DEFAULT)
        parsed["error"] = f"route_with_fc failed: {exc}"

    template = parsed.get("response_template") or ""
    persona_intensity = parsed.get("persona_intensity") or "standard"
    if not template:
        from app.agent.graph.response_templates import get_response_template
        template = get_response_template(intent, persona_intensity)

    multi_fcs = parsed.get("function_calls")
    if multi_fcs:
        logger.debug(f"[fc_router] multi-FC: {[f['name'] for f in multi_fcs]}")

    # The model returns sandy_face on every reply so the hardware face always reacts.
    _VALID_FACES = {
        "happy", "big_happy", "sad", "angry", "surprised", "curious",
        "thinking", "focused", "sleepy", "bored", "excited", "shy",
        "confused", "love", "proud", "worried", "playful", "silly",
        "grumpy", "hopeful", "grateful", "disappointed", "alert",
        "calm", "idle",
    }
    sandy_face = (parsed.get("sandy_face") or "").strip().lower()
    if sandy_face not in _VALID_FACES:
        # Pick a face from the user's mood instead.
        _USER_MOOD_FALLBACK = {
            "happy": "happy", "sad": "worried", "stressed": "worried",
            "frustrated": "calm", "calm": "calm", "neutral": "idle",
        }
        sandy_face = _USER_MOOD_FALLBACK.get((parsed.get("mood") or "").lower(), "idle")
    try:
        from app.integrations.sandy_device import get_sandy_device_client
        get_sandy_device_client().sync_mood_async(sandy_face)
    except Exception as e:
        logger.debug(f"[fc_router] hardware face sync skipped: {e}")

    return merge_state(state, {
        "intent": intent,
        "function_call": fc,
        "function_calls": multi_fcs or None,
        "confidence": parsed.get("confidence"),
        "complexity": parsed.get("complexity"),
        "mood": parsed.get("mood", "neutral"),
        "urgency": parsed.get("urgency"),
        "requires_clarification": requires_clarification,
        "routing_hint": routing_hint,
        "clarification_question": clarification_q,
        "persona_intensity": persona_intensity,
        "persona_snippet": parsed.get("persona_snippet") or None,
        "response_template": template or None,
        "error": parsed.get("error"),
    })
