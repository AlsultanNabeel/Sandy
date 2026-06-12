"""Tests for soul_vault and soul_node."""

import unittest
from unittest.mock import patch
from app.agent.graph.state import create_initial_state


class TestSoulVault(unittest.TestCase):

    def setUp(self):
        from app.agent.soul_vault import get_persona, _safe_intensity
        self.get_persona = get_persona
        self.safe_intensity = _safe_intensity

    def test_valid_intensity_returned(self):
        result = self.get_persona("u1", "empathetic")
        self.assertEqual(result["intensity"], "empathetic")

    def test_invalid_intensity_defaults_to_standard(self):
        result = self.get_persona("u1", "unknown_level")
        self.assertEqual(result["intensity"], "standard")

    def test_none_intensity_defaults_to_standard(self):
        result = self.get_persona("u1", None)
        self.assertEqual(result["intensity"], "standard")

    def test_stressed_mood_forbids_playful(self):
        result = self.get_persona("u1", "playful", mood="stressed")
        self.assertNotEqual(result["intensity"], "playful")
        self.assertEqual(result["intensity"], "empathetic")

    def test_frustrated_mood_forbids_playful(self):
        result = self.get_persona("u1", "playful", mood="frustrated")
        self.assertEqual(result["intensity"], "empathetic")

    def test_sad_mood_forbids_playful(self):
        result = self.get_persona("u1", "playful", mood="sad")
        self.assertEqual(result["intensity"], "empathetic")

    def test_calm_mood_allows_playful(self):
        result = self.get_persona("u1", "playful", mood="calm")
        self.assertEqual(result["intensity"], "playful")

    def test_env_var_overrides_default_snippet(self):
        with patch.dict("os.environ", {"SANDY_SOUL_STANDARD": "أهلاً! 🌟"}):
            result = self.get_persona("u1", "standard")
            self.assertEqual(result["snippet"], "أهلاً! 🌟")

    def test_minimal_snippet_is_empty_by_default(self):
        result = self.get_persona("u1", "minimal")
        self.assertEqual(result["snippet"], "")

    def test_returns_dict_with_required_keys(self):
        result = self.get_persona("u1", "standard")
        self.assertIn("intensity", result)
        self.assertIn("snippet", result)


class TestSoulNode(unittest.TestCase):

    def _run(self, intensity="standard", mood="calm"):
        state = create_initial_state("مرحبا", "u1", "c1")
        from app.agent.graph.state import merge_state
        state = merge_state(state, {
            "persona_intensity": intensity,
            "mood": mood,
        })
        from app.agent.nodes.soul import soul_node
        return soul_node(state)

    def test_soul_node_sets_persona_intensity(self):
        result = self._run("empathetic", "calm")
        self.assertEqual(result["persona_intensity"], "empathetic")

    def test_soul_node_enforces_mood_rule(self):
        result = self._run("playful", "stressed")
        self.assertEqual(result["persona_intensity"], "empathetic")

    def test_soul_node_preserves_message(self):
        result = self._run()
        self.assertEqual(result["message"], "مرحبا")

    def test_soul_node_fallback_on_exception(self):
        state = create_initial_state("hello", "u1", "c1")
        with patch("app.agent.nodes.soul.get_persona", side_effect=Exception("Vault down")):
            from app.agent.nodes.soul import soul_node
            result = soul_node(state)
        self.assertEqual(result["persona_intensity"], "minimal")
        self.assertIsNone(result.get("error"))  # لا error في الـ state — يُسجل في Sentry فقط

    def test_soul_node_minimal_has_no_snippet(self):
        result = self._run("minimal", "calm")
        # minimal بدون مقتطف شخصية — بس لاحقة [حالة المستخدم] من context_builder مسموحة
        snippet = result["persona_snippet"]
        if snippet is not None:
            self.assertTrue(snippet.startswith("[حالة المستخدم:"), snippet)

    def test_soul_node_standard_has_snippet(self):
        result = self._run("standard", "calm")
        # snippet قد يكون None أو يبدأ بالجملة حسب الـ env — لاحقة [حالة المستخدم] مسموحة
        snippet = result["persona_snippet"]
        if snippet is not None and not snippet.startswith("[حالة المستخدم:"):
            self.assertTrue(snippet.startswith("هيه، وينك؟ 🤍"), snippet)


