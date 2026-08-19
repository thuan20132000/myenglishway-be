import pytest

from common.text import collapse_whitespace, strip_accents, tokenize


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", []),
        ("   ", []),
        ("Hello world", ["hello", "world"]),
        ("Hello,   world!", ["hello", "world"]),
        ("  leading and trailing  ", ["leading", "and", "trailing"]),
        ("Is it ready?", ["is", "it", "ready"]),
        ("I'd like a room.", ["i'd", "like", "a", "room"]),
        ("well-known co-op", ["well-known", "co-op"]),
        ("I’d like", ["i'd", "like"]),
        ("A (parenthetical) aside;", ["a", "parenthetical", "aside"]),
        ("MIXED Case Words", ["mixed", "case", "words"]),
    ],
)
def test_tokenize(text, expected):
    assert tokenize(text) == expected


def test_tokenize_preserves_spelling_errors():
    assert tokenize("acommodation") == ["acommodation"]


def test_tokenize_preserves_duplicate_words():
    assert tokenize("the the room") == ["the", "the", "room"]


def test_tokenize_can_drop_apostrophes():
    assert tokenize("don't", keep_apostrophes=False) == ["dont"]


def test_tokenize_can_preserve_case():
    assert tokenize("Hello World", lowercase=False) == ["Hello", "World"]


def test_tokenize_can_fold_accents():
    assert tokenize("café", fold_accents=True) == ["cafe"]
    assert tokenize("café") == ["café"]


def test_collapse_whitespace():
    assert collapse_whitespace("  a \n b\tc  ") == "a b c"


def test_strip_accents():
    assert strip_accents("résumé") == "resume"
