import threading
import time

from app.agent.self_coding import task_state
from app.agent.self_coding import _redis as sa_redis
from app.agent.self_coding import shutdown as sa_shutdown


class FakeRedisClient2:
    def __init__(self):
        self.store = {}

    def hset(self, key, mapping=None, **kwargs):
        existing = self.store.get(key, {})
        if isinstance(existing, str):
            existing = {}
        existing.update(mapping or {})
        self.store[key] = existing

    def expire(self, key, ex):
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

    def delete(self, key):
        return self.store.pop(key, None)

    def pipeline(self):
        return self

    def execute(self):
        return True

    def hget(self, key, field):
        val = self.store.get(key)
        if not val:
            return None
        if isinstance(val, dict):
            v = val.get(field)
            if v is None:
                return None
            if isinstance(v, str):
                return v
            try:
                import json

                return json.dumps(v, ensure_ascii=False)
            except Exception:
                return str(v)
        return None

    def hgetall(self, key):
        val = self.store.get(key)
        if not val:
            return {}
        if isinstance(val, dict):
            out = {}
            import json

            for k, v in val.items():
                out[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            return out
        return {}


def test_sigterm_during_wait_and_resume(monkeypatch):
    fake = FakeRedisClient2()
    monkeypatch.setattr(sa_redis, "get_client", lambda: fake)

    task_id = "sa_integ_1"
    # Save resume state with chat_id so waiting index is set
    resume = {"messages": [{"role": "user", "content": "please confirm"}], "tool_use_id": "tu"}
    task_state.save_agent_resume_state(task_id, resume_state=resume, current_feature_index=0, chat_id="owner42")

    # Ensure resume_state present and waiting_user index set
    assert task_state.get_agent_resume_state(task_id) is not None
    assert fake.get(sa_redis.k_waiting_user("owner42")) == task_id

    # Now start a thread that waits for resume; we will simulate SIGTERM
    result = {}

    def waiter():
        ok = task_state.wait_for_resume(task_id, timeout=10, poll_interval=0.5)
        result["ok"] = ok

    th = threading.Thread(target=waiter)
    th.start()

    # Give the waiter a moment to start and enter loop
    time.sleep(0.5)

    # Simulate SIGTERM: request shutdown
    sa_shutdown.request_shutdown()

    # Wait for thread to observe shutdown and exit
    th.join(timeout=5)
    assert "ok" in result
    assert result["ok"] is False

    # After SIGTERM, resume_state must still be present and waiting_user index intact
    assert task_state.get_agent_resume_state(task_id) is not None
    assert fake.get(sa_redis.k_waiting_user("owner42")) == task_id

    # Simulate worker restart: clear shutdown flag
    sa_shutdown.reset()

    # Owner signals resume
    task_state.signal_resume(task_id, agreed_solution="yes")

    # Now a fresh waiter should succeed
    ok2 = task_state.wait_for_resume(task_id, timeout=5, poll_interval=0.5)
    assert ok2 is True

    # After successful resume, waiting_user index should be cleared
    assert fake.get(sa_redis.k_waiting_user("owner42")) is None
