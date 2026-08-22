"""Unit tests for answer-key parsing. No database involved."""

import pytest

from listening.answer_key import AnswerKeyParseError, format_answer_key, parse_answer_key


class FakeAnswer:
    def __init__(self, number, text):
        self.number = number
        self.text = text


# ------------------------------------------------------------------ numbering


@pytest.mark.parametrize(
    "separator",
    [".", ")", "-", ":"],
)
def test_accepts_each_numbering_separator(separator):
    assert parse_answer_key(f"1{separator} library") == [(1, "library")]


def test_numbers_an_unnumbered_list_by_position():
    assert parse_answer_key("library\nmuseum\nblue") == [
        (1, "library"),
        (2, "museum"),
        (3, "blue"),
    ]


def test_question_numbers_need_not_start_at_one():
    """An IELTS Section 2 sheet is questions 11-20."""
    assert parse_answer_key("11. library\n12. museum") == [(11, "library"), (12, "museum")]


def test_gaps_in_numbering_are_allowed():
    assert parse_answer_key("1. a\n5. b") == [(1, "a"), (5, "b")]


def test_out_of_order_input_is_sorted():
    assert parse_answer_key("3. c\n1. a\n2. b") == [(1, "a"), (2, "b"), (3, "c")]


def test_blank_lines_and_surrounding_whitespace_are_ignored():
    assert parse_answer_key("\n  1. library  \n\n  2. museum\n\n") == [
        (1, "library"),
        (2, "museum"),
    ]


def test_empty_input_clears_the_key():
    assert parse_answer_key("") == []
    assert parse_answer_key("   \n\n  ") == []
    assert parse_answer_key(None) == []


# ------------------------------------------------- the numbering ambiguities


def test_a_time_answer_is_not_mistaken_for_a_question_number():
    """'9.30' is an answer, not question 9 answered '30'.

    This is why the separator must be followed by whitespace.
    """
    assert parse_answer_key("library\n9.30\nblue") == [
        (1, "library"),
        (2, "9.30"),
        (3, "blue"),
    ]


def test_a_decimal_answer_survives():
    assert parse_answer_key("3.5 km\n2.5 hours") == [(1, "3.5 km"), (2, "2.5 hours")]


def test_a_number_without_a_separator_is_an_answer():
    assert parse_answer_key("12 monkeys") == [(1, "12 monkeys")]


def test_a_numbered_line_may_answer_with_a_number():
    assert parse_answer_key("1. 9.30\n2. 12") == [(1, "9.30"), (2, "12")]


# ----------------------------------------------------------------- rejections


def test_rejects_a_half_numbered_list():
    """Forgetting one number is a typo, not a request to renumber."""
    with pytest.raises(AnswerKeyParseError) as exc:
        parse_answer_key("1. library\n2. museum\nblue")
    assert exc.value.extra == {"line": "blue"}


def test_rejects_a_duplicate_question_number():
    with pytest.raises(AnswerKeyParseError) as exc:
        parse_answer_key("1. library\n1. museum")
    assert exc.value.extra == {"number": 1}


def test_rejects_question_zero():
    with pytest.raises(AnswerKeyParseError):
        parse_answer_key("0. library")


def test_rejects_a_numbered_line_with_no_answer():
    with pytest.raises(AnswerKeyParseError):
        parse_answer_key("1. library\n2.   ")


def test_rejects_an_overlong_answer():
    with pytest.raises(AnswerKeyParseError):
        parse_answer_key("1. " + "x" * 501)


# ------------------------------------------------------------------ formatting


def test_format_round_trips_through_parse():
    raw = "11. library\n12. 9.30\n13. blue"
    answers = [FakeAnswer(number, text) for number, text in parse_answer_key(raw)]
    assert format_answer_key(answers) == raw
