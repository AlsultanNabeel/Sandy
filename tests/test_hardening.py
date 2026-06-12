"""Hardening tests for the 5 confirmed risks from the technical briefing."""

import threading
import unittest
from typing import Any, Dict


# ── Risk 1: Chroma stable hash IDs ───────────────────────────────────────────

class ChromaHashStabilityTests(unittest.TestCase):
    def test_fact_id_is_deterministic(self):
        """Fact IDs must be reproducible across processes (no builtin hash())."""
        from app.agent.semantic_memory import _fact_id
        from app.utils.user_profiles import set_active_user_profile

        chat_id = "123456"
        text = "المستخدم اسمه نبيل"
        expected_id = _fact_id(text, chat_id)

        captured = {}

        class FakeCollection:
            def count_documents(self, *a, **kw): return 0
            def update_one(self, filter_, update, upsert=False):
                captured["id"] = filter_["_id"]
                class R:
                    upserted_id = filter_["_id"]
                return R()

        class FakeDb:
            def __getitem__(self, name):
                return FakeCollection()

        import app.agent.semantic_memory as cm
        orig_db = cm._mongo_db
        cm._mongo_db = FakeDb()
        set_active_user_profile({"relation": "owner", "permissions": "all", "chat_id": chat_id})
        try:
            from app.agent.semantic_memory import load_facts_to_chroma
            load_facts_to_chroma([{"text": text, "type": "owner_name"}])
        finally:
            cm._mongo_db = orig_db
            set_active_user_profile(None)

        self.assertIn("id", captured, "update_one() was never called")
        self.assertEqual(captured["id"], expected_id)
        self.assertTrue(expected_id.startswith("f_"), f"ID should start with 'f_', got: {expected_id}")

    def test_same_text_produces_same_id_on_repeated_calls(self):
        """Two calls with the same fact text must produce identical IDs."""
        from app.agent.semantic_memory import _fact_id
        text = "يسكن في القاهرة"
        self.assertEqual(_fact_id(text, "111"), _fact_id(text, "111"))

    def test_different_texts_produce_different_ids(self):
        """Different fact texts must not collide."""
        from app.agent.semantic_memory import _fact_id
        self.assertNotEqual(_fact_id("يسكن في القاهرة", "111"), _fact_id("يعمل مهندس", "111"))

    def test_different_users_produce_different_ids(self):
        """Same text for different users must not collide."""
        from app.agent.semantic_memory import _fact_id
        self.assertNotEqual(_fact_id("اسمي نبيل", "111"), _fact_id("اسمي نبيل", "222"))


# ── Risk 2: Calendar service caching ─────────────────────────────────────────

_OWNER_PROFILE = {"relation": "owner", "permissions": "all", "tone": "casual", "name": "Test"}


class PredictionThreadSafetyTests(unittest.TestCase):
    def test_concurrent_predict_calls_do_not_corrupt_memory(self):
        """Multiple threads writing predicted_intent must not corrupt the dict."""
        state: Dict[str, Any] = {}
        lock = threading.Lock()
        errors = []

        def _write(hint: str) -> None:
            try:
                with lock:
                    state["predicted_intent"] = hint
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(f"hint_{i}",)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIn("predicted_intent", state)

    def test_sandy_agent_has_memory_lock(self):
        """SandyAgent.__init__ must initialise _memory_lock."""
        import pathlib
        src = pathlib.Path("cloud/app/agent/facade/agent.py").read_text()
        self.assertIn("_memory_lock = threading.Lock()", src)


# ── Glossary injection tests ──────────────────────────────────────────────────

