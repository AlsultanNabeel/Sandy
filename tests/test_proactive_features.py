from unittest.mock import MagicMock, patch

import app.api.telegram_runtime as runtime
from app.agent.interests_tracker import get_proactive_interest_candidate
from app.agent.proactive_context import build_proactive_need_hint


def _make_mongo(*, memory_docs=None, goal_docs=None, interest_docs=None):
    memory_docs = memory_docs or []
    goal_docs = goal_docs or []
    interest_docs = interest_docs or []

    memory_coll = MagicMock()
    memory_coll.find.return_value = memory_docs

    goal_coll = MagicMock()
    goal_coll.find.return_value = goal_docs

    interest_coll = MagicMock()
    interest_coll.find.return_value = interest_docs

    mongo_db = MagicMock()
    mongo_db.__getitem__.side_effect = lambda key: {
        "sandy_memories": memory_coll,
        "sandy_goals": goal_coll,
    }[key]
    mongo_db._memory_coll = memory_coll
    mongo_db._goal_coll = goal_coll
    mongo_db._interest_coll = interest_coll
    return mongo_db


class TestProactiveNeedPrediction:
    def test_returns_hint_only_after_three_repeats(self):
        mongo_db = _make_mongo(
            memory_docs=[
                {"chat_id": "c1", "label": "work_pattern", "pattern": "كل أحد أرتب المهام"},
                {"chat_id": "c1", "label": "work_pattern", "pattern": "كل أحد أرتب المهام"},
                {"chat_id": "c1", "label": "work_pattern", "pattern": "كل أحد أرتب المهام"},
            ],
        )

        result = build_proactive_need_hint("c1", "u1", mongo_db=mongo_db)

        assert result is not None
        assert "كل أحد أرتب المهام" in result
        assert "3 مرات" in result

    def test_stays_silent_below_threshold(self):
        mongo_db = _make_mongo(
            memory_docs=[
                {"chat_id": "c1", "label": "habit", "pattern": "أراجع المهام صباحاً"},
                {"chat_id": "c1", "label": "habit", "pattern": "أراجع المهام صباحاً"},
            ],
        )

        result = build_proactive_need_hint("c1", "u1", mongo_db=mongo_db)

        assert result is None


class TestInterestCandidate:
    def test_returns_top_interest_after_three_mentions(self):
        mongo_db = MagicMock()
        interest_coll = MagicMock()
        interest_coll.find.return_value = [
            {"keyword": "python", "count": 4},
            {"keyword": "docker", "count": 2},
        ]
        mongo_db.__getitem__.return_value = interest_coll

        result = get_proactive_interest_candidate("c1", "u1", mongo_db=mongo_db)

        assert result == "python"


class TestProactiveScheduler:
    @patch("app.api.telegram_runtime.save_memory")
    @patch("app.api.telegram_runtime.get_proactive_interest_candidate", return_value="python")
    @patch("app.api.telegram_runtime.build_proactive_need_hint", return_value="[نمط متكرر: كل أحد أرتب المهام — ظهر 3 مرات]")
    def test_scheduler_registers_proactive_job_and_sends_message(
        self,
        _mock_need,
        _mock_interest,
        _mock_save_memory,
    ):
        scheduler = MagicMock()
        bot = MagicMock()
        agent = MagicMock()
        agent.memory = {"sandy_state": {}}
        agent.memory_file = "/tmp/sandy-memory.json"
        agent.mongo_db = MagicMock()

        with patch("app.utils.user_profiles.OWNER_CHAT_ID", "123"):
            runtime.configure_sandy_scheduler(
                scheduler=scheduler,
                agent=agent,
                telegram_bot=bot,
                sandy_user_chat_id="123",
                check_reminders_fn=MagicMock(),
            )

        assert scheduler.add_job.call_count == 5

        proactive_job = scheduler.add_job.call_args_list[2][0][0]

        with patch("app.agent.executor.dispatch.execute_operational_action", return_value={"handled": True, "reply": "📚 Python مفيد اليوم"}):
            proactive_job()

        bot.send_message.assert_called_once()
        sent_text = bot.send_message.call_args[0][1]
        assert "نمط متكرر" in sent_text
        assert "python" in sent_text.lower()
