"""Tests for SandyState and create_initial_state."""

import unittest
from datetime import datetime


class TestSandyState(unittest.TestCase):

    def setUp(self):
        from app.agent.graph.state import create_initial_state, merge_state, SandyState
        self.create_initial_state = create_initial_state
        self.merge_state = merge_state
        self.SandyState = SandyState

    def test_create_initial_state_required_fields(self):
        """يجب أن تملأ الحقول الأساسية صح."""
        state = self.create_initial_state("أنشئ مهمة", "u123", "c456")

        self.assertEqual(state["message"], "أنشئ مهمة")
        self.assertEqual(state["user_id"], "u123")
        self.assertEqual(state["chat_id"], "c456")
        self.assertEqual(state["source"], "user")

    def test_create_initial_state_session_id_unique(self):
        """كل state يحصل على session_id فريد."""
        s1 = self.create_initial_state("msg1", "u1", "c1")
        s2 = self.create_initial_state("msg2", "u1", "c1")

        self.assertNotEqual(s1["session_id"], s2["session_id"])

    def test_create_initial_state_redis_key_format(self):
        """مفتاح Redis يجب أن يكون بالتنسيق الصحيح."""
        state = self.create_initial_state("hello", "user_99", "chat_77")

        self.assertEqual(state["stm_redis_key"], "stm:chat_77:user_99")

    def test_create_initial_state_defaults(self):
        """الحقول الاختيارية تبدأ None أو قيمة افتراضية."""
        state = self.create_initial_state("hello", "u1", "c1")

        self.assertIsNone(state["intent"])
        self.assertIsNone(state["confidence"])
        self.assertIsNone(state["mood"])
        self.assertIsNone(state["persona_snippet"])
        self.assertIsNone(state["final_response"])
        self.assertIsNone(state["error"])
        self.assertFalse(state["requires_clarification"])
        self.assertEqual(state["conversation_history"], [])
        self.assertEqual(state["pending_archived"], [])

    def test_create_initial_state_created_at_is_valid_iso(self):
        """created_at يجب أن يكون ISO format صالح."""
        state = self.create_initial_state("hello", "u1", "c1")

        # يجب أن لا يرمي exception
        datetime.fromisoformat(state["created_at"])

    def test_create_initial_state_proactive_source(self):
        """يمكن إنشاء state بـ source=proactive."""
        state = self.create_initial_state("check tasks", "u1", "c1", source="proactive")

        self.assertEqual(state["source"], "proactive")

    def test_create_initial_state_with_existing_pending(self):
        """يمكن تمرير pending_state موجود."""
        pending = {"pending_type": "confirmation", "pending_id": "abc123"}
        state = self.create_initial_state("تمام", "u1", "c1", pending_state=pending)

        self.assertEqual(state["pending_state"]["pending_id"], "abc123")

    def test_merge_state_updates_fields(self):
        """merge_state يحدث الحقول المطلوبة فقط."""
        state = self.create_initial_state("hello", "u1", "c1")
        updated = self.merge_state(state, {
            "intent": "task.create",
            "confidence": 0.95,
            "mood": "calm",
        })

        self.assertEqual(updated["intent"], "task.create")
        self.assertEqual(updated["confidence"], 0.95)
        self.assertEqual(updated["mood"], "calm")
        self.assertEqual(updated["message"], "hello")  # لم يتغير

    def test_merge_state_does_not_mutate_original(self):
        """merge_state لا يعدّل الـ state الأصلي."""
        state = self.create_initial_state("hello", "u1", "c1")
        self.merge_state(state, {"intent": "task.create"})

        self.assertIsNone(state["intent"])  # الأصلي لم يتغير

    def test_state_chat_id_and_user_id_coerced_to_str(self):
        """chat_id و user_id يتحولان لـ string حتى لو جاءوا int."""
        state = self.create_initial_state("hello", 123, 456)

        self.assertIsInstance(state["user_id"], str)
        self.assertIsInstance(state["chat_id"], str)

    def test_persona_intensity_valid_values(self):
        """persona_intensity يقبل القيم المعرّفة في الخطة."""
        valid = {"minimal", "standard", "empathetic", "playful", "formal"}
        state = self.create_initial_state("hello", "u1", "c1")

        for intensity in valid:
            updated = self.merge_state(state, {"persona_intensity": intensity})
            self.assertEqual(updated["persona_intensity"], intensity)


if __name__ == "__main__":
    unittest.main()
