from types import SimpleNamespace

from app.api.webhook import create_telegram_webhook_app
from app.utils.error_tracking import log_unhandled_exception


class _FakeCollection:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(document)


class _FakeMongoDB:
    def __init__(self):
        self.collections = {}
        self.pinged = False

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _FakeCollection()
        return self.collections[name]

    def command(self, name):
        if name != "ping":
            raise AssertionError(name)
        self.pinged = True


class _FakeTelegramBot:
    def __init__(self, *, should_fail=False):
        self.should_fail = should_fail

    def get_me(self):
        return SimpleNamespace(id=42, username="sandy_bot")

    def process_new_updates(self, updates):
        if self.should_fail:
            raise RuntimeError("telegram failed")

    def callback_query_handler(self, func):
        def decorator(f):
            return f
        return decorator


def test_log_unhandled_exception_persists_stack_trace():
    mongo_db = _FakeMongoDB()

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        stored = log_unhandled_exception(mongo_db, exc, chat_id=123, source="tests")

    assert stored is True
    document = mongo_db.collections["sandy_error_logs"].documents[0]
    assert document["chat_id"] == 123
    assert document["source"] == "tests"
    assert document["exception_type"] == "RuntimeError"
    assert "boom" in document["message"]
    assert "RuntimeError" in document["stack_trace"]
    assert document["timestamp"].tzinfo is not None


def test_health_endpoint_reports_services_and_logs_webhook_error():
    mongo_db = _FakeMongoDB()
    bot = _FakeTelegramBot(should_fail=True)
    app = create_telegram_webhook_app(
        telegram_bot=bot,
        webhook_path="/webhook",
        mongo_db=mongo_db,
        semantic_memory_stats_fn=lambda: {"path": "/tmp/chroma", "facts": 2, "conversations": 3},
    )

    client = app.test_client()

    health_response = client.get("/health")
    assert health_response.status_code == 200
    payload = health_response.get_json()
    assert payload["mongo"]["ok"] is True
    assert payload["chroma"]["ok"] is True
    assert payload["telegram"]["ok"] is True

    webhook_response = client.post(
        "/webhook",
        data='{"message": {"chat": {"id": 777}}}',
        content_type="application/json",
    )
    assert webhook_response.status_code == 200
    document = mongo_db.collections["sandy_error_logs"].documents[-1]
    assert document["chat_id"] == 777
    assert document["source"] == "webhook"