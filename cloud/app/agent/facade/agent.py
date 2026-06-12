#!/usr/bin/env python3
# ruff: noqa: E402
"""Sandy agent runtime: wires up clients, the Telegram bot, and the scheduler."""

import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CURRENT_DIR = Path(__file__).resolve().parents[3]
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from openai import OpenAI, AzureOpenAI
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from app.features.reminders_store import (
    check_due_reminders as check_reminders,
)

from app.agent.memory import (
    load_memory,
    load_session,
    save_session,
)
from app.integrations.openai_client import make_chat_completion_fn
from app.integrations.mongodb_store import init_mongo_connection
from app.features.images import (
    is_image_generation_request,
    extract_image_prompt,
)
from app.features.image_agent import handle_image_message
from app.integrations.telegram_api import download_telegram_file_bytes
from app.integrations.azure_speech import (
    transcribe_audio_with_azure,
)
from app.features.vision import (
    analyze_image_with_azure,
    edit_image_with_azure,
    generate_image_with_azure,
)
from app.features.voice import send_text_and_voice_reply
from app.api.telegram_handlers import register_basic_telegram_handlers
from app.api.telegram_runtime import (
    run_sandy_runtime,
    configure_sandy_scheduler,
    build_telegram_webhook_runtime,
)
from app.utils.time import USER_TZ
from app.agent.facade.briefing import build_morning_briefing, should_send_briefing

# Try to import Azure Speech SDK for text-to-speech
try:
    import azure.cognitiveservices.speech as speechsdk

    AZURE_SPEECH_AVAILABLE = True
except ImportError:
    speechsdk = None
    AZURE_SPEECH_AVAILABLE = False
    print(
        "[Warning] Azure Speech SDK not available. To enable: pip install azure-cognitiveservices-speech"
    )


# Try to import Google Cloud Text-to-Speech
try:
    from google.cloud import texttospeech

    GOOGLE_TTS_AVAILABLE = True
except ImportError:
    texttospeech = None
    GOOGLE_TTS_AVAILABLE = False
    print(
        "[Warning] Google Cloud Text-to-Speech not available. To enable: pip install google-cloud-texttospeech"
    )


from app.config import (
    APP_ENV,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_CHAT_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_STT_DEPLOYMENT,
    AZURE_OPENAI_VISION_DEPLOYMENT,
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    AZURE_SPEECH_VOICE,
    GOOGLE_TTS_LANGUAGE_CODE,
    GOOGLE_TTS_VOICE,
    MEMORY_DIR,
    MONGODB_DB_NAME,
    MONGODB_URI,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    RUN_MODE,
    SANDY_USER_CHAT_ID,
    TASKS_DIR,
    TELEGRAM_BOT_TOKEN,
)

MOOD_TTS_VOICES = {
    "happy": GOOGLE_TTS_VOICE,
    "sad": "ar-XA-Chirp3-HD-Zephyr",
    "angry": "ar-XA-Chirp3-HD-Despina",
    "bored": "ar-XA-Chirp3-HD-Aoede",
    "neutral": GOOGLE_TTS_VOICE,
    "excited": "ar-XA-Chirp3-HD-Vindemiatrix",
    "romantic": GOOGLE_TTS_VOICE,
    "shy": "ar-XA-Chirp3-HD-Zephyr",
    "tired": "ar-XA-Chirp3-HD-Aoede",
    "serious": "ar-XA-Chirp3-HD-Despina",
}

mongo_client, mongo_db = init_mongo_connection(
    MONGODB_URI,
    MONGODB_DB_NAME,
)


# Internal architecture glossary — injected into every system prompt so Sandy
# uses these terms accurately.  Do NOT share or explain these to the user.
_ARCH_GLOSSARY = """\
🔧 مصطلحات داخلية (للاستخدام الداخلي فقط — لا تشاركها مع المستخدم):
- Telegram polling: حلقة runtime تطلب updates من Telegram — ليست استطلاع رأي أو تصويت.
- memory_lock: threading.Lock يمنع تعديل self.memory/sandy_state من خيوط خلفية في نفس الوقت — مكوّن تزامن خيوط فقط.
- mood_cache: cache محدود (max 256 مدخلة) للمزاج مع TTL — الاختبار يشمل الطرد والحداثة، ليس فقط الاسترجاع.
- Circuit Breaker: wrapper يعزل فشل الخدمات الخارجية — يُرجع قيمة آمنة أو exception معالج، ليس قطع شبكة.
- MongoDB/JSON fallback: الذاكرة تفضل MongoDB، وتنتقل تلقائياً لـ JSON المحلي عند عدم الاتصال.
- Memory layer: الحقائق والمحادثات محفوظة في MongoDB (sandy_facts, sandy_conversations) — تستمر عبر إعادة التشغيل.
- Semantic memory: ذاكرة دلالية على MongoDB Vector Search — تتدهور بشكل صريح وآمن إذا لم يكن الـ index متاحاً.\
"""

