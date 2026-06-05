import json


from app.agent.self_coding import task_state
from app.agent.self_coding import _redis as sa_redis


class FakeRedisClient:
    def __init__(self):
        self.store = {}

    def hset(self, key, mapping=None, **kwargs):
        # simple mapping write
        existing = self.store.get(key, {})
        if isinstance(existing, str):
            existing = {}
        existing.update(mapping or {})
        self.store[key] = existing

    def hget(self, key, field):
        val = self.store.get(key)
        if not val:
            return None
        if isinstance(val, dict):
            # return the raw value as a JSON string if it's a nested structure
            v = val.get(field)
            if v is None:
                return None
            # If already a string, return it; else dump to JSON-like string
            if isinstance(v, str):
                return v
            try:
                return json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)

    def hgetall(self, key):
        val = self.store.get(key)
        if not val:
            return {}
        if isinstance(val, dict):
            # Return dict of strings
            out = {}
            for k, v in val.items():
                out[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            return out
        return {}

    def expire(self, key, ex):
        # noop for fake
        return True

    def set(self, key, value, ex=None, nx=False):
        if nx:
            if key in self.store:
                return False
            self.store[key] = value
            return True
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def pipeline(self):
        # Return self for simplicity; execute is noop
        return self

    def execute(self):
        return True


def test_save_and_get_agent_resume_state_monkeypatch(monkeypatch):
    fake = FakeRedisClient()
    monkeypatch.setattr(sa_redis, "get_client", lambda: fake)

    tid = "sa_test_1234"
    resume = {"messages": [{"role": "user", "content": "Hi"}], "tool_use_id": "t1"}
    # Call save with chat_id so pipeline path sets waiting_user key
    task_state.save_agent_resume_state(tid, resume_state=resume, current_feature_index=2, chat_id="42")

    # Now read back via get_agent_resume_state — ensure it's parsed
    rs = task_state.get_agent_resume_state(tid)
    assert isinstance(rs, dict)
    assert rs.get("messages")[0]["content"] == "Hi"

    # Ensure waiting_user index set
    waiting = fake.get(sa_redis.k_waiting_user("42"))
    assert waiting == tid

