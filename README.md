# Sandy

Sandy is an AI companion that lives inside a small robot. She hears your voice,
sees images, remembers your life, reads your mood, keeps your tasks and calendar,
does web research, writes code, and controls her own body (face, head, camera).
She runs on LangGraph with multi-agent function calling over Azure GPT-4o-mini,
and she stays online 24/7 on Heroku.

> Goal number one: a fast voice robot. You talk, Sandy understands, Sandy talks
> back, with almost no delay. Every decision in this project is measured against
> that goal.

---

## Two ways to reach Sandy

Same personality, same backend, same memory. Only the transport changes:

| Way | Status | How it reaches you |
|---|---|---|
| Telegram | Live (and we leave it as is) | Text and voice notes in the Telegram app |
| The robot | In progress | The ESP32-S3 streams audio over WebSocket, Sandy processes it and replies with voice from the device speaker, with almost no delay |

Why two: a Telegram voice note is a file (record, upload, download), so it can
never be real-time no matter how fast the backend is. The robot streams audio
live instead of sending files.

---

## What Sandy can do

| Feature | Details |
|---|---|
| Conversation | Text and voice, changing mood, short-term memory (Redis) and long-term memory (MongoDB) |
| Voice | STT with Azure Speech. TTS with Gemini 3.1 Flash TTS first, then Google or Azure as fallback. Tone follows her mood |
| Tasks | Add, edit, complete, delete, with a confirmation before anything destructive |
| Reminders | Create, edit, delete, linked to tasks and the calendar |
| Google Calendar | Read, add, edit, and resolve appointment conflicts |
| Email (Gmail) | Read the inbox, send, reply |
| Research | News, places, and deep web research with Exa |
| Images | Generate and edit with Azure FLUX (DALL·E / gpt-image as fallback), plus image description with Vision |
| Documents | Read and analyze TXT, PDF, DOCX, CSV, XLSX, JSON |
| Memory | MongoDB plus an encrypted emotional long-term memory (Fernet) |
| GitHub | Read commits, issues, PRs, and files over MCP |
| Hardware | Control the face, head, buzzer, and camera over MQTT |
| Reports | Cost reports for Heroku, Azure, AWS, and Google Cloud |
| Project Builder | Build a whole feature or project in another repo after the owner approves (runs on the worker dyno) |

---

## Web app (React)

A bilingual (Arabic / English) React + Vite site. Same Sandy, same backend, in
the browser. It lives in its own git repo under `frontend/`.

- Pages: Home, Chat (chat, search, and images in one place), Meet Sandy (voice, memory, timeline), Projects, Status, Privacy, Terms.
- Bilingual: an AR/EN toggle flips every string, the page direction (RTL/LTR), and the font (Cairo/Inter). Sandy answers in the active language. The site sends `lang` to `/api/agent` and `/api/analyze-image`, and she replies in that language with the same personality.
- Owner vs guest: the owner logs in for full access (real Google Calendar and Tasks, plus the full pipeline). Guests get a rate-limited Sandy with in-chat owner approval.
- Projects list: the Projects page reads the owner's GitHub repos and shows the ones tagged with the `sandy` topic that have Pages enabled, each as a live `iframe` preview. Projects that Sandy builds (repo plus `sandy` topic plus Pages, README stamped "Created by Sandy") show up here on their own.

---

## Voice pipeline

### Today (over Telegram)
```
voice note → download → Azure Speech STT → run_graph (LLM) → text right away
                                                           → TTS in the background: Gemini → Google → Azure → voice note
```
- `SANDY_VOICE_ENABLED=0` turns voice off (text only).
- The text reaches you right away. The audio follows on a separate thread, so the text is never held back.

### The road to low latency
1. Quick wins, keeping Telegram: cache the Gemini client and add timeouts, drop the extra summarization LLM call, fold routing from two LLM calls into one, stream STT instead of going through ffmpeg.
2. Real-time, the robot: ESP32-S3 to WebSocket (WSS) streaming plus a streaming speech-to-speech engine.
   - Recommended engine: Google Gemini Live API. Best Arabic and Levantine support, least friction (the project already uses `google-genai` and has a `GEMINI_API_KEY`), with built-in barge-in and tool calling.
   - OpenAI Realtime API is the documented fallback. Amazon Nova Sonic is not recommended (weak Arabic).

> Note: the old Nova attempt (`aws_nova_realtime.py`) was a broken placeholder, so it was deleted.

---

## Tech stack

```
Python 3.11              the backend
Azure GPT-4o-mini        core brain: routing, intent, chat, Vision
OpenAI (direct)          backup brain (fallback)
Anthropic Claude Sonnet  self-coding only (via AWS Bedrock / Google Vertex)
Azure FLUX + DALL·E      image generation and editing
Gemini 3.1 Flash TTS     primary voice (plus Google/Azure fallback)
Azure Speech             STT (hearing)
LangGraph pipeline       RouterAgent → Specialist(FC) → soul → router → execute → response
ToolRegistry/Dispatcher  FC system: 50+ tools (Python or MCP)
MCPHub (Node.js)         GitHub MCP over JSON-RPC stdio
Redis                    short-term memory (30-min TTL) plus the worker queue
MongoDB                  persistent memory plus emotional memory
Fernet (cryptography)    encrypts the emotional memory
Telegram Bot API         current user interface
Exa                      web search
Sentry / Langfuse        error monitoring plus LLM tracing
Prometheus / Grafana     metrics plus dashboards
ESP32 + MQTT             the physical hardware
Heroku                   cloud deploy (web and worker dynos)
```