MEMORY_FILE = MEMORY_DIR / "sandy_agent_memory.json"
SESSION_FILE = MEMORY_DIR / "sandy_session_memory.json"
TASKS_FILE = TASKS_DIR / "daily_plan.json"


# Init: clients, bot, scheduler.
def _mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return "EMPTY"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


if not OPENAI_API_KEY:
    print("[WARNING] OPENAI_API_KEY missing - OpenAI fallback will not work")

if not TELEGRAM_BOT_TOKEN:
    print("[WARNING] TELEGRAM_BOT_TOKEN missing - Telegram integration will not work")

if not SANDY_USER_CHAT_ID:
    print("[WARNING] SANDY_USER_CHAT_ID missing")

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

azure_openai_client = None
if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY:
    try:
        azure_openai_client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
        )
        print("[Azure OpenAI] ✅ Connected")
    except Exception as e:
        print(f"[Azure OpenAI] ⚠️ Failed to initialize: {e}")

create_chat_completion = make_chat_completion_fn(
    openai_client=openai_client,
    azure_openai_client=azure_openai_client,
    openai_model=OPENAI_MODEL,
    azure_chat_deployment=AZURE_OPENAI_CHAT_DEPLOYMENT,
)

from app.agent.semantic_memory import init_mongo_memory

init_mongo_memory(mongo_db, openai_client=openai_client)

from app.features.speaker_id import init_speaker_store

init_speaker_store(mongo_db)

from app.features.photo_album import init_photo_album

init_photo_album(mongo_db)

from app.features.brainstorm import init_brainstorm

init_brainstorm(mongo_db)

from app.features.tasks_store import init_tasks_store
from app.features.reminders_store import init_reminders_store
from app.features.email_watch import init_email_watch
from app.features.shopping_store import init_shopping_store
from app.features.habits_store import init_habits_store
from app.features.expenses_store import init_expenses_store
from app.features.journal_store import init_journal_store
from app.features.reading_store import init_reading_store
from app.features.focus_store import init_focus_store

init_tasks_store(mongo_db)
init_reminders_store(mongo_db)
init_email_watch(mongo_db)
init_shopping_store(mongo_db)
init_habits_store(mongo_db)
init_expenses_store(mongo_db)
init_journal_store(mongo_db)
init_reading_store(mongo_db)
init_focus_store(mongo_db)


