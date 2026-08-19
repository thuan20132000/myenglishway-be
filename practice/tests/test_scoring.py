"""The scoring contract, case by case.

These are the tests that pin down what a learner sees. Anything that changes a
number here changes the product, so each case states its intent explicitly.
"""

import pytest

from practice.normalization import NormalizationConfig
from practice.scoring import SCORING_VERSION, score_answer

REFERENCE = "I would like accommodation near the university."


# --------------------------------------------------------------- full marks


@pytest.mark.parametrize(
    "submitted,reason",
    [
        ("I would like accommodation near the university.", "exact match"),
        ("i would like accommodation near the university.", "capitalisation differs"),
        ("I WOULD LIKE ACCOMMODATION NEAR THE UNIVERSITY.", "all caps"),
        ("I would like accommodation near the university", "trailing period dropped"),
        ("I would like accommodation near the university?", "different end punctuation"),
        ("I would like accommodation, near the university.", "extra comma"),
        ("  I would like accommodation near the university.  ", "surrounding spaces"),
        ("I  would   like accommodation near  the university.", "repeated inner spaces"),
        ("I would like accommodation near the university!!!", "repeated punctuation"),
    ],
)
def test_noise_only_differences_score_full_marks(submitted, reason):
    result = score_answer(REFERENCE, submitted)
    assert result["score"] == 100.0, reason
    assert result["counts"]["missing"] == 0
    assert result["counts"]["extra"] == 0
    assert result["counts"]["incorrect"] == 0


# ------------------------------------------------------------------ missing


def test_one_missing_word():
    result = score_answer(REFERENCE, "I would like accommodation near university")

    assert result["score"] == 85.7  # 6 of 7
    assert result["missing"] == [{"expected": "the", "position": 5}]
    assert result["extra"] == []
    assert result["incorrect"] == []
    assert result["correct"] == [
        "i", "would", "like", "accommodation", "near", "university",
    ]


def test_several_missing_words():
    result = score_answer(REFERENCE, "I would like accommodation")
    assert result["counts"]["missing"] == 3
    assert result["score"] == pytest.approx(57.1)


def test_empty_answer_scores_zero_and_reports_every_word():
    result = score_answer(REFERENCE, "")

    assert result["score"] == 0.0
    assert result["correct"] == []
    assert result["counts"]["missing"] == 7
    assert [item["expected"] for item in result["missing"]][:3] == ["i", "would", "like"]


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", "..."])
def test_answers_with_no_words_score_zero(blank):
    assert score_answer(REFERENCE, blank)["score"] == 0.0


# -------------------------------------------------------------------- extra


def test_one_extra_word():
    result = score_answer(REFERENCE, "I would like some accommodation near the university.")

    assert result["counts"]["correct"] == 7
    assert result["extra"] == [{"received": "some", "position": 3}]
    assert result["score"] == 87.5  # 7 of 8


def test_padding_with_surplus_words_is_penalised():
    """Every reference word is present, but the answer is still not correct."""
    result = score_answer("hello", "hello there friend")

    assert result["counts"]["correct"] == 1
    assert result["counts"]["extra"] == 2
    assert result["score"] == pytest.approx(33.3)


def test_duplicated_word_counts_once_as_correct_and_once_as_extra():
    result = score_answer("the room is ready", "the the room is ready")

    assert result["counts"]["correct"] == 4
    assert result["counts"]["extra"] == 1
    assert result["score"] == 80.0


# ---------------------------------------------------------------- incorrect


def test_spelling_mistake_is_not_forgiven():
    result = score_answer(REFERENCE, "I would like acommodation near the university.")

    assert result["incorrect"] == [
        {"expected": "accommodation", "received": "acommodation", "position": 3}
    ]
    assert result["counts"]["correct"] == 6
    assert result["score"] == pytest.approx(85.7)


def test_wrong_word_is_reported_with_both_sides():
    result = score_answer(REFERENCE, "I would like accommodation near the college.")

    assert result["incorrect"] == [
        {"expected": "university", "received": "college", "position": 6}
    ]


def test_substitution_does_not_change_sequence_length():
    """A wrong word costs the correct count only - it is not charged twice."""
    result = score_answer("the room is ready", "the house is ready")
    assert result["score"] == 75.0  # 3 of 4
    assert result["counts"]["extra"] == 0
    assert result["counts"]["missing"] == 0


def test_contraction_differs_from_its_expansion_in_v1():
    result = score_answer("I'd like it", "I would like it")
    assert result["score"] < 100.0
    assert result["counts"]["correct"] == 2


def test_contraction_matches_when_expansion_is_enabled():
    config = NormalizationConfig(expand_contractions=True)
    result = score_answer("I don't know", "I do not know", config)
    assert result["score"] == 100.0


# --------------------------------------------------------------- word order


def test_swapped_word_order_is_partially_credited():
    """Documented behaviour: the moved word is one missing plus one extra."""
    result = score_answer("the cat sat", "sat the cat")

    assert result["score"] == pytest.approx(66.7)
    assert result["missing"] == [{"expected": "sat", "position": 2}]
    assert result["extra"] == [{"received": "sat", "position": 0}]


def test_full_reversal_scores_poorly():
    result = score_answer("a b c d", "d c b a")
    assert result["score"] == 25.0


# ------------------------------------------------------------------- shape


def test_result_reports_the_scoring_version():
    assert score_answer(REFERENCE, REFERENCE)["version"] == SCORING_VERSION


def test_counts_are_consistent_with_the_lists():
    result = score_answer(REFERENCE, "I would like some acommodation near university")
    counts = result["counts"]

    assert counts["correct"] == len(result["correct"])
    assert counts["missing"] == len(result["missing"])
    assert counts["extra"] == len(result["extra"])
    assert counts["incorrect"] == len(result["incorrect"])
    assert counts["expected_total"] == 7


def test_every_expected_word_is_accounted_for():
    """correct + missing + incorrect must cover the whole reference."""
    result = score_answer(REFERENCE, "I would like some acommodation near university")
    counts = result["counts"]
    assert counts["correct"] + counts["missing"] + counts["incorrect"] == counts["expected_total"]


@pytest.mark.parametrize(
    "submitted",
    [
        "",
        "I",
        REFERENCE,
        "completely unrelated words here",
        "I would like accommodation near the university and also a map",
    ],
)
def test_score_is_always_within_bounds(submitted):
    assert 0.0 <= score_answer(REFERENCE, submitted)["score"] <= 100.0


def test_scoring_is_deterministic():
    first = score_answer(REFERENCE, "I would like acommodation near university")
    second = score_answer(REFERENCE, "I would like acommodation near university")
    assert first == second


def test_positions_index_the_right_sequence():
    """missing/incorrect index the reference; extra indexes the learner's answer."""
    result = score_answer("one two three", "one nine two extra three")

    for item in result["incorrect"]:
        assert item["expected"] == "two"
        assert item["position"] == 1  # index in the reference
    assert all(item["position"] >= 0 for item in result["extra"])


def test_empty_reference_with_empty_answer():
    assert score_answer("", "")["score"] == 100.0


def test_empty_reference_with_an_answer():
    result = score_answer("", "unexpected words")
    assert result["score"] == 0.0
    assert result["counts"]["extra"] == 2


def test_scoring_needs_no_database(django_db_blocker):
    """The module is pure: importable and usable with no database access."""
    with django_db_blocker.block():
        assert score_answer("the room", "the room")["score"] == 100.0