---

## Architecture

```
User message (Telegram: text or voice)
         ↓
api/webhook.py (Flask) or polling: dedup, instant 200 OK, hand off to a thread
         ↓
api/telegram_handlers.py: voice? → download → STT (Azure)
         ↓ text
┌──────────────────────────────────────────────────────┐
│                  agent/graph: run_graph()              │
│                                                        │
│  RouterAgent    classify category (chat/core/...)      │  (LLM call #1)
│  Specialist+FC  fc_router → function_call + mood       │  (LLM call #2)
│  soul_node      persona snippet + prefetch             │
│  router_node    pending? clarify? execute?             │
│      → execute / pending / clarify                     │  ToolDispatcher → Python or MCP
│  response_node  template + persona → final reply       │
└──────────────────────────────────────────────────────┘
         ↓ text
features/voice.py: text now, then TTS in the background (Gemini → Google → Azure)
         ↓
reply to user (text + voice)

  Web dyno ──(Redis queue)── Worker dyno (Project Builder / self-coding)
```

FC mode: every action is a tool registered in `ToolRegistry` (through
`setup.register_all_tools`) and run by `ToolDispatcher`. Adding a capability
means adding a tool, not editing the router.

---

## Humanization layer

| System | Role |
|---|---|
| Emotional LTM | Encrypted (Fernet) emotional memory. Remembers feelings and situations |
| Soul Vault | Persona snippets that change with mood (standard, empathetic, playful, formal) |
| Dreams Engine | Thoughts and notes that build up in the background |
| Interests / Style / Lessons / Relationships memory | Tracks interests, learns writing style, saves lessons and relationships |
| Health Monitor + Anomaly Detector | Tracks sleep and activity patterns and notices odd changes |

### Proactive features
- Proactive Comfort: reassurance on its own when a hard mood shows up.
- Proactive Context: daily notes plus accumulated context.
- Smart Silence: checks the calendar and `SANDY_QUIET_HOURS` (default 23-7) before any proactive message.
- Multi-Tool Chain: "add a meeting tomorrow at 10 and remind me half an hour before" runs `calendar_add` then `reminder_create` from one message.

---

## Project Builder (runs on the worker)

The sanctioned way to build a whole feature or project in another repo after the
owner approves:

```
Owner request → PLAN (Sonnet) → owner approval → build groups → PR
```

- 3 phases: PLAN (Sonnet writes structured JSON), Build (per-file content plus a strict validator), Finalize (wait_for_ci, then PR).
- Safety limits: 3 attempts per task, up to 200 KB per file, up to 30 files, up to 8 groups, an isolated branch.
- Graceful shutdown: if a Heroku redeploy lands mid-task, it checkpoints in Redis and the new worker picks up from the same point.

---

## Run locally

```bash
git clone <repo> && cd Sandy
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# create .env at the repo root. Minimum to boot:
#   TELEGRAM_BOT_TOKEN · OWNER_CHAT_ID · AZURE_OPENAI_ENDPOINT · AZURE_OPENAI_API_KEY · AZURE_OPENAI_CHAT_DEPLOYMENT
#   (everything else is optional. A missing key only disables its own feature.)

# the web process (the assistant you chat with). Use polling locally:
RUN_MODE=polling python cloud/sandy_agent.py

# the worker (optional, needs REDIS_URL + GITHUB_TOKEN + GITHUB_DEFAULT_REPO):
python cloud/sandy_worker.py
```
- The GitHub MCP server runs on Node 20.x (`@modelcontextprotocol/server-github`).

---

## Deploy to Heroku

```bash
heroku git:remote -a sandy-robot
heroku ps:scale web=1 worker=1 -a sandy-robot      # the worker needs Basic+ (Eco dynos sleep)
heroku config:set TELEGRAM_BOT_TOKEN=... OWNER_CHAT_ID=... AZURE_OPENAI_ENDPOINT=... -a sandy-robot
heroku config:set GOOGLE_CREDENTIALS_JSON="$(cat sandy-gcloud-key.json)" -a sandy-robot   # written to a file at boot
git push heroku main
```
- `Procfile`: `web: python cloud/sandy_agent.py` · `worker: python cloud/sandy_worker.py`
- Health: `GET /health`. Metrics: `GET /metrics` (Prometheus).
- In webhook mode: set `RUN_MODE=webhook` plus `APP_URL` / `HEROKU_APP_DEFAULT_DOMAIN_NAME`.

---

## Environment variables (the essentials)

