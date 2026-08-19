"""Text primitives shared by transcript storage and answer scoring.

These live in ``common`` rather than ``practice`` so ``listening`` can count
words without importing the practice app - the dependency direction is
listening -> common and practice -> common, never listening -> practice.

The scoring-specific rules (what counts as a mistake, how strict to be) belong
to ``practice.normalization``, which builds on top of these.
"""

import re
import unicodedata

# Punctuation stripped from token edges. Apostrophes and intra-word hyphens are
# deliberately absent: "don't" and "well-known" are single words.
_EDGE_PUNCTUATION = r"""!"#$%&()*+,./:;<=>?@[\]^_`{|}~"""

_WHITESPACE_RE = re.compile(r"\s+")


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_token(token: str, *, keep_apostrophes: bool = True) -> str:
    """Strip edge punctuation from a single token."""
    stripped = token.strip(_EDGE_PUNCTUATION)
    # Curly quotes used as apostrophes normalise to the ASCII form so
    # "don't" and "don't" compare equal.
    stripped = stripped.replace("’", "'").replace("‘", "'")
    if not keep_apostrophes:
        stripped = stripped.replace("'", "")
    else:
        stripped = stripped.strip("'")
    return stripped


def tokenize(
    text: str,
    *,
    lowercase: bool = True,
    strip_punctuation: bool = True,
    keep_apostrophes: bool = True,
    fold_accents: bool = False,
) -> list[str]:
    """Split text into comparable word tokens.

    Never corrects spelling, drops words, or reorders - only case, punctuation
    and whitespace are neutralised.
    """
    if not text:
        return []

    working = text
    if fold_accents:
        working = strip_accents(working)
    if lowercase:
        working = working.lower()

    tokens = []
    for raw in collapse_whitespace(working).split(" "):
        token = clean_token(raw, keep_apostrophes=keep_apostrophes) if strip_punctuation else raw
        if token:
            tokens.append(token)
    return tokens
