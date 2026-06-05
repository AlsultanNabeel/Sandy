"""Tests for execute_node — chat path + FC path (Phase 11)."""

import unittest
from unittest.mock import patch, MagicMock
from app.agent.graph.state import create_initial_state, merge_state


def _make_state(**kwargs):
    state = create_initial_state("أضف مهمة تسوق", "u1", "c1")
    return merge_state(state, kwargs)


# ── Chat intents (Legacy path — لا تزال صالحة) ───────────────────────────────

class TestExecuteNodeChatIntents(unittest.TestCase):

    def _run(self, state, llm_reply="ردّ من الـ LLM"):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = llm_reply
        mock_fn = MagicMock(return_value=mock_response)
        from app.agent.nodes.execute import execute_node
        with patch("app.agent.nodes.execute._get_chat_completion_fn", return_value=mock_fn):
            return execute_node(state)

    def test_chat_general_returns_llm_reply(self):
        state = _make_state(intent="chat.general", persona_snippet="أهلاً! 😊")
        result = self._run(state, llm_reply="كيفك؟")
        self.assertEqual(result["final_response"], "كيفك؟")

    def test_chat_emotional_returns_llm_reply(self):
        state = _make_state(intent="chat.emotional_support", persona_snippet="أنا هون معك.")
        result = self._run(state, llm_reply="أنا سامعك.")
        self.assertEqual(result["final_response"], "أنا سامعك.")

    def test_chat_proactive_returns_llm_reply(self):
        state = _make_state(intent="chat.proactive_engage", persona_snippet="وقت الراحة؟ 🌟")
        result = self._run(state, llm_reply="شو أخبارك؟")
        self.assertEqual(result["final_response"], "شو أخبارك؟")

    def test_chat_llm_failure_uses_fallback(self):
        state = _make_state(intent="chat.general", persona_snippet=None)
        mock_fn = MagicMock(side_effect=Exception("LLM error"))
        from app.agent.nodes.execute import execute_node
        with patch("app.agent.nodes.execute._get_chat_completion_fn", return_value=mock_fn):
            result = execute_node(state)
        self.assertIsNotNone(result.get("final_response"))

    def test_chat_source_is_execute_node_chat(self):
        state = _make_state(intent="chat.general", persona_snippet="مرحبا")
        result = self._run(state)
        self.assertEqual(result["execution_result"]["source"], "execute_node_chat")

    def test_chat_handled_true_when_llm_replies(self):
        state = _make_state(intent="chat.general", persona_snippet="")
        result = self._run(state, llm_reply="أهلاً")
        self.assertTrue(result["execution_result"]["handled"])


# ── FC Path ───────────────────────────────────────────────────────────────────

class TestExecuteNodeFC(unittest.TestCase):
    """function_call موجود + tool مسجل → ToolDispatcher ينفّذه."""

    def _run_fc(self, fn_name, fn_args=None, handler_result=None, tool_registered=True):
        state = _make_state(
            function_call={"name": fn_name, "args": fn_args or {}},
            intent=fn_name.replace("_", ".", 1),
        )

        mock_tool = MagicMock()
        mock_tool.tool_type = "python"

        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = mock_tool if tool_registered else None

        dispatch_result = handler_result or {"handled": True, "reply": "تم ✅"}

        with patch("app.agent.tools.registry.get_registry", return_value=mock_registry), \
             patch("app.agent.tools.dispatcher.ToolDispatcher") as MockDispatcher, \
             patch("app.agent.nodes.execute._get_chat_completion_fn"), \
             patch("app.agent.nodes.execute._get_mongo_db", return_value=None), \
             patch("app.utils.user_profiles.is_owner_chat_id", return_value=True):
            MockDispatcher.return_value.dispatch.return_value = dispatch_result
            from app.agent.nodes.execute import execute_node
            return execute_node(state)

    def test_fc_task_create_returns_reply(self):
        result = self._run_fc("task_create", {"title": "تسوق"},
                              handler_result={"handled": True, "reply": "تم إضافة المهمة ✅"})
        self.assertEqual(result["final_response"], "تم إضافة المهمة ✅")

    def test_fc_source_is_execute_node_fc(self):
        result = self._run_fc("task_create")
        self.assertEqual(result["execution_result"]["source"], "execute_node_fc")

    def test_fc_handled_true_in_execution_result(self):
        result = self._run_fc("task_list",
                              handler_result={"handled": True, "reply": "مهامك:"})
        self.assertTrue(result["execution_result"]["handled"])

    def test_fc_reply_markup_passed_through(self):
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "yes"}]]}
        result = self._run_fc("task_list",
                              handler_result={"handled": True, "reply": "مهامك:", "reply_markup": markup})
        self.assertEqual(result["execution_result"]["reply_markup"], markup)

    def test_fc_not_handled_no_final_response(self):
        result = self._run_fc("task_create",
                              handler_result={"handled": False, "reply": ""})
        self.assertIsNone(result.get("final_response"))

    def test_fc_pending_state_propagated(self):
        state = _make_state(function_call={"name": "task_delete", "args": {"reference": "1"}})
        mock_tool = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = mock_tool

        pending = {"type": "task", "action": "delete", "confirmation_status": "pending"}

        def fake_dispatch(name, args, ctx):
            ctx.session["pending_action"] = pending
            return {"handled": True, "reply": "متأكد؟"}

        with patch("app.agent.tools.registry.get_registry", return_value=mock_registry), \
             patch("app.agent.tools.dispatcher.ToolDispatcher") as MockDispatcher, \
             patch("app.agent.nodes.execute._get_chat_completion_fn"), \
             patch("app.agent.nodes.execute._get_mongo_db", return_value=None), \
             patch("app.utils.user_profiles.is_owner_chat_id", return_value=True):
            MockDispatcher.return_value.dispatch.side_effect = fake_dispatch
            from app.agent.nodes.execute import execute_node
            result = execute_node(state)

        self.assertEqual(result["pending_state"], pending)

    def test_fc_unknown_tool_falls_back_to_chat(self):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "ردّ fallback"
        result = self._run_fc("unknown_tool_xyz", tool_registered=False)
        # ما في tool → يمشي على legacy/chat path
        self.assertIsNotNone(result.get("execution_result"))

    def test_fc_meta_tool_skips_dispatcher(self):
        """chat_respond هو meta-tool — ما يروح لـ ToolDispatcher."""
        state = _make_state(
            function_call={"name": "chat_respond", "args": {"type": "general"}},
            intent="chat.general",
        )
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "مرحبا"
        mock_fn = MagicMock(return_value=mock_response)

        with patch("app.agent.nodes.execute._get_chat_completion_fn", return_value=mock_fn):
            from app.agent.nodes.execute import execute_node
            result = execute_node(state)

        # chat_respond يمشي على chat path (execute_node_chat)
        self.assertIn(result["execution_result"]["source"],
                      {"execute_node_chat", "execute_node"})

    def test_fc_dispatcher_exception_returns_error_result(self):
        state = _make_state(
            function_call={"name": "task_create", "args": {}},
            intent="task.create",
        )
        mock_tool = MagicMock()
        mock_registry = MagicMock()
        mock_registry.get_tool.return_value = mock_tool

        with patch("app.agent.tools.registry.get_registry", return_value=mock_registry), \
             patch("app.agent.tools.dispatcher.ToolDispatcher") as MockDispatcher, \
             patch("app.agent.nodes.execute._get_chat_completion_fn"), \
             patch("app.agent.nodes.execute._get_mongo_db", return_value=None), \
             patch("app.utils.user_profiles.is_owner_chat_id", return_value=True):
            MockDispatcher.return_value.dispatch.side_effect = RuntimeError("crash")
            from app.agent.nodes.execute import execute_node
            result = execute_node(state)

        self.assertFalse(result["execution_result"]["handled"])

    def test_fc_preserves_message(self):
        result = self._run_fc("task_create")
        self.assertEqual(result["message"], "أضف مهمة تسوق")

    def test_fc_image_bytes_passed_through(self):
        img = b"\x89PNG"
        result = self._run_fc("image_generate",
                              handler_result={"handled": True, "reply": "", "image_bytes": img})
        self.assertEqual(result["execution_result"]["image_bytes"], img)


# ── _build_session_from_state ─────────────────────────────────────────────────

class TestBuildSessionFromState(unittest.TestCase):

    def test_session_has_user_and_chat_id(self):
        from app.agent.nodes.execute import _build_session_from_state
        state = create_initial_state("مرحبا", "u42", "c99")
        session = _build_session_from_state(state)
        self.assertEqual(session["user_id"], "u42")
        self.assertEqual(session["chat_id"], "c99")

    def test_session_pending_action_set_when_present(self):
        from app.agent.nodes.execute import _build_session_from_state
        state = create_initial_state("تمام", "u1", "c1")
        state = merge_state(state, {"pending_state": {"type": "confirmation", "nonce": "x"}})
        session = _build_session_from_state(state)
        self.assertIsNotNone(session["pending_action"])

    def test_session_pending_action_none_when_absent(self):
        from app.agent.nodes.execute import _build_session_from_state
        state = create_initial_state("مهمة", "u1", "c1")
        session = _build_session_from_state(state)
        self.assertIsNone(session["pending_action"])


if __name__ == "__main__":
    unittest.main()
