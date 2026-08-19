import pytest

from practice.normalization import (
    DEFAULT_CONFIG,
    NormalizationConfig,
    normalize_answer,
    tokenize,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", ""),
        ("   ", ""),
        ("hello world", "hello world"),
        ("Hello World", "hello world"),
        ("HELLO WORLD", "hello world"),
        ("hello, world.", "hello world"),
        ("Is it ready?", "is it ready"),
        ("Wait -- what!", "wait -- what"),
        ("  leading and trailing  ", "leading and trailing"),
        ("collapse    inner     spaces", "collapse inner spaces"),
        ("line\nbreaks\ttoo", "line breaks too"),
        ("I'd like a room.", "i'd like a room"),
        ("(parenthetical) aside;", "parenthetical aside"),
    ],
)
def test_normalize_answer(text, expected):
    assert normalize_answer(text) == expected


def test_normalization_is_idempotent():
    once = normalize_answer("  Hello,   World!  ")
    assert normalize_answer(once) == once


@pytest.mark.parametrize(
    "left,right",
    [
        ("The room.", "the room"),
        ("the room", "THE ROOM"),
        ("the  room", "the room"),
        ("Is it ready?", "is it ready"),
        ("Yes, please.", "yes please"),
    ],
)
def test_answers_differing_only_in_noise_normalize_alike(left, right):
    assert normalize_answer(left) == normalize_answer(right)


@pytest.mark.parametrize(
    "left,right",
    [
        ("accommodation", "acommodation"),  # spelling
        ("the room", "room"),  # missing word
        ("the room", "the big room"),  # extra word
        ("the room", "the house"),  # wrong word
        ("don't", "dont"),  # apostrophe is meaningful
        ("i'd like", "i would like"),  # contraction, v1 default
    ],
)
def test_real_mistakes_survive_normalization(left, right):
    assert normalize_answer(left) != normalize_answer(right)


# ---------------------------------------------------------------- config


def test_config_is_immutable():
    with pytest.raises(Exception):
        DEFAULT_CONFIG.lowercase = False


def test_default_config_values():
    assert DEFAULT_CONFIG.lowercase is True
    assert DEFAULT_CONFIG.strip_punctuation is True
    assert DEFAULT_CONFIG.expand_contractions is False
    assert DEFAULT_CONFIG.fold_accents is False


def test_case_sensitive_config():
    config = NormalizationConfig(lowercase=False)
    assert normalize_answer("Hello World", config) == "Hello World"


def test_punctuation_sensitive_config():
    config = NormalizationConfig(strip_punctuation=False)
    assert "." in normalize_answer("the room.", config)


def test_contraction_expansion_when_enabled():
    config = NormalizationConfig(expand_contractions=True)
    assert tokenize("don't stop", config) == ["do", "not", "stop"]
    assert normalize_answer("I'm ready", config) == "i am ready"


def test_ambiguous_contractions_are_not_expanded():
    """"I'd" is either "I would" or "I had"; expanding it would guess."""
    config = NormalizationConfig(expand_contractions=True)
    assert tokenize("i'd", config) == ["i'd"]


def test_accent_folding_when_enabled():
    config = NormalizationConfig(fold_accents=True)
    assert normalize_answer("café", config) == "cafe"
    assert normalize_answer("café") == "café"


def test_tokenize_returns_words():
    assert tokenize("I would like a room.") == ["i", "would", "like", "a", "room"]
