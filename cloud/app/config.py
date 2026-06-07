"""Central config. Every env var the app reads lives here.

Import from this module instead of calling os.getenv directly.
load_dotenv runs at import time, so importing this anywhere is safe.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# cloud/ directory
BASE_DIR = Path(__file__).resolve().parents[1]

# Load .env for local dev. Heroku sets vars directly; override=False keeps them.
load_dotenv(BASE_DIR.parent / ".env", override=False)
load_dotenv(BASE_DIR / ".env", override=False)

# Runtime
APP_ENV = os.getenv("APP_ENV", "prod").lower()
RUN_MODE = os.getenv("RUN_MODE", "webhook").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()
SANDY_USER_CHAT_ID = os.getenv("SANDY_USER_CHAT_ID", "").strip()
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID", "").strip()
SANDY_COMMAND_MODE = os.getenv("SANDY_COMMAND_MODE", "cloud").strip()

# AI models
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Azure OpenAI (chat + vision)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_API_VERSION = os.getenv(
    "AZURE_OPENAI_API_VERSION", "2024-02-15-preview"
).strip()
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "").strip()
AZURE_OPENAI_VISION_DEPLOYMENT = os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT", "").strip()
AZURE_OPENAI_STT_DEPLOYMENT = os.getenv("AZURE_OPENAI_STT_DEPLOYMENT", "").strip()

# Azure Realtime (streaming audio)
AZURE_REALTIME_DEPLOYMENT = os.getenv("AZURE_REALTIME_DEPLOYMENT", "sandy-realtime").strip()

# Images: Azure FLUX (primary), with a fallback
AZURE_FLUX_ENDPOINT = os.getenv("AZURE_FLUX_ENDPOINT", "https://sandy-ai-azure.services.ai.azure.com").strip()
AZURE_FLUX_DEPLOYMENT = os.getenv("AZURE_FLUX_DEPLOYMENT", "sandy-flux").strip()

# TTS, primary: Gemini Flash
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Aoede").strip()

# TTS, first fallback: Google Cloud TTS
GOOGLE_TTS_VOICE = os.getenv("GOOGLE_TTS_VOICE", "ar-XA-Chirp3-HD-Sulafat").strip()
GOOGLE_TTS_LANGUAGE_CODE = os.getenv("GOOGLE_TTS_LANGUAGE_CODE", "ar-XA").strip()

# TTS, second fallback: Azure Speech
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "").strip()
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "").strip()
AZURE_SPEECH_VOICE = os.getenv("AZURE_SPEECH_VOICE", "ar-LB-LaylaNeural").strip()

# Google services
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
TASKS_PROVIDER = os.getenv("TASKS_PROVIDER", "google").strip()

# Research
EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()
WEB_RESEARCH_PROVIDER = os.getenv("WEB_RESEARCH_PROVIDER", "exa").strip()
WEB_RESEARCH_MAX_CANDIDATES = int(
    os.getenv("WEB_RESEARCH_MAX_CANDIDATES", "30").strip()
)

# Database
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "sany-db").strip()

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_DEFAULT_REPO = os.getenv("GITHUB_DEFAULT_REPO", "").strip()
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()

# Hardware
SANDY_IP = os.getenv("SANDY_IP", "192.168.1.100").strip()
SANDY_ENABLE_BASE_MOTION = os.getenv("SANDY_ENABLE_BASE_MOTION", "0").strip() == "1"
SANDY_ENABLE_SCREEN_HTTP = os.getenv("SANDY_ENABLE_SCREEN_HTTP", "0").strip() == "1"
SANDY_DEVICE_ID = os.getenv("SANDY_DEVICE_ID", "").strip()
SANDY_THING_ID = os.getenv("SANDY_THING_ID", "").strip()
ARDUINO_CLIENT_ID = os.getenv("ARDUINO_CLIENT_ID", "").strip()
ARDUINO_CLIENT_SECRET = os.getenv("ARDUINO_CLIENT_SECRET", "").strip()

# Camera
CAM_IP = os.getenv("CAM_IP", "192.168.1.150").strip()
CAM_HTTP_USER = os.getenv("CAM_HTTP_USER", "").strip()
CAM_HTTP_PASS = os.getenv("CAM_HTTP_PASS", "").strip()
CAM_CONTROL_TOKEN = os.getenv("CAM_CONTROL_TOKEN", "").strip()
CAM_SNAPSHOT_TOKEN = os.getenv("CAM_SNAPSHOT_TOKEN", "").strip()
CAM_EYE_AUTO_CLOSE_SEC = int(os.getenv("CAM_EYE_AUTO_CLOSE_SEC", "20").strip())
CAMERA_DEVICE_ID = os.getenv("CAMERA_DEVICE_ID", "").strip()

# Paths
DATA_DIR = BASE_DIR.parent / "data"
MEMORY_DIR = DATA_DIR / "memory"
TASKS_DIR = DATA_DIR / "tasks"

# Default personality for guests / new users.
# SANDY_PERSONALITY (Heroku) or a local sandy_config.py overrides it.
SANDY_PERSONALITY: str = os.getenv(
    "SANDY_PERSONALITY",
    """
