"""Pure-logic guards for research-intent detection and task-name matching.

No Mongo, no network — both are pure string functions that sit on hot paths:
is_research_request decides whether a message fires an Exa search, and
_task_match_key is the normaliser every task lookup compares against.
"""
from app.features.research_intent import is_research_request
from app.features.tasks_matcher import _task_match_key


# ── research intent ──────────────────────────────────────────────────────────
def test_strong_trigger_fires_research():
    assert is_research_request("ابحث عن أفضل لابتوب") is True
    assert is_research_request("آخر أخبار التكنولوجيا") is True


def test_casual_question_does_not_fire():
    # Generic question words alone (short chat) must NOT trigger a search.
    assert is_research_request("كيف حالك") is False
    assert is_research_request("ما هي") is False


def test_substantial_question_fires():
    # A real question (4+ words) with a generic opener is research.
    assert is_research_request("ما هو أفضل لابتوب للبرمجة") is True


def test_plain_chat_does_not_fire():
    assert is_research_request("تمام شكرا الك") is False


# ── task match key (normalisation) ───────────────────────────────────────────
def test_match_key_normalises_alef_variants():
    assert _task_match_key("أحمد") == _task_match_key("احمد")
    assert _task_match_key("إشترِ") == _task_match_key("اشتر")


def test_match_key_normalises_ya_and_ta_marbuta():
    assert _task_match_key("اشترى") == _task_match_key("اشتري")
    assert _task_match_key("مدرسة") == _task_match_key("مدرسه")


def test_match_key_maps_arabic_digits_and_strips_diacritics():
    assert "5" in _task_match_key("مهمة ٥")
    assert _task_match_key("مُهِمّ") == _task_match_key("مهم")
