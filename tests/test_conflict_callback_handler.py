from types import SimpleNamespace
from unittest.mock import patch

from app.api.telegram_handlers import process_conflict_resolution_callback
from app.agent.conflict_resolution import create_conflict_inline_markup
from app.utils.user_profiles import active_profile_allows_privileged_access


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, filt, update, upsert=False):
        self.calls.append({"filter": filt, "update": update, "upsert": upsert})


class _FakeMongo(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _FakeCollection()
        return dict.__getitem__(self, name)


class _FakeBot:
    def __init__(self):
        self.answers = []
        self.messages = []
        self.edits = []

    def answer_callback_query(self, call_id, text=None):
        self.answers.append((call_id, text))

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text, parse_mode))

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edits.append((chat_id, message_id, reply_markup))


def _build_call(data: str):
    return SimpleNamespace(
        id="cb1",
        data=data,
        message=SimpleNamespace(chat=SimpleNamespace(id=777), message_id=12),
    )


def test_conflict_yes_updates_event_and_mongodb():
    bot = _FakeBot()
    mongo = _FakeMongo()
    agent = SimpleNamespace(
        session={
            "pending_conflict_resolution": {
                "id": "abc123",
                "event_id": "evt_1",
                "title": "اجتماع المشروع",
                "suggestions": [{"start_iso": "2026-05-10T10:00:00+03:00", "end_iso": "2026-05-10T11:00:00+03:00"}],
            }
        },
        mongo_db=mongo,
    )
    persisted = {"called": False}

    def _persist():
        persisted["called"] = True

    with patch("app.features.google_calendar.update_calendar_event", return_value={"success": True}):
        handled = process_conflict_resolution_callback(
            telegram_bot=bot,
            agent=agent,
            call=_build_call("conflict:yes:abc123"),
            profile_allows_full_access_fn=lambda _chat_id: True,
            log_handler_exception_fn=lambda *args, **kwargs: None,
            persist_agent_session_fn=_persist,
        )

    assert handled is True
    assert "pending_conflict_resolution" not in agent.session
    assert persisted["called"] is True
    assert any("عدّلت" in text for _, text, _ in bot.messages)
    assert len(mongo["conflict_resolutions"].calls) == 1
    assert len(mongo["calendar_events"].calls) == 1


def test_conflict_yes_runs_calendar_update_in_owner_context():
    bot = _FakeBot()
    agent = SimpleNamespace(
        session={
            "pending_conflict_resolution": {
                "id": "ctx123",
                "event_id": "evt_ctx",
                "title": "موعد",
                "suggestions": [{"start_iso": "2026-05-10T10:00:00+03:00", "end_iso": "2026-05-10T11:00:00+03:00"}],
            }
        },
        mongo_db=None,
    )
    seen = {"privileged": False}

    def _update_calendar_event(*, event_id, start_iso, end_iso):
        seen["privileged"] = active_profile_allows_privileged_access()
        return {"success": True, "link": "http://example.com"}

    with patch("app.features.google_calendar.update_calendar_event", side_effect=_update_calendar_event):
        handled = process_conflict_resolution_callback(
            telegram_bot=bot,
            agent=agent,
            call=_build_call("conflict:yes:ctx123"),
            profile_allows_full_access_fn=lambda _chat_id: True,
            log_handler_exception_fn=lambda *args, **kwargs: None,
            persist_agent_session_fn=lambda: None,
        )

    assert handled is True
    assert seen["privileged"] is True


def test_conflict_no_keeps_event_and_sends_ack():
    bot = _FakeBot()
    mongo = _FakeMongo()
    agent = SimpleNamespace(
        session={
            "pending_conflict_resolution": {
                "id": "abc999",
                "event_id": "evt_2",
                "title": "اجتماع",
                "suggestions": [{"start_iso": "2026-05-10T10:00:00+03:00", "end_iso": "2026-05-10T11:00:00+03:00"}],
            }
        },
        mongo_db=mongo,
    )

    handled = process_conflict_resolution_callback(
        telegram_bot=bot,
        agent=agent,
        call=_build_call("conflict:no:abc999"),
        profile_allows_full_access_fn=lambda _chat_id: True,
        log_handler_exception_fn=lambda *args, **kwargs: None,
        persist_agent_session_fn=lambda: None,
    )

    assert handled is True
    assert "pending_conflict_resolution" not in agent.session
    assert any(text == "تمام، خلّيته كما هو" for _, text, _ in bot.messages)
    assert len(mongo["conflict_resolutions"].calls) == 1


def test_create_conflict_inline_markup_contains_expected_buttons():
    markup = create_conflict_inline_markup(
        "cid_1",
        suggestions=[
            {"start_iso": "2026-05-10T08:00:00+03:00", "end_iso": "2026-05-10T09:00:00+03:00"},
            {"start_iso": "2026-05-10T13:00:00+03:00", "end_iso": "2026-05-10T14:00:00+03:00"},
        ],
    )
    labels = [btn.text for row in markup.keyboard for btn in row]
    callbacks = [btn.callback_data for row in markup.keyboard for btn in row]

    assert any(label.startswith("1) 08:00 AM - 09:00 AM") for label in labels)
    assert any(label.startswith("2) 01:00 PM - 02:00 PM") for label in labels)
    assert "✅ نعم عدّل" in labels
    assert "❌ لا خلّيه" in labels
    assert "conflict:pick:cid_1:0" in callbacks
    assert "conflict:pick:cid_1:1" in callbacks
    assert "conflict:yes:cid_1" in callbacks
    assert "conflict:no:cid_1" in callbacks


def test_conflict_pick_specific_suggestion_updates_event():
    bot = _FakeBot()
    mongo = _FakeMongo()
    agent = SimpleNamespace(
        session={
            "pending_conflict_resolution": {
                "id": "pick123",
                "event_id": "evt_3",
                "title": "دراسة رياضيات",
                "suggestions": [
                    {"start_iso": "2026-05-10T08:00:00+03:00", "end_iso": "2026-05-10T09:00:00+03:00"},
                    {"start_iso": "2026-05-10T13:00:00+03:00", "end_iso": "2026-05-10T14:00:00+03:00"},
                ],
            }
        },
        mongo_db=mongo,
    )

    with patch("app.features.google_calendar.update_calendar_event", return_value={"success": True}):
        handled = process_conflict_resolution_callback(
            telegram_bot=bot,
            agent=agent,
            call=_build_call("conflict:pick:pick123:1"),
            profile_allows_full_access_fn=lambda _chat_id: True,
            log_handler_exception_fn=lambda *args, **kwargs: None,
            persist_agent_session_fn=lambda: None,
        )

    assert handled is True
    assert any("13:00" in text or "01:00 PM" in text for _, text, _ in bot.messages)
    assert len(mongo["calendar_events"].calls) == 1