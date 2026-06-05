import unittest

from app.agent.deep_context import wants_comparison_grounded_in_search


class ComparisonHintTests(unittest.TestCase):

    def test_arabic_triggers(self):
        self.assertTrue(wants_comparison_grounded_in_search("شو أحسن واحد؟"))
        self.assertTrue(wants_comparison_grounded_in_search("اي أقرب؟"))
        self.assertFalse(wants_comparison_grounded_in_search("كم الساعة؟"))


if __name__ == "__main__":
    unittest.main()
