"""Speaker-gender addressing guard.

The default speaker is the owner (male), so only an explicitly-female profile
flips Sandy to feminine address. Everything else (male, empty, no active
profile) resolves to masculine.
"""
from app.utils.user_profiles import address_instruction, set_active_user_profile


def test_female_profile_gets_feminine():
    assert "المؤنث" in address_instruction({"gender": "female"})


def test_male_profile_gets_masculine():
    assert "المذكر" in address_instruction({"gender": "male"})


def test_empty_or_missing_gender_defaults_masculine():
    assert "المذكر" in address_instruction({"gender": ""})
    assert "المذكر" in address_instruction({})


def test_no_active_profile_defaults_masculine():
    # No identified speaker → default is the owner (male).
    set_active_user_profile(None)
    assert "المذكر" in address_instruction(None)
