from pathlib import Path

import pytest

from app.agent.semantic_memory import load_conversations_to_chroma, load_facts_to_chroma, search_relevant_conversations, search_relevant_facts
from app.agent.memory import load_memory, load_session, save_memory, save_session
from app.api.telegram_runtime import configure_sandy_scheduler
from app.features.gmail import get_gmail_service
from app.features.reminders_store import add_reminder
from app.features.tasks_store import load_tasks
from app.utils.user_profiles import active_user_profile_context


GUEST_PROFILE = {
    "chat_id": "222",
    "name": "ضيف",
    "relation": "guest",
    "tone": "formal",
    "permissions": "chat-only",
}


def test_guest_cannot_read_or_write_memory(tmp_path: Path):
    memory_file = tmp_path / "memory.json"
    session_file = tmp_path / "session.json"
    memory_file.write_text('{"facts": [{"text": "secret"}], "conversations": [{"user": "a"}]}', encoding="utf-8")
    session_file.write_text('{"messages": [{"role": "user", "content": "hi"}]}', encoding="utf-8")

    with active_user_profile_context(GUEST_PROFILE):
        memory = load_memory(memory_file=memory_file)
        session = load_session(session_file=session_file)

        save_memory({"facts": [{"text": "x"}], "conversations": [{"user": "y"}]}, memory_file=memory_file)
        save_session({"messages": [{"role": "user", "content": "x"}]}, session_file=session_file)
        load_facts_to_chroma([{"text": "secret"}])
        load_conversations_to_chroma([{"user": "hello", "sandy": "hi"}])

        assert search_relevant_facts("secret") == []
        assert search_relevant_conversations("secret") == []

    assert memory["facts"] == []
    assert session["messages"] == []
    assert memory_file.read_text(encoding="utf-8").find("secret") != -1
    assert session_file.read_text(encoding="utf-8").find("hi") != -1


def test_guest_cannot_use_private_services():
    with active_user_profile_context(GUEST_PROFILE):
        with pytest.raises(PermissionError, match="هذا خاص بنبيل"):
            load_tasks()
        with pytest.raises(PermissionError, match="هذا خاص بنبيل"):
            add_reminder("test", "2027-01-01T10:00:00+00:00")
        with pytest.raises(PermissionError, match="هذا خاص بنبيل"):
            get_gmail_service()


def test_scheduler_targets_owner_only(monkeypatch):
    import app.api.telegram_runtime as telegram_runtime
    import app.utils.user_profiles as user_profiles

    # OWNER_CHAT_ID is now read from one canonical place at call time.
    monkeypatch.setattr(user_profiles, "OWNER_CHAT_ID", "999")

    sent = []

    class FakeBot:
        def send_message(self, chat_id, text, parse_mode=None):
            sent.append((chat_id, text, parse_mode))

    class FakeAgent:
        def __init__(self):
            self.memory = {"sandy_state": {}}
            self.memory_file = None
            self.mongo_db = None

        def _build_morning_briefing(self):
            return "brief"

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append((func, trigger, kwargs))

    scheduler = FakeScheduler()
    agent = FakeAgent()
    monkeypatch.setattr(telegram_runtime, "save_memory", lambda *args, **kwargs: None)

    configure_sandy_scheduler(
        scheduler=scheduler,
        agent=agent,
        telegram_bot=FakeBot(),
        sandy_user_chat_id="111",
        check_reminders_fn=lambda **kwargs: sent.append((kwargs["user_chat_id"], "reminder", None)),
    )

    # 8 jobs: daily_briefing, evening_summary, weekly_stats, run_owner_reminders,
    # watch_important_emails, send_proactive_insight, log_memory_usage, check_heroku_health
    assert len(scheduler.jobs) == 8
    briefing_job = scheduler.jobs[0][0]
    reminder_job = scheduler.jobs[3][0]

    briefing_job()
    reminder_job()

    assert sent[0][0] == "999"
    assert sent[1][0] == "999"