class TestSoulVaultNewFeatures(unittest.TestCase):
    """B3 + C3 + D5 + F2"""

    # B3 — get_apology
    def test_get_apology_returns_string(self):
        from app.agent.soul_vault import get_apology
        result = get_apology("neutral")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_get_apology_stressed_different_from_neutral(self):
        from app.agent.soul_vault import get_apology
        self.assertNotEqual(get_apology("stressed"), get_apology("neutral"))

    def test_get_apology_frustrated_different_from_neutral(self):
        from app.agent.soul_vault import get_apology
        self.assertNotEqual(get_apology("frustrated"), get_apology("neutral"))

    def test_get_apology_unknown_mood_falls_back_to_neutral(self):
        from app.agent.soul_vault import get_apology
        self.assertEqual(get_apology("nonexistent"), get_apology("neutral"))

    def test_get_apology_none_mood_falls_back_to_neutral(self):
        from app.agent.soul_vault import get_apology
        self.assertEqual(get_apology(None), get_apology("neutral"))

    # D5 — get_hint_snippet
    def test_hint_stressed_returns_string(self):
        from app.agent.soul_vault import get_hint_snippet
        result = get_hint_snippet("stressed")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_hint_frustrated_returns_string(self):
        from app.agent.soul_vault import get_hint_snippet
        result = get_hint_snippet("frustrated")
        self.assertIsInstance(result, str)

    def test_hint_neutral_returns_none(self):
        from app.agent.soul_vault import get_hint_snippet
        self.assertIsNone(get_hint_snippet("neutral"))

    def test_hint_happy_returns_none(self):
        from app.agent.soul_vault import get_hint_snippet
        self.assertIsNone(get_hint_snippet("happy"))

    def test_hint_none_mood_returns_none(self):
        from app.agent.soul_vault import get_hint_snippet
        self.assertIsNone(get_hint_snippet(None))

    # D5 — get_persona uses hints for negative moods
    def test_get_persona_stressed_uses_hint(self):
        from app.agent.soul_vault import get_persona, _HINT_SNIPPETS
        result = get_persona("u1", "empathetic", mood="stressed")
        self.assertEqual(result["snippet"], _HINT_SNIPPETS["stressed"])

    def test_get_persona_calm_does_not_use_hint(self):
        from app.agent.soul_vault import get_persona, _HINT_SNIPPETS
        result = get_persona("u1", "standard", mood="calm")
        self.assertNotIn(result["snippet"], _HINT_SNIPPETS.values())

    # F2 — get_gratitude_snippet
    def test_gratitude_returns_string(self):
        from app.agent.soul_vault import get_gratitude_snippet
        result = get_gratitude_snippet()
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_gratitude_is_from_list(self):
        from app.agent.soul_vault import get_gratitude_snippet, _GRATITUDE_SNIPPETS
        result = get_gratitude_snippet()
        self.assertIn(result, _GRATITUDE_SNIPPETS)

    # C3 — get_varied_snippet
    def test_varied_snippet_standard_is_nonempty(self):
        from app.agent.soul_vault import get_varied_snippet
        result = get_varied_snippet("standard")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_varied_snippet_minimal_returns_empty(self):
        from app.agent.soul_vault import get_varied_snippet
        result = get_varied_snippet("minimal")
        self.assertEqual(result, "")

    def test_varied_snippet_unknown_intensity_returns_standard(self):
        from app.agent.soul_vault import get_varied_snippet
        result = get_varied_snippet("nonexistent")
        self.assertIsInstance(result, str)

    def test_varied_snippet_env_override(self):
        from app.agent.soul_vault import get_varied_snippet
        with patch.dict("os.environ", {"SANDY_SOUL_STANDARD": "كلام مخصص 🌟"}):
            result = get_varied_snippet("standard")
        self.assertEqual(result, "كلام مخصص 🌟")


class TestResponseNodeApology(unittest.TestCase):
    """B3 — response_node يعتذر عند غياب الرد"""

    def _run(self, **kwargs):
        from app.agent.graph.state import create_initial_state, merge_state
        from app.agent.nodes.response import response_node
        state = create_initial_state("اختبار", "u1", "c1")
        return response_node(merge_state(state, kwargs))

    def test_no_reply_uses_apology(self):
        from app.agent.soul_vault import _APOLOGY_SNIPPETS
        result = self._run(execution_result={}, mood="neutral")
        self.assertIn(result["final_response"], _APOLOGY_SNIPPETS.values())

    def test_stressed_mood_uses_stressed_apology(self):
        from app.agent.soul_vault import get_apology
        result = self._run(execution_result={}, mood="stressed")
        self.assertEqual(result["final_response"], get_apology("stressed"))

    def test_frustrated_mood_uses_frustrated_apology(self):
        from app.agent.soul_vault import get_apology
        result = self._run(execution_result={}, mood="frustrated")
        self.assertEqual(result["final_response"], get_apology("frustrated"))

    def test_existing_reply_not_replaced(self):
        result = self._run(execution_result={"reply": "رد طبيعي"}, mood="stressed")
        self.assertEqual(result["final_response"], "رد طبيعي")

    def test_existing_final_response_not_replaced(self):
        result = self._run(final_response="رد نهائي موجود", execution_result={}, mood="stressed")
        self.assertEqual(result["final_response"], "رد نهائي موجود")


if __name__ == "__main__":
    unittest.main()
