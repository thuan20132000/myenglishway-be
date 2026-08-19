"""Answer normalization rules for dictation scoring.

Normalization decides what counts as "the same word". It neutralises the things
a dictation exercise is not testing - case, punctuation, spacing - and nothing
else. Spelling errors, missing words, extra words and wrong words all survive
normalization so the scorer can see them.

The rules are a frozen dataclass rather than module constants so tests can vary
them and a future per-exercise strictness setting can pass its own config
without mutating global state.
"""

from dataclasses import dataclass

from common.text import collapse_whitespace, tokenize as tokenize_text


@dataclass(frozen=True)
class NormalizationConfig:
    #: Fold case, so "Hello" and "hello" match.
    lowercase: bool = True
    #: Strip punctuation from word edges, so "room." matches "room".
    strip_punctuation: bool = True
    #: Collapse runs of whitespace, so spacing never affects the result.
    collapse_whitespace: bool = True
    #: Keep internal apostrophes, so "don't" stays one token distinct from "dont".
    keep_apostrophes: bool = True
    #: Treat "I'd" and "I would" as equal. Off in v1: the expansion is genuinely
    #: ambiguous ("I'd" is either "I would" or "I had") and IELTS dictation
    #: expects the contracted form the speaker actually used.
    expand_contractions: bool = False
    #: Treat "café" and "cafe" as equal. Off in v1.
    fold_accents: bool = False


DEFAULT_CONFIG = NormalizationConfig()


#: Conservative expansions used only when ``expand_contractions`` is enabled.
#: Deliberately excludes ambiguous forms ("I'd", "he's") whose expansion cannot
#: be chosen without context.
_CONTRACTIONS = {
    "i'm": "i am",
    "you're": "you are",
    "we're": "we are",
    "they're": "they are",
    "i've": "i have",
    "you've": "you have",
    "we've": "we have",
    "they've": "they have",
    "i'll": "i will",
    "you'll": "you will",
    "we'll": "we will",
    "they'll": "they will",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "can't": "cannot",
    "couldn't": "could not",
    "wouldn't": "would not",
    "shouldn't": "should not",
    "won't": "will not",
    "haven't": "have not",
    "hasn't": "has not",
    "hadn't": "had not",
}


def tokenize(text: str, config: NormalizationConfig = DEFAULT_CONFIG) -> list[str]:
    """Split text into the comparable tokens the scorer aligns."""
    tokens = tokenize_text(
        text or "",
        lowercase=config.lowercase,
        strip_punctuation=config.strip_punctuation,
        keep_apostrophes=config.keep_apostrophes,
        fold_accents=config.fold_accents,
    )

    if not config.expand_contractions:
        return tokens

    expanded: list[str] = []
    for token in tokens:
        replacement = _CONTRACTIONS.get(token.lower())
        expanded.extend(replacement.split(" ") if replacement else [token])
    return expanded


def normalize_answer(text: str, config: NormalizationConfig = DEFAULT_CONFIG) -> str:
    """Return the canonical form of an answer, for storage and comparison.

    Never corrects spelling, drops words, or reorders - two answers that differ
    only in case, punctuation or spacing normalize to the same string, and
    anything else does not.
    """
    if not text:
        return ""
    if not config.collapse_whitespace:
        return " ".join(tokenize(text, config)) if config.strip_punctuation else text
    return collapse_whitespace(" ".join(tokenize(text, config)))
