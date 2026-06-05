"""Tests for graph.py — Sandy pipeline runner."""

import unittest
from unittest.mock import patch, MagicMock
from app.agent.graph.state import create_initial_state, merge_state


def _mock_maestro(state):
    return merge_state(state, {
        "intent": "task.create",
        "confidence": 0.9,
        "mood": "calm",
        "complexity": "simple",
        "requires_clarification": False,
        "routing_hint": "execute_direct",
        "persona_snippet": "خلص، سجّلتها!",
    })


def _mock_soul(state):
    return merge_state(state, {"persona_intensity": "standard"})


def _mock_router(state):
    return state


def _mock_execute(state):
    return merge_state(state, {
        "execution_result": {"handled": True, "reply": "تم إضافة المهمة ✅", "source": "execute_node"},
        "final_response": "تم إضافة المهمة ✅",
    })


def _mock_response(state):
    return state


class TestRunGraph(unittest.TestCase):

    def _run(self, message="أضف مهمة تسوق", intent="task.create",
             next_node="execute_node", execute_result=None):
        from app.agent.graph.graph import run_graph

        mock_execute_result = execute_result or {
            "execution_result": {"handled": True, "reply": "تم ✅", "source": "execute_node"},
            "final_response": "تم ✅",
        }

        def _exec_node(state):
            return merge_state(state, mock_execute_result)

        with patch("app.agent.graph.graph._route_intent", side_effect=_mock_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value=next_node), \
             patch("app.agent.graph.graph.execute_node", side_effect=_exec_node), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save"):
            return run_graph(message, "u1", "c1")

    def test_pipeline_produces_final_response(self):
        result = self._run()
        self.assertEqual(result["final_response"], "تم ✅")

    def test_preserves_message(self):
        result = self._run(message="أضف مهمة مهمة")
        self.assertEqual(result["message"], "أضف مهمة مهمة")

    def test_routes_to_pending_node(self):
        called = []

        def _pending(state):
            called.append("pending")
            return merge_state(state, {
                "execution_result": {"handled": True, "reply": "تم", "source": "pending_node"},
                "final_response": "تم",
            })

        from app.agent.graph.graph import run_graph
        with patch("app.agent.graph.graph._route_intent", side_effect=_mock_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value="pending_node"), \
             patch("app.agent.graph.graph.pending_node", side_effect=_pending), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save"):
            run_graph("تمام", "u1", "c1")

        self.assertIn("pending", called)

    def test_routes_to_clarify_node(self):
        called = []

        def _clarify(state):
            called.append("clarify")
            return merge_state(state, {
                "final_response": "إيش تقصد؟",
                "execution_result": {"handled": True, "reply": "إيش تقصد؟", "source": "clarify_node"},
            })

        from app.agent.graph.graph import run_graph
        with patch("app.agent.graph.graph._route_intent", side_effect=_mock_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value="clarify_node"), \
             patch("app.agent.graph.graph.clarify_node", side_effect=_clarify), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save"):
            run_graph("شيء غامض", "u1", "c1")

        self.assertIn("clarify", called)

    def test_history_loaded_into_state(self):
        history = [{"role": "user", "content": "مرحبا"}]
        from app.agent.graph.graph import run_graph

        captured = []

        def _capturing_maestro(state):
            captured.append(state.get("conversation_history"))
            return _mock_maestro(state)

        with patch("app.agent.graph.graph._route_intent", side_effect=_capturing_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value="execute_node"), \
             patch("app.agent.graph.graph.execute_node", side_effect=_mock_execute), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=history), \
             patch("app.agent.graph.graph._stm_save"):
            run_graph("أهلا", "u1", "c1")

        self.assertEqual(captured[0], history)

    def test_stm_save_called_after_run(self):
        from app.agent.graph.graph import run_graph
        with patch("app.agent.graph.graph._route_intent", side_effect=_mock_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value="execute_node"), \
             patch("app.agent.graph.graph.execute_node", side_effect=_mock_execute), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save") as mock_save:
            run_graph("أضف مهمة", "u1", "c1")

        mock_save.assert_called_once()
        args = mock_save.call_args[0]
        self.assertEqual(args[0], "c1")
        self.assertEqual(args[1], "u1")
        self.assertEqual(args[2], "أضف مهمة")

    def test_exception_in_pipeline_returns_error_reply(self):
        from app.agent.graph.graph import run_graph
        with patch("app.agent.graph.graph._route_intent", side_effect=RuntimeError("boom")), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save"):
            result = run_graph("أضف مهمة", "u1", "c1")

        self.assertIn("خطأ", result.get("final_response", ""))

    def test_pending_state_passed_to_initial_state(self):
        pending = {"type": "confirmation", "nonce": "x"}
        from app.agent.graph.graph import run_graph

        captured = []

        def _cap_maestro(state):
            captured.append(state.get("pending_state"))
            return _mock_maestro(state)

        with patch("app.agent.graph.graph._route_intent", side_effect=_cap_maestro), \
             patch("app.agent.graph.graph.soul_node", side_effect=_mock_soul), \
             patch("app.agent.graph.graph.router_node", side_effect=_mock_router), \
             patch("app.agent.graph.graph.route_after_router", return_value="execute_node"), \
             patch("app.agent.graph.graph.execute_node", side_effect=_mock_execute), \
             patch("app.agent.graph.graph.response_node", side_effect=_mock_response), \
             patch("app.agent.graph.graph._stm_load", return_value=[]), \
             patch("app.agent.graph.graph._stm_save"):
            run_graph("تمام", "u1", "c1", pending_state=pending)

        self.assertEqual(captured[0], pending)


class TestGetFinalReply(unittest.TestCase):

    def test_extracts_text_and_markup(self):
        from app.agent.graph.graph import get_final_reply
        markup = {"inline_keyboard": []}
        state = create_initial_state("test", "u1", "c1")
        state = merge_state(state, {
            "final_response": "تم ✅",
            "execution_result": {"reply": "تم ✅", "reply_markup": markup, "handled": True},
        })
        reply = get_final_reply(state)
        self.assertEqual(reply["text"], "تم ✅")
        self.assertEqual(reply["reply_markup"], markup)

    def test_empty_reply_markup_when_none(self):
        from app.agent.graph.graph import get_final_reply
        state = create_initial_state("test", "u1", "c1")
        state = merge_state(state, {
            "final_response": "تم",
            "execution_result": {"reply": "تم", "reply_markup": None, "handled": True},
        })
        reply = get_final_reply(state)
        self.assertIsNone(reply["reply_markup"])


class TestSTMHelpers(unittest.TestCase):

    def test_stm_load_returns_empty_on_no_redis(self):
        from app.agent.graph.graph import _stm_load
        with patch("app.utils.redis_stm.get_redis_stm_client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.enabled = False
            mock_client.return_value = mock_instance
            result = _stm_load("c1", "u1")
        self.assertEqual(result, [])

    def test_stm_save_skips_on_no_redis(self):
        from app.agent.graph.graph import _stm_save
        with patch("app.utils.redis_stm.get_redis_stm_client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.enabled = False
            mock_client.return_value = mock_instance
            _stm_save("c1", "u1", "مرحبا", "أهلا")


if __name__ == "__main__":
    unittest.main()
