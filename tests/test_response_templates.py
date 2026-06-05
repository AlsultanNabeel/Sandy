"""Tests for response_templates.py and soul_vault mood caching."""

import unittest


class TestGetResponseTemplate(unittest.TestCase):

    def setUp(self):
        from app.agent.graph.response_templates import get_response_template
        self.get = get_response_template

    def test_known_intent_standard_returns_nonempty(self):
        result = self.get("task.create", "standard")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_playful_task_create(self):
        result = self.get("task.create", "playful")
        self.assertIn("🎉", result)

    def test_formal_task_create(self):
        result = self.get("task.create", "formal")
        self.assertIn("بنجاح", result)

    def test_empathetic_task_create(self):
        result = self.get("task.create", "empathetic")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_minimal_returns_empty_for_task(self):
        result = self.get("task.create", "minimal")
        self.assertEqual(result, "")

    def test_minimal_returns_empty_for_reminder(self):
        result = self.get("reminder.create", "minimal")
        self.assertEqual(result, "")

    def test_unknown_intent_returns_empty(self):
        result = self.get("unknown.action", "standard")
        self.assertEqual(result, "")

    def test_chat_general_returns_empty(self):
        result = self.get("chat.general", "standard")
        self.assertEqual(result, "")

    def test_chat_emotional_support_returns_empty(self):
        result = self.get("chat.emotional_support", "empathetic")
        self.assertEqual(result, "")

    def test_unknown_intensity_falls_back_to_standard(self):
        result = self.get("task.create", "ultra-unknown")
        standard = self.get("task.create", "standard")
        self.assertEqual(result, standard)

    def test_reminder_create_standard(self):
        result = self.get("reminder.create", "standard")
        self.assertIn("⏰", result)

    def test_reminder_create_playful(self):
        result = self.get("reminder.create", "playful")
        self.assertIn("⏰", result)

    def test_reminder_create_empathetic(self):
        result = self.get("reminder.create", "empathetic")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_calendar_add_standard(self):
        result = self.get("calendar.add", "standard")
        self.assertIn("📅", result)

    def test_calendar_add_formal(self):
        result = self.get("calendar.add", "formal")
        self.assertIn("التقويم", result)

    def test_email_send_formal(self):
        result = self.get("email.send", "formal")
        self.assertIn("البريد الإلكتروني", result)

    def test_research_web_standard(self):
        result = self.get("research.web", "standard")
        self.assertIn("🔍", result)

    def test_task_delete_standard(self):
        result = self.get("task.delete", "standard")
        self.assertIn("✅", result)

    def test_task_complete_playful(self):
        result = self.get("task.complete", "playful")
        self.assertIn("🎊", result)

    def test_returns_string_not_none(self):
        result = self.get("task.create", "standard")
        self.assertIsNotNone(result)




class TestMaestroTemplateIntegration(unittest.TestCase):

    def test_fc_router_fills_template_when_gemini_returns_empty(self):
        import json
        from unittest.mock import patch, MagicMock
        from app.agent.graph.state import create_initial_state

        # FC format: function_call بدون response_template → fc_router يملأ من get_response_template
        mock_response = {
            "function_call": {"name": "task_create", "args": {"title": "حليب"}},
            "mood": "neutral",
            "persona_intensity": "standard",
            "persona_snippet": "خلص، سجّلتها!",
            "confidence": 0.9,
        }

        with patch("app.agent.agents.fc_router.AzureIntentClient") as MockClient:
            instance = MagicMock()
            instance._generate_with_gemini.return_value = json.dumps(mock_response)
            MockClient.return_value = instance

            from app.agent.agents.fc_router import route_with_fc
            from app.agent.tools.registry import get_registry

            state = create_initial_state("أضف مهمة حليب", "u1", "c1")
            declarations = get_registry().get_function_declarations()
            result = route_with_fc(state, declarations, agent_name="test")

        self.assertIsNotNone(result.get("response_template"))
        self.assertIsInstance(result["response_template"], str)
        self.assertGreater(len(result["response_template"]), 0)


if __name__ == "__main__":
    unittest.main()