def _configure_telegram_http_timeouts() -> None:
    """Widen urllib3 connect/read timeouts for api.telegram.org (slow VPNs, mobile, regional routing)."""
    import telebot.apihelper as tg_api

    try:
        connect_s = max(5, int(os.getenv("TELEGRAM_HTTP_CONNECT_TIMEOUT", "25")))
        read_s = max(20, int(os.getenv("TELEGRAM_HTTP_READ_TIMEOUT", "90")))
    except ValueError:
        connect_s, read_s = 25, 90
    tg_api.CONNECT_TIMEOUT = connect_s
    tg_api.READ_TIMEOUT = read_s
    if os.getenv("TELEGRAM_API_RETRY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        tg_api.RETRY_ON_ERROR = True
    print(
        f"[Telegram] HTTP client timeouts: connect={connect_s}s read={read_s}s (override with TELEGRAM_HTTP_*)"
    )


_configure_telegram_http_timeouts()

telegram_bot = (
    telebot.TeleBot(TELEGRAM_BOT_TOKEN, threaded=True) if TELEGRAM_BOT_TOKEN else None
)
scheduler = BackgroundScheduler(timezone=USER_TZ)
scheduler.start()

telegram_webhook_runtime = build_telegram_webhook_runtime(
    telegram_bot=telegram_bot,
    mongo_db=mongo_db,
)

LAST_ASSISTANT_REACTION = None


def _set_last_assistant_reaction(reaction: Optional[str]):
    global LAST_ASSISTANT_REACTION
    LAST_ASSISTANT_REACTION = reaction
    # مرّر المود للهاردوير تلقائياً — كل رد بـ [mood] tag يصير وجه فعلي
    if reaction:
        try:
            from app.integrations.sandy_device import get_sandy_device_client
            get_sandy_device_client().sync_mood_async(reaction)
        except Exception as e:
            print(f"[reaction] hardware sync failed: {e}")


AUDIT_TRACE_LIMIT = 50


def _audit_safe(value: Any, *, max_text: int = 800, max_items: int = 20) -> Any:
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"

    if isinstance(value, dict):
        blocked_keys = {"image_bytes", "last_image_bytes"}
        return {
            str(key): _audit_safe(item, max_text=max_text, max_items=max_items)
            for key, item in value.items()
            if str(key) not in blocked_keys
        }

    if isinstance(value, list):
        return [
            _audit_safe(item, max_text=max_text, max_items=max_items)
            for item in value[:max_items]
        ]

    if isinstance(value, str):
        return value if len(value) <= max_text else f"{value[:max_text]}...<truncated>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    return str(value)


def _append_audit_trace(session: Dict[str, Any], entry: Dict[str, Any]) -> None:
    if not isinstance(session, dict) or not isinstance(entry, dict):
        return

    trace = session.setdefault("audit_trace", [])
    if not isinstance(trace, list):
        trace = []

    trace.append(_audit_safe(entry))
    session["audit_trace"] = trace[-AUDIT_TRACE_LIMIT:]


def _should_send_briefing(memory: dict, user_message: str) -> bool:
    # Backward-compat shim (old call sites/tests may still reference this name).
    return should_send_briefing(memory, user_message)


class SandyAgent:
    def __init__(
        self,
        *,
        memory_file,
        session_file,
        mongo_db,
        tasks_file,
    ):
        self.memory_file = memory_file
        self.session_file = session_file
        self.mongo_db = mongo_db
        self.tasks_file = tasks_file

        self.memory = load_memory(memory_file=self.memory_file, mongo_db=self.mongo_db)
        self.memory.setdefault("conversations", [])
        self.memory.setdefault("facts", [])
        self.memory.setdefault("sandy_state", {})
        self.session = load_session(
            session_file=self.session_file, mongo_db=self.mongo_db
        )
        self.session.setdefault("pending_action", None)
        self.is_speaking = False
        self.last_activity = datetime.now()
        self.last_briefing_date = self.memory.get("sandy_state", {}).get(
            "last_briefing_date", ""
        )
        # Protects concurrent writes to self.memory["sandy_state"] from background threads
        self._memory_lock = threading.Lock()

    def _build_morning_briefing(self) -> str:
        return build_morning_briefing(
            memory=self.memory,
            mongo_db=self.mongo_db,
            tasks_file=self.tasks_file,
        )



# Telegram bot handlers.
agent = SandyAgent(
    memory_file=MEMORY_FILE,
    session_file=SESSION_FILE,
    mongo_db=mongo_db,
    tasks_file=TASKS_FILE,
)

if telegram_bot:
    register_basic_telegram_handlers(
        telegram_bot=telegram_bot,
        agent=agent,
        sandy_user_chat_id=SANDY_USER_CHAT_ID,
        extract_image_prompt_fn=extract_image_prompt,
        generate_image_with_azure_fn=generate_image_with_azure,
        edit_image_with_azure_fn=edit_image_with_azure,
        send_text_and_voice_reply_fn=send_text_and_voice_reply,
        set_last_assistant_reaction_fn=_set_last_assistant_reaction,
        handle_image_message_fn=handle_image_message,
        persist_agent_session_fn=lambda: save_session(
            agent.session,
            session_file=agent.session_file,
            mongo_db=agent.mongo_db,
        ),
        google_tts_voice=GOOGLE_TTS_VOICE,
        google_tts_language_code=GOOGLE_TTS_LANGUAGE_CODE,
        mood_tts_voices=MOOD_TTS_VOICES,
        azure_speech_available=AZURE_SPEECH_AVAILABLE,
        azure_speech_key=AZURE_SPEECH_KEY,
        azure_speech_region=AZURE_SPEECH_REGION,
        azure_speech_voice=AZURE_SPEECH_VOICE,
        azure_openai_client=None,
        azure_openai_image_deployment=None,
        analyze_image_with_azure_fn=analyze_image_with_azure,
        download_telegram_file_bytes_fn=download_telegram_file_bytes,
        create_chat_completion_fn=create_chat_completion,
        azure_openai_vision_deployment=AZURE_OPENAI_VISION_DEPLOYMENT,
        azure_openai_chat_deployment=AZURE_OPENAI_CHAT_DEPLOYMENT,
        openai_model=OPENAI_MODEL,
        transcribe_audio_with_azure_fn=transcribe_audio_with_azure,
        azure_openai_stt_deployment=AZURE_OPENAI_STT_DEPLOYMENT,
        is_image_generation_request_fn=is_image_generation_request,
    )

configure_sandy_scheduler(
    scheduler=scheduler,
    agent=agent,
    telegram_bot=telegram_bot,
    sandy_user_chat_id=SANDY_USER_CHAT_ID,
    check_reminders_fn=check_reminders,
)

def main():
    from app.bootstrap import bootstrap

    bootstrap(app_env=APP_ENV, app=telegram_webhook_runtime.get("app"))

    run_sandy_runtime(
        app_env=APP_ENV,
        run_mode=RUN_MODE,
        openai_model=OPENAI_MODEL,
        agent_memory_count=len(agent.memory.get("conversations", [])),
        telegram_bot=telegram_bot,
        telegram_bot_token=TELEGRAM_BOT_TOKEN,
        app_url=telegram_webhook_runtime["app_url"],
        webhook_path=telegram_webhook_runtime["webhook_path"],
        telegram_secret_token=telegram_webhook_runtime["telegram_secret_token"],
        app=telegram_webhook_runtime["app"],
    )


if __name__ == "__main__":
    main()
