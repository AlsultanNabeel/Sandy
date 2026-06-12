# Native Productivity Overhaul — Status

Last updated: 2026-06-12. Working session handoff note — if a fresh session
picks this up, everything below is already committed; continue from "Remaining".

## Done (committed, main repo + frontend repo)

1. **Native stores on Mongo** (Google Calendar/Tasks deleted, Notion deleted):
   tasks_store, reminders_store (RRULE recurrence, snooze buttons in Telegram
   via remsnz:/remdone: callbacks), shopping_store, habits_store (streaks),
   expenses_store, journal_store, reading_store (books + sessions +
   pause/resume + "وين وصلت؟"), focus_store (robot FOCUSED at start,
   celebration at end, end-alert rides reminders pipeline).
2. **Rename sweep**: self_coding → project_builder (package, schema, metrics,
   tests), chroma_memory → semantic_memory, calendar_time_parser → time_parser.
3. **Email**: gmail.py grew list_inbox_emails/get_email_body/mark_read/archive;
   emails_api.py (list/read/archive/to-task/summarize/draft-reply/send-reply);
   email_watch.py + 5-min scheduler job (Gemini classifies, alerts important only).
4. **Studio APIs**: robot status (with why-offline diagnostics: available/
   connected/last_seen_sec) + command, /api/projects (Redis scan), GitHub
   issues (github_api.list_issues), /api/plans, /api/search (unified).
5. **Frontend (separate repo)**: 7 tabs (talk/tasks/reminders/emails/plans/
   projects/robot), unified search box, browser notifications for due
   reminders, emails tab error+retry (never hangs), robot offline diagnostics.
6. **Briefings**: morning (+unread emails +day-plan suggestion), evening 21:30,
   weekly stats Friday 18:00. Scheduler now has 8 jobs.
7. **Tools**: 22 life tools registered in setup.py → available on Telegram,
   web agent, AND voice (shared ToolRegistry).
8. **Tests**: suite green (557 passed). Fixed the deploy blocker: deps.py had
   been deleted but 3 pending handlers import it (`import ... as deps`) —
   restored as re-export hub over the stores. Also fixed a PRE-EXISTING
   test_soul_vault failure (persona_snippet now carries [حالة المستخدم] suffix)
   that was likely blocking CI deploys before this effort.

## Remaining

- ~~6c — "حياتي" tab~~ DONE: life_api.py (15 endpoints, owner real / guest
  demo) + LifeTab.jsx (5 sub-views: shopping/habits/expenses/journal/books)
  + i18n ar/en + 8th top tab. Also fixed a worker-dyno crash: the rename
  sweep had missed cloud/sandy_worker.py (5 self_coding imports).
- **Phase 7 — closing**: docs update (README/Claude.md, English), Heroku env
  vars list for the owner to DELETE (GOOGLE_CALENDAR_ID, TASKS_PROVIDER,
  ARDUINO_CLIENT_ID/SECRET, AZURE_REALTIME_DEPLOYMENT, SANDY_THING_ID,
  NOTION_*), and vars to ADD for web robot control if missing
  (SANDY_MQTT_HOST/PORT/USER/PASS — same HiveMQ creds as the firmware).
  Then "وقت البوش" with test phrases per feature.
- **Deferred by decision**: step-by-step project building with checkpoint
  (backend later; frontend-only for now), MQTT broker decision (tied to room
  scenes), reminder-by-robot-voice (tied to the echo fix).

## Owner workflow rules (critical)

- NEVER push or deploy; only commit. Owner pushes → GitHub Actions → Heroku.
- NEVER run pytest — fix by reading code; owner says «شغّل» when he wants tests.
- Arabic-only narration INCLUDING Bash description fields; always include
  completion percentages; no inline EN/AR mixing.
