"""Tests for clarify_node and response_node."""

import unittest
from app.agent.graph.state import create_initial_state, merge_state


def _state(**kwargs):
    s = create_initial_state("ممكن توضح؟", "u1", "c1")
    return merge_state(s, kwargs)


class TestClarifyNode(unittest.TestCase):

    def _run(self, state):
        from app.agent.nodes.clarify import clarify_node
        return clarify_node(state)

    def test_uses_clarification_question(self):
        state = _state(
            requires_clarification=True,
            clarification_question="إيش تقصد بـ 'قريب'؟",
        )
        result = self._run(state)
        self.assertEqual(result["final_response"], "إيش تقصد بـ 'قريب'؟")

    def test_fallback_to_default_question(self):
        state = _state(
            requires_clarification=True,
            clarification_question=None,
            persona_snippet="وضّحلي أكثر؟",
        )
        result = self._run(state)
        self.assertIn("توضّح", result["final_response"])

    def test_fallback_when_no_question_or_snippet(self):
        state = _state(requires_clarification=True, clarification_question=None, persona_snippet=None)
        result = self._run(state)
        self.assertIn("توضّح", result["final_response"])

    def test_sets_pending_state(self):
        state = _state(requires_clarification=True, clarification_question="متى؟")
        result = self._run(state)
        self.assertIsNotNone(result["pending_state"])
        self.assertEqual(result["pending_state"]["type"], "clarification")

    def test_pending_has_original_message(self):
        state = _state(requires_clarification=True, clarification_question="متى؟")
        result = self._run(state)
        self.assertEqual(result["pending_state"]["original_message"], "ممكن توضح؟")

    def test_execution_result_source(self):
        state = _state(requires_clarification=True, clarification_question="أيش؟")
        result = self._run(state)
        self.assertEqual(result["execution_result"]["source"], "clarify_node")

    def test_execution_result_handled_true(self):
        state = _state(requires_clarification=True, clarification_question="أيش؟")
        result = self._run(state)
        self.assertTrue(result["execution_result"]["handled"])

    def test_preserves_message(self):
        state = _state(requires_clarification=True, clarification_question="؟")
        result = self._run(state)
        self.assertEqual(result["message"], "ممكن توضح؟")

    def test_pending_has_expiry(self):
        state = _state(requires_clarification=True, clarification_question="متى؟")
        result = self._run(state)
        self.assertIn("expires_at", result["pending_state"])
        self.assertTrue(result["pending_state"]["expires_at"])


class TestResponseNode(unittest.TestCase):

    def _run(self, state):
        from app.agent.nodes.response import response_node
        return response_node(state)

    def test_uses_existing_final_response(self):
        state = _state(final_response="تم إضافة المهمة ✅")
        result = self._run(state)
        self.assertEqual(result["final_response"], "تم إضافة المهمة ✅")

    def test_uses_execution_reply_when_no_final(self):
        state = _state(
            final_response=None,
            execution_result={"reply": "تم الحذف", "handled": True, "source": "execute_node"},
        )
        result = self._run(state)
        self.assertEqual(result["final_response"], "تم الحذف")

    def test_prepends_template_to_reply(self):
        state = _state(
            final_response=None,
            response_template="نتيجة الأمر:",
            execution_result={"reply": "تم", "handled": True},
        )
        result = self._run(state)
        self.assertIn("نتيجة الأمر:", result["final_response"])
        self.assertIn("تم", result["final_response"])

    def test_skips_template_if_already_in_reply(self):
        state = _state(
            final_response=None,
            response_template="تم",
            execution_result={"reply": "تم المهمة", "handled": True},
        )
        result = self._run(state)
        self.assertEqual(result["final_response"].count("تم"), 1)

    def test_uses_fallback_when_no_reply(self):
        state = _state(
            final_response=None,
            persona_snippet="هيه، وينك؟ 🤍",
            execution_result={"reply": "", "handled": False},
        )
        result = self._run(state)
        self.assertNotEqual(result["final_response"], "هيه، وينك؟ 🤍")
        self.assertTrue(len(result["final_response"]) > 0)

    def test_fallback_when_nothing_available(self):
        state = _state(
            final_response=None,
            persona_snippet=None,
            execution_result={"reply": "", "handled": False},
        )
        result = self._run(state)
        self.assertIn("خطأ", result["final_response"])

    def test_preserves_reply_markup(self):
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "yes"}]]}
        state = _state(
            final_response=None,
            execution_result={"reply": "تأكيد؟", "handled": True, "reply_markup": markup},
        )
        result = self._run(state)
        self.assertEqual(result["execution_result"]["reply_markup"], markup)

    def test_marks_execution_result_final(self):
        state = _state(
            final_response="تم",
            execution_result={"reply": "تم", "handled": True, "source": "execute_node"},
        )
        result = self._run(state)
        self.assertTrue(result["execution_result"]["final"])

    def test_preserves_source_from_execution_result(self):
        state = _state(
            execution_result={"reply": "تم", "handled": True, "source": "pending_node"},
        )
        result = self._run(state)
        self.assertEqual(result["execution_result"]["source"], "pending_node")

    def test_preserves_message(self):
        state = _state(final_response="تم")
        result = self._run(state)
        self.assertEqual(result["message"], "ممكن توضح؟")

    def test_no_execution_result_uses_fallback(self):
        state = _state(persona_snippet="أهلاً!", execution_result=None)
        result = self._run(state)
        self.assertNotEqual(result["final_response"], "أهلاً!")
        self.assertTrue(len(result["final_response"]) > 0)


if __name__ == "__main__":
    unittest.main()
