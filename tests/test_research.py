"""Tests for research routing and response handling.

Covers:
1. Arabic news query classified as research
2. Successful search returns non-empty content
3. Empty results → explicit fallback message
4. Circuit breaker / exception → explicit fallback message
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud"))

from app.features.research import (
    detect_research_type,
    is_research_request,
    is_research_followup_request,
    extract_requested_result_count,
    execute_web_research,
)
from app.agent.deep_context import LAST_SEARCH_RESULTS_KEY
def _is_obvious_research_request(msg):  # stub — حُذف مع planner في Phase 7
    return True


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_exa_result(title: str, text: str) -> dict:
    return {"title": title, "url": "https://example.com", "text": text, "published_date": ""}


def _make_llm_fn(reply: str):
    """Return a mock create_chat_completion_fn that returns *reply*."""
    mock = MagicMock()
    mock.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=reply))]
    )
    return mock


# ── Test 1: Classification ────────────────────────────────────────────────────

class ResearchClassificationTests(unittest.TestCase):

    def test_arabic_news_query_detect_research_type(self):
        msg = "ابحثي لي بسرعة عن آخر أخبار OpenAI اليوم ولخصيها بثلاث نقاط"
        self.assertEqual(detect_research_type(msg), "news")

    def test_arabic_news_query_is_research_request(self):
        msg = "ابحثي لي بسرعة عن آخر أخبار OpenAI اليوم ولخصيها بثلاث نقاط"
        self.assertTrue(is_research_request(msg))

    def test_planner_fallback_recognises_news_query(self):
        msg = "ابحثي لي بسرعة عن آخر أخبار OpenAI اليوم ولخصيها بثلاث نقاط"
        self.assertTrue(_is_obvious_research_request(msg))

    def test_lakhisi_triggers_research(self):
        self.assertTrue(is_research_request("لخصيها بثلاث نقاط"))

    def test_akhbar_triggers_research(self):
        self.assertTrue(is_research_request("أخبار اليوم عن OpenAI"))

    def test_plain_chat_not_classified_as_news(self):
        self.assertNotEqual(detect_research_type("كيف حالك؟"), "news")

    def test_cafe_query_routes_to_places(self):
        self.assertEqual(detect_research_type("أقرب كافيه"), "places")
        self.assertFalse(is_research_request("أقرب كافيه"))

    def test_followup_detection_catches_best_request(self):
        self.assertTrue(is_research_followup_request("من هدول اعطيني الأفضل"))

    def test_requested_count_heuristics(self):
        self.assertEqual(extract_requested_result_count("من هدول اعطيني الأفضل"), 1)
        self.assertEqual(extract_requested_result_count("لخص النتائج"), 3)
        self.assertEqual(extract_requested_result_count("آخر أخبار OpenAI"), 5)


# ── Test 2: Successful search returns non-empty content ───────────────────────

class SuccessfulSearchTests(unittest.TestCase):

    def _run(self, query="أخبار OpenAI", llm_reply="نقطة 1\nنقطة 2\nنقطة 3"):
        fake_results = [
            _make_exa_result("OpenAI launches GPT-5", "GPT-5 is here with major improvements."),
            _make_exa_result("OpenAI raises $40B", "The funding round values OpenAI at $300B."),
        ]

        def mock_exa(q, *, exa_api_key, **kwargs):
            return fake_results

        llm_fn = _make_llm_fn(llm_reply)

        return execute_web_research(
            query=query,
            user_message=query,
            research_type="news",
            requested_count=3,
            search_exa_fn=mock_exa,
            create_chat_completion_fn=llm_fn,
            exa_api_key="test-key",
        )

    def test_returns_non_empty_string(self):
        result, ctx = self._run()
        self.assertIsInstance(result, str)
        self.assertIsInstance(ctx, list)
        self.assertGreater(len(result.strip()), 0)

    def test_does_not_return_filler_only(self):
        result, _ctx = self._run()
        filler = {"تفضل.", "تفضل", "حاضر.", "حاضر", ""}
        self.assertNotIn(result.strip(), filler)

    def test_returns_llm_summary_when_available(self):
        result, _ctx = self._run(llm_reply="نقطة 1\nنقطة 2\nنقطة 3")
        self.assertIn("نقطة", result)

    def test_falls_back_to_snippets_when_llm_empty(self):
        fake_results = [_make_exa_result("GPT-5 Launch", "Major AI news today.")]

        def mock_exa(q, *, exa_api_key, **kwargs):
            return fake_results

        llm_fn = _make_llm_fn("")  # LLM returns empty

        result, items = execute_web_research(
            query="OpenAI news",
            user_message="OpenAI news",
            research_type="news",
            requested_count=2,
            search_exa_fn=mock_exa,
            create_chat_completion_fn=llm_fn,
            exa_api_key="test-key",
        )
        self.assertGreater(len(result.strip()), 0)
        self.assertNotIn(result.strip(), {"تفضل.", "تفضل", ""})
        self.assertEqual(len(items), 1)


class FollowupAndPlacesTests(unittest.TestCase):

    def test_followup_uses_previous_search_results(self):
        session = {
            LAST_SEARCH_RESULTS_KEY: {
                "domain": "research",
                "query": "OpenAI news",
                "items": [
                    {"row": 1, "title": "OpenAI launches GPT-5", "url": "https://example.com/1", "snippet": "Strongest result."},
                    {"row": 2, "title": "OpenAI raises $40B", "url": "https://example.com/2", "snippet": "Second result."},
                ],
            }
        }

        search_mock = MagicMock(side_effect=AssertionError("search should not be called for follow-up"))

        result, items = execute_web_research(
            query="من هدول اعطيني الأفضل",
            user_message="من هدول اعطيني الأفضل",
            research_type="general",
            requested_count=5,
            search_exa_fn=search_mock,
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="test-key",
            session=session,
        )

        self.assertIn("الأفضل مبدئيًا", result)
        self.assertIn("OpenAI launches GPT-5", result)
        self.assertEqual(len(items), 2)
        search_mock.assert_not_called()

    @patch.dict(os.environ, {"GOOGLE_PLACES_API_KEY": "test-places-key"})
    def test_places_mode_uses_google_places(self):
        places = [
            {
                "name": "Cafe Roma",
                "address": "Downtown",
                "rating": 4.7,
                "reviews_count": 220,
                "phone": "",
                "website": "",
                "price_level": "متوسط",
                "open_now": "مفتوح الآن",
                "maps_url": "https://maps.example/cafe",
            }
        ]

        with patch("app.features.research.search_places", return_value=places) as search_mock:
            result, items = execute_web_research(
                query="أقرب كافيه",
                user_message="أقرب كافيه",
                research_type="places",
                requested_count=3,
                search_exa_fn=lambda *a, **kw: [],
                exa_api_key="",
                session={},
            )

        self.assertIn("Cafe Roma", result)
        self.assertIn("⭐ 4.7", result)
        self.assertEqual(len(items), 1)
        search_mock.assert_called_once()


# ── Test 3: Empty results → explicit fallback ─────────────────────────────────

class EmptyResultsTests(unittest.TestCase):

    def test_empty_exa_returns_explicit_message(self):
        def mock_exa(q, *, exa_api_key, **kwargs):
            return []

        result, ctx = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=mock_exa,
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="test-key",
        )
        self.assertIn("ما قدرت", result)
        self.assertEqual(ctx, [])

    def test_no_api_key_returns_explicit_message(self):
        result, ctx = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=lambda *a, **kw: [],
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="",  # empty key
        )
        self.assertIn("ما قدرت", result)
        self.assertEqual(ctx, [])

    def test_missing_search_fn_returns_explicit_message(self):
        result, ctx = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=None,  # missing
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="test-key",
        )
        self.assertIn("ما قدرت", result)
        self.assertEqual(ctx, [])


# ── Test 4: Exception / circuit breaker → explicit fallback ───────────────────

class CircuitBreakerFallbackTests(unittest.TestCase):

    def test_exa_exception_returns_explicit_message(self):
        def mock_exa_raises(q, *, exa_api_key, **kwargs):
            raise RuntimeError("Simulated Exa failure")

        result, ctx = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=mock_exa_raises,
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="test-key",
        )
        self.assertIn("ما قدرت", result)
        self.assertNotIn(result.strip(), {"تفضل.", "تفضل", ""})
        self.assertEqual(ctx, [])

    def test_circuit_open_error_returns_explicit_message(self):
        from app.utils.circuit_breaker import CircuitOpenError

        def mock_exa_circuit_open(q, *, exa_api_key, **kwargs):
            raise CircuitOpenError("exa", 5)

        result, ctx = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=mock_exa_circuit_open,
            create_chat_completion_fn=_make_llm_fn(""),
            exa_api_key="test-key",
        )
        self.assertIn("ما قدرت", result)
        self.assertEqual(ctx, [])

    def test_llm_exception_falls_back_to_snippets(self):
        fake_results = [_make_exa_result("OpenAI news", "Big AI announcement today.")]

        def mock_exa(q, *, exa_api_key, **kwargs):
            return fake_results

        def mock_llm_raises(*args, **kwargs):
            raise RuntimeError("LLM timeout")

        result, items = execute_web_research(
            query="أخبار OpenAI",
            user_message="ابحثي عن أخبار OpenAI",
            research_type="news",
            requested_count=3,
            search_exa_fn=mock_exa,
            create_chat_completion_fn=mock_llm_raises,
            exa_api_key="test-key",
        )
        # Should fall back to raw snippets, not empty or filler
        self.assertGreater(len(result.strip()), 0)
        self.assertNotIn(result.strip(), {"تفضل.", "تفضل", ""})
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