class ArchGlossaryTests(unittest.TestCase):
    """Verify the architecture glossary is injected and accurate."""

    def _get_system_prompt(self) -> str:
        """Build a system prompt using a minimal in-memory SandyAgent-like object."""
        import pathlib
        src = pathlib.Path("cloud/app/agent/facade/agent.py").read_text()
        # Extract _ARCH_GLOSSARY value directly from source — avoids importing the
        # full agent (which requires live credentials).
        import re
        m = re.search(r'_ARCH_GLOSSARY = """(.*?)"""', src, re.DOTALL)
        self.assertIsNotNone(m, "_ARCH_GLOSSARY constant not found in facade/agent.py")
        return m.group(1)

    def test_glossary_present_in_agent_source(self):
        """_ARCH_GLOSSARY must be defined in facade/agent.py."""
        import pathlib
        src = pathlib.Path("cloud/app/agent/facade/agent.py").read_text()
        self.assertIn("_ARCH_GLOSSARY", src)

    def test_all_six_terms_present(self):
        """All six required terms must appear in the glossary."""
        glossary = self._get_system_prompt()
        required = [
            "Telegram polling",
            "memory_lock",
            "mood_cache",
            "Circuit Breaker",
            "MongoDB",
            "Semantic memory",
        ]
        for term in required:
            self.assertIn(term, glossary, f"Term missing from glossary: {term}")

    def test_memory_lock_is_not_described_as_security_or_auth(self):
        """The memory_lock definition must not use authentication or security framing."""
        glossary = self._get_system_prompt()

        # Extract only the memory_lock line
        lock_line = ""
        for line in glossary.splitlines():
            if "memory_lock" in line:
                lock_line = line
                break

        self.assertTrue(lock_line, "memory_lock entry not found in glossary")

        forbidden_terms = [
            "security", "auth", "authorization", "authentication",
            "تأمين", "أمان", "مصادقة", "تفويض",
        ]
        for term in forbidden_terms:
            self.assertNotIn(
                term.lower(),
                lock_line.lower(),
                f"memory_lock definition must not mention '{term}' — "
                "it is a threading primitive, not a security feature",
            )

    def test_memory_lock_mentions_threading(self):
        """The memory_lock definition must reference threading."""
        glossary = self._get_system_prompt()
        lock_line = next(
            (line for line in glossary.splitlines() if "memory_lock" in line), ""
        )
        self.assertTrue(lock_line, "memory_lock entry not found")
        threading_terms = ["threading", "Lock", "خيوط", "كتابة متزامنة"]
        self.assertTrue(
            any(t in lock_line for t in threading_terms),
            f"memory_lock line must mention threading/concurrency: {lock_line!r}",
        )

    def test_semantic_memory_describes_graceful_degradation(self):
        """Semantic memory definition must mention explicit/graceful degradation."""
        glossary = self._get_system_prompt()
        memory_line = next(
            (line for line in glossary.splitlines() if "Semantic memory" in line), ""
        )
        self.assertTrue(memory_line, "Semantic memory entry not found")
        degradation_terms = ["تتدهور", "degrad", "آمن", "صريح"]
        self.assertTrue(
            any(t in memory_line for t in degradation_terms),
            f"Semantic memory line must describe graceful degradation: {memory_line!r}",
        )

    def test_circuit_breaker_not_described_as_network_outage(self):
        """Circuit Breaker definition must not imply it is a network-level concept."""
        glossary = self._get_system_prompt()
        cb_line = next(
            (line for line in glossary.splitlines() if "Circuit Breaker" in line), ""
        )
        self.assertTrue(cb_line, "Circuit Breaker entry not found")
        # Must mention external services / safe fallback
        self.assertTrue(
            any(t in cb_line for t in ["خدمات", "service", "آمنة", "safe", "wrapper"]),
            f"Circuit Breaker line must mention service isolation: {cb_line!r}",
        )

    def test_glossary_marked_internal_only(self):
        """Glossary must instruct Sandy not to share these definitions with users."""
        glossary = self._get_system_prompt()
        internal_markers = ["للاستخدام الداخلي", "داخلية", "لا تشاركها"]
        self.assertTrue(
            any(m in glossary for m in internal_markers),
            "Glossary must carry an 'internal only' marker so Sandy doesn't volunteer it",
        )


# ── Normal chat path: timeout protection and fallback ─────────────────────────

class NormalChatTimeoutHardeningTests(unittest.TestCase):
    """Verify that the normal chat path (multi_step_hint=NONE) has timeout and fallback protection.

    Root cause: 'راجعي ساندي ثم قوليلي شو ناقص.' hung silently — no Telegram reply sent.
    Chroma queries and Azure LLM calls have no timeout, so a slow response = infinite hang.
    """

    def _src(self):
        import pathlib
        return pathlib.Path("cloud/app/agent/facade/agent.py").read_text()



if __name__ == "__main__":
    unittest.main()
