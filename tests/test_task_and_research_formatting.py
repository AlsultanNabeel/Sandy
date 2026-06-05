import os
import sys
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud"))

from app.agent.executor.task_handlers import handle_task_action
from app.agent.executor.task_handlers import _task_callback_to_message
from app.agent.executor.task_handlers import _build_task_inline_markup
from app.features.google_tasks import build_task_display
from app.features.google_tasks import resolve_task_reference_for_write
from app.features.research_formatter import summarize_research_results
from app.utils.user_profiles import set_active_user_profile

_OWNER_PROFILE = {"relation": "owner", "permissions": "all", "tone": "casual", "name": "Test"}


def test_build_task_display_uses_numbered_rows_and_due_dates():
    tasks = [
        {"id": "1", "text": "شراء حليب", "due": "2026-05-05T10:00:00+02:00", "due_at": ""},
        {"id": "2", "text": "اتصال الأم", "due": "", "due_at": ""},
    ]

    with patch("app.features.google_tasks.load_tasks", return_value=tasks):
        text, aliases = build_task_display(mongo_db=None, tasks_file=None)

    assert "1. شراء حليب — الموعد:" in text
    assert "2. اتصال الأم — الموعد: بدون موعد" in text
    assert aliases["T1"]["id"] == "1"


def test_handle_task_list_includes_quick_reply_markup():
    tasks = [{"id": "1", "text": "شراء حليب", "due": "", "due_at": ""}]
    session = {}
    set_active_user_profile(_OWNER_PROFILE)

    with patch("app.features.google_tasks.load_tasks", return_value=tasks):
        result = handle_task_action(
            {"action": "list"},
            user_message="شو مهامي",
            normalized_user_message="شو مهامي",
            session=session,
            session_file=None,
            mongo_db=None,
            tasks_file=None,
            create_chat_completion_fn=lambda *args, **kwargs: SimpleNamespace(),
            save_session_fn=lambda *args, **kwargs: None,
        )

    set_active_user_profile(None)
    assert result["handled"] is True
    assert "reply_markup" in result
    assert "شراء حليب" in result["reply"]


def test_task_callback_translates_to_action_message():
    task_aliases = {"T1": {"id": "1", "text": "شراء حليب"}}

    assert _task_callback_to_message("task:complete:T1", task_aliases) == "كملي المهمة T1"
    assert _task_callback_to_message("task:delete:T1", task_aliases) == "احذفي المهمة T1"
    assert _task_callback_to_message("task:complete:id:1", {}) == "كملي المهمة ID:1"
    assert _task_callback_to_message("task:delete:id:1", {}) == "احذفي المهمة ID:1"
    assert _task_callback_to_message("task:add", task_aliases) == "أضف مهمة"


def test_task_inline_markup_uses_stable_task_ids_when_available():
    markup = _build_task_inline_markup({"T1": {"id": "abc123", "text": "شراء حليب"}})
    callbacks = [btn.callback_data for row in markup.keyboard for btn in row]

    assert "task:add" in callbacks
    assert "task:complete:id:abc123" in callbacks
    assert "task:delete:id:abc123" in callbacks


def test_task_reference_resolver_accepts_explicit_id_prefix():
    tasks = [{"id": "abc123", "text": "شراء حليب", "done": False}]

    with patch("app.features.google_tasks.load_tasks", return_value=tasks):
        result = resolve_task_reference_for_write("ID:abc123", mongo_db=None, tasks_file=None, aliases={})

    assert result["status"] == "matched"
    assert result["task"]["id"] == "abc123"


def test_research_summary_includes_source_and_summary_sections():
    results = [
        {
            "source_title": "OpenAI launches GPT-5",
            "source_url": "https://example.com/openai-gpt-5",
            "page_data": {"summary": "GPT-5 brings major improvements.", "institution_name": "Example News"},
        }
    ]

    text = summarize_research_results(results, requested_count=1)

    assert "المصدر:" in text
    assert "الملخص:" in text
    assert "GPT-5" in text