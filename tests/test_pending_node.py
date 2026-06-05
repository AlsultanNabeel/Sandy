"""Tests for pending_node."""

import unittest
from unittest.mock import patch
from app.agent.graph.state import create_initial_state, merge_state


def _make_state(message="تمام", pending_type="task", pending_action="delete_one"):
    state = create_initial_state(message, "u1", "c1")
    return merge_state(state, {
        "pending_state": {
            "type": pending_type,
            "action": pending_action,
            "pending_id": "abc123",
            "nonce": "test_nonce",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "consumed_at": "",
            "created_at": "2026-05-10T00:00:00+00:00",
        }
    })


class TestPendingNode(unittest.TestCase):

    def _run(self, state, execute_result):
        with patch("app.agent.nodes.pending.execute_pending_action",
                   return_value=execute_result):
            from app.agent.nodes.pending import pending_node
            return pending_node(state)

    def test_confirmation_yes_returns_reply(self):
        state = _make_state("تمام", "task", "delete_one")
        result = self._run(state, {"handled": True, "reply": "تم الحذف ✅"})
        self.assertEqual(result["final_response"], "تم الحذف ✅")

    def test_confirmation_no_clears_pending(self):
        state = _make_state("لأ", "task", "delete_one")
        result = self._run(state, {"handled": True, "reply": "تمام، لغيت العملية."})
        self.assertIn("لغيت", result["final_response"])

    def test_not_handled_preserves_state(self):
        state = _make_state("شيء ثاني", "task", "delete_one")
        result = self._run(state, {"handled": False})
        self.assertIsNone(result.get("final_response"))

    def test_execution_result_has_source(self):
        state = _make_state("تمام")
        result = self._run(state, {"handled": True, "reply": "تم"})
        self.assertEqual(result["execution_result"]["source"], "pending_node")

    def test_preserves_original_message(self):
        state = _make_state("تمام الله يخليك")
        result = self._run(state, {"handled": True, "reply": "تم"})
        self.assertEqual(result["message"], "تمام الله يخليك")

    def test_exception_returns_error_reply(self):
        state = _make_state("تمام")
        with patch("app.agent.nodes.pending.execute_pending_action", side_effect=Exception("DB down")):
            from app.agent.nodes.pending import pending_node
            result = pending_node(state)
        self.assertIn("خطأ", result.get("execution_result", {}).get("reply", ""))

    def test_reply_markup_passed_through(self):
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "yes"}]]}
        state = _make_state("تمام")
        result = self._run(state, {"handled": True, "reply": "تأكيد؟", "reply_markup": markup})
        self.assertEqual(result["execution_result"]["reply_markup"], markup)

    def test_build_session_from_state(self):
        from app.agent.nodes.pending import _build_session_from_state
        state = _make_state("تمام")
        session = _build_session_from_state(state)
        self.assertIsNotNone(session["pending_action"])
        self.assertEqual(session["user_id"], "u1")

    def test_no_pending_returns_not_handled(self):
        state = create_initial_state("مرحبا", "u1", "c1")
        result = self._run(state, {"handled": False})
        self.assertFalse(result["execution_result"]["handled"])


if __name__ == "__main__":
    unittest.main()