### Required
```env
TELEGRAM_BOT_TOKEN=          # bot token
OWNER_CHAT_ID=              # owner chat (privileged)
AZURE_OPENAI_ENDPOINT=      # core brain
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_CHAT_DEPLOYMENT=   # gpt-4o-mini
MONGODB_URI=                # persistent memory
REDIS_URL=                  # STM + worker queue (required for the worker)
```

### Voice
```env
SANDY_VOICE_ENABLED=1
GEMINI_API_KEY=                 # Gemini TTS (primary)
GEMINI_TTS_MODEL=gemini-3.1-flash-tts
GEMINI_TTS_VOICE=Aoede
GOOGLE_TTS_VOICE=ar-XA-Chirp3-HD-Sulafat     # fallback
AZURE_SPEECH_KEY=  AZURE_SPEECH_REGION=  AZURE_SPEECH_VOICE=ar-LB-LaylaNeural   # STT + fallback
# per-mood tone: SANDY_TTS_STYLE_BASE / _HAPPY / _SAD / _PLAYFUL / _EMPATHETIC ...
```

### Images and research
```env
AZURE_FLUX_ENDPOINT=  AZURE_FLUX_DEPLOYMENT=sandy-flux        # image generation
AZURE_OPENAI_IMAGE_DEPLOYMENT=  AZURE_OPENAI_IMAGE_EDIT_DEPLOYMENT=   # DALL·E/gpt-image fallback
EXA_API_KEY=  WEB_RESEARCH_PROVIDER=exa  GOOGLE_PLACES_API_KEY=
```

### Google / GitHub / Claude (optional)
```env
GOOGLE_CALENDAR_ID=  GOOGLE_CREDENTIALS_JSON=  TASKS_PROVIDER=google
GITHUB_TOKEN=  GITHUB_DEFAULT_REPO=owner/repo  GITHUB_WEBHOOK_SECRET=
AWS_ACCESS_KEY_ID=  AWS_SECRET_ACCESS_KEY=  AWS_REGION=us-east-1     # Claude/self-coding
CLAUDE_VERTEX_MODEL=claude-sonnet-4-5@20250929  VERTEX_REGION=us-east5
```

### Persona / hardware / monitoring (optional)
```env
SANDY_PERSONALITY=  SYSTEM_PROMPT_ADDITION=  SANDY_QUIET_HOURS=23-7  SANDY_LTM_KEY=
SANDY_IP=192.168.1.100  CAM_IP=192.168.1.150  SANDY_MQTT_HOST=  SANDY_MQTT_USER=  SANDY_MQTT_PASS=
SENTRY_DSN=  LANGFUSE_PUBLIC_KEY=  LANGFUSE_SECRET_KEY=  GRAFANA_CLOUD_REMOTE_WRITE_URL=
```

> Sandy runs without any optional service. A missing one only disables its own feature.

---

## Tests

```bash
python -m pytest tests/ -q                                 # all tests (~40 files, ~560 tests)
python -m pytest tests/ --cov=cloud/app --cov-report=term-missing
ruff check <changed files> && python -m py_compile <changed files>
```
> Note: the voice pipeline (voice, STT, TTS) has no automated tests yet. Contributions welcome.

---

## Hardware

| Device | IP | Description |
|---|---|---|
| Main ESP32 | 192.168.1.100 | current controller |
| ESP32-CAM | 192.168.1.150 | camera |

Components: TFT ST7789 240×240 display (face), SG90 servo (head), HC-SR04 distance
sensor, buzzer, 18650 (2S) batteries plus Mini360.
On the way (ordered): ESP32-S3 N16R8 (new brain, wake word), INMP441 microphone,
MAX98357 amplifier plus speaker.
Firmware lives in `sandy/` and `esp32cam_Camera/` (Arduino C++).

---

## Project structure

```
Sandy/
├── cloud/                      # the backend (Python)
│   ├── sandy_agent.py          # web dyno entrypoint
│   ├── sandy_worker.py         # worker dyno entrypoint (self-coding queue)
│   └── app/
│       ├── config.py  bootstrap.py
│       ├── agent/              # graph/ · nodes/ · agents/ · tools/ · executor/ · facade/ · self_coding/ + humanization
│       ├── api/                # webhook.py · telegram_handlers.py · telegram_runtime.py
│       ├── features/           # voice · vision · images · research · google_* · weather
│       ├── integrations/       # azure · gemini_tts · google_tts · azure_speech · mongodb · mcp · exa · github · sandy_device(MQTT)
│       ├── tools/              # heroku_tool · cost_tool
│       └── utils/              # redis_stm · rate_limiter · circuit_breaker · metrics · text · time ...
├── sandy/  esp32cam_Camera/    # ESP32 firmware (Arduino C++)
├── tests/                      # backend tests
├── monitoring/                 # Prometheus + Grafana
├── Procfile  requirements.txt  package.json
```

---

## The vision

Sandy is a small Jarvis. She understands you, remembers you, hears you, answers
in her own voice, sees images, does research, keeps your life in order, and moves
in the real world. Fast enough to feel like a conversation, not a command line.
