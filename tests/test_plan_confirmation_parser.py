"""Structural CONFIRM/CANCEL/NONE parser — fail closed (PR4)."""
from app.services.coach_plan_policy import (
    CANCEL,
    CONFIRM,
    NONE,
    parse_confirmation_intent,
)


def test_bounded_confirm_forms():
    for message in ("yes", "Yes", "YES", "confirm", "do it",
                    "yeah", "yep", "confirmed", "proceed", "go ahead",
                    "evet", "Evet", "onayla", "uygula", "  evet  ",
                    "olur", "kaydet", "tabii"):
        assert parse_confirmation_intent(message) == CONFIRM, message


def test_bounded_cancel_forms():
    for message in ("no", "cancel", "don't", "dont", "do not",
                    "never mind", "nevermind",
                    "hayır", "hayir", "iptal", "vazgeç", "vazgec"):
        assert parse_confirmation_intent(message) == CANCEL, message


def test_mixed_intent_is_not_confirmation():
    for message in (
        "yes but make it 8 reps",
        "yes, and remove squats",
        "do it, and also remove squats",
        "evet ama 8 tekrar yap",
        "onayla ve squat'ı çıkar",
        "maybe yes",
        "I guess yes",
        "do not confirm",
        "onaylama",
        "uygulama",
        "olur mu?",
        "tamam ama...",
        "cancel? no",
        "hayır, uygula",
    ):
        assert parse_confirmation_intent(message) == NONE, message


def test_hedges_and_questions_are_not_confirmation():
    for message in (
        "I guess maybe",
        "yes? I'm not sure",
        "yes?",
        "maybe",
        "olur mu",
        "",
        None,
        123,
    ):
        assert parse_confirmation_intent(message) == NONE, message


def test_negation_never_classifies_as_confirm():
    for message in (
        "not yes",
        "don't do it",
        "dont do it",
        "do not",
        "hayır",
        "don't",
        "no",
    ):
        intent = parse_confirmation_intent(message)
        assert intent != CONFIRM, message


def test_cancel_forms_that_contain_negation_are_still_cancel():
    assert parse_confirmation_intent("don't") == CANCEL
    assert parse_confirmation_intent("no") == CANCEL
    assert parse_confirmation_intent("hayır") == CANCEL


def test_trailing_punctuation_on_an_exact_form_is_still_confirm():
    assert parse_confirmation_intent("yes.") == CONFIRM
    assert parse_confirmation_intent("evet!") == CONFIRM