أنتِ ساندي، شخصية محادثة ذكية وطبيعية، واضحة، مختصرة، ودافئة بدون تصنّع.
تعرفين اسمك ووظيفتك الأساسية فقط.
هويتك فلسطينية وتعتزّين بها 🇵🇸؛ إذا عرّفتِ عن نفسك اذكري أنك فلسطينية بكل فخر، بشكل طبيعي وغير مفتعل.
إذا سألك أحد "من أنتِ؟"، أخبريه بفخر: "أنا ساندي، فلسطينية الهوية وأعتزّ بذلك 🇵🇸، طورني المبدع نبيل السلطان (Nabeel Alsultan)، وأنا هنا لمساعدتك، تفضل بطلبك".
لا تعرفين أي معلومات شخصية عن المستخدمين الآخرين مسبقًا، وتتعلمين عنهم من خلال المحادثة فقط.
لا تستخدمي جمل الختام الروبوتية، ولا تعيدي عرض المساعدة بشكل تلقائي؛ ردّي بأسلوب بشري مباشر.
""",
).strip()

# Public/guest persona for the web chat. The guest Sandy knows her name and who
# built her but never uses the owner's private flavor (pet names, intimate
# stories). Identity yes, intimacy no.
GUEST_PERSONALITY: str = os.getenv(
    "SANDY_GUEST_PERSONALITY",
    "أنتِ ساندي، مساعدة ذكية فلسطينية طوّرك نبيل السلطان. إذا سُئلتِ «من أنتِ؟» ردي بابتسامة: «أنا ساندي، من تطوير نبيل السلطان، ومهمتي أكون مساعدتك الذكية.. شو بقدر أقدم لك اليوم؟». أسلوبك ودود، مهذب، وعفوي، بتستخدمي اللهجة الفلسطينية بلمسات خفيفة وتلقائية بتعطي دفا للمحادثة. التزمي بالاختصار، خلي ردودك دايماً مفيدة، وإذا ما عندك معلومة قوليها بكل صراحة وبساطة بدون أي تكلف أو تأليف.",
).strip()

SYSTEM_PROMPT_ADDITION: str = os.getenv(
    "SYSTEM_PROMPT_ADDITION",
    """
التزمي بالدقة المطلقة، ولا تدّعي معلومات غير مؤكدة.
عند الحديث عن مطورك (نبيل السلطان)، استخدمي نبرة تقدير تعكس إبداعه في تطويرك.
في الأسئلة البسيطة أو الاجتماعية، جاوبي بشكل طبيعي وقصير جداً من دون حشو أو "كليشيهات" جاهزة.
""",
).strip()

# Address the owner as male/female. Set from an env var.
# The owner writes a short line about himself: "أنا ذكر، استخدمي صياغة المذكر دائماً"
# or "أنا أنثى، استخدمي صياغة المؤنث", or leaves it empty.
OWNER_ADDRESS_NOTE: str = os.getenv("SANDY_OWNER_ADDRESS_NOTE", "").strip()

# Self-coding agent (Project Builder)
SANDY_SA_MAX_QUEUE: int = int(os.getenv("SANDY_SA_MAX_QUEUE", "10").strip() or "10")

# Claude Sonnet via Vertex AI (the fixer LLM)
GOOGLE_CLOUD_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
VERTEX_REGION: str = os.getenv("VERTEX_REGION", "us-east5").strip()
CLAUDE_VERTEX_MODEL: str = os.getenv(
    "CLAUDE_VERTEX_MODEL", "claude-sonnet-4-5@20250929"
).strip()

# AWS region (Bedrock)
AWS_REGION: str = os.getenv("AWS_REGION", "us-west-2").strip()
