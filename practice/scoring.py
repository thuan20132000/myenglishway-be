"""Word-level dictation scoring.

A pure module: no Django imports, no database access, no knowledge of who is
answering. The API layer hands it two strings and gets back a result dict.

Algorithm - ``difflib.SequenceMatcher`` over token lists. Its ``get_opcodes()``
output is an alignment, which is what the result actually needs (which word was
expected where, and what arrived instead), rather than a bare distance number.
A weighted edit-distance would produce comparable output on segment-length
input for noticeably more code; ``scoring_version`` is recorded on every
attempt so swapping the implementation later does not invalidate history.

``autojunk`` is disabled: its heuristic ignores elements appearing in more than
1% of a long sequence, which would silently mistreat common words like "the".
"""

from difflib import SequenceMatcher

from .normalization import DEFAULT_CONFIG, NormalizationConfig, tokenize

SCORING_VERSION = "v1"


def score_answer(
    expected: str,
    submitted: str,
    config: NormalizationConfig = DEFAULT_CONFIG,
) -> dict:
    """Compare a dictation answer against the reference transcript.

    Returns a dict of::

        {
          "score":     float,   # 0..100, one decimal
          "correct":   [str],
          "missing":   [{"expected": str, "position": int}],
          "extra":     [{"received": str, "position": int}],
          "incorrect": [{"expected": str, "received": str, "position": int}],
          "counts":    {"correct", "missing", "extra", "incorrect", "expected_total"},
          "version":   str,
        }

    ``position`` is an index into the sequence the word belongs to: the expected
    tokens for ``missing`` and ``incorrect``, the submitted tokens for ``extra``.
    That is what a client needs to highlight each case in the text it is
    rendering - the reference for words that were left out, the learner's own
    answer for words they added.
    """
    expected_tokens = tokenize(expected, config)
    submitted_tokens = tokenize(submitted, config)

    correct: list[str] = []
    missing: list[dict] = []
    extra: list[dict] = []
    incorrect: list[dict] = []

    matcher = SequenceMatcher(None, expected_tokens, submitted_tokens, autojunk=False)

    for tag, exp_start, exp_end, sub_start, sub_end in matcher.get_opcodes():
        if tag == "equal":
            correct.extend(expected_tokens[exp_start:exp_end])

        elif tag == "delete":
            missing.extend(
                {"expected": expected_tokens[i], "position": i}
                for i in range(exp_start, exp_end)
            )

        elif tag == "insert":
            extra.extend(
                {"received": submitted_tokens[j], "position": j}
                for j in range(sub_start, sub_end)
            )

        elif tag == "replace":
            incorrect.extend(_pair_replacements(expected_tokens, submitted_tokens,
                                                exp_start, exp_end, sub_start, sub_end))
            # An unequal span leaves a tail on one side: words the learner
            # omitted, or words they added beyond the substitution.
            paired = min(exp_end - exp_start, sub_end - sub_start)
            missing.extend(
                {"expected": expected_tokens[i], "position": i}
                for i in range(exp_start + paired, exp_end)
            )
            extra.extend(
                {"received": submitted_tokens[j], "position": j}
                for j in range(sub_start + paired, sub_end)
            )

    expected_total = len(expected_tokens)
    return {
        "score": _score(len(correct), expected_total, len(submitted_tokens)),
        "correct": correct,
        "missing": missing,
        "extra": extra,
        "incorrect": incorrect,
        "counts": {
            "correct": len(correct),
            "missing": len(missing),
            "extra": len(extra),
            "incorrect": len(incorrect),
            "expected_total": expected_total,
        },
        "version": SCORING_VERSION,
    }


def _pair_replacements(expected_tokens, submitted_tokens, exp_start, exp_end, sub_start, sub_end):
    """Pair substituted words positionally so the client can show both sides."""
    paired = min(exp_end - exp_start, sub_end - sub_start)
    for offset in range(paired):
        yield {
            "expected": expected_tokens[exp_start + offset],
            "received": submitted_tokens[sub_start + offset],
            "position": exp_start + offset,
        }


def _score(correct_count: int, expected_total: int, submitted_total: int) -> float:
    """Correct words as a share of the longer of the two token sequences.

    Dividing by ``max(expected, submitted)`` rather than by ``expected`` alone
    is what stops padding from being free. With ``expected`` as the denominator,
    "hello there friend" scores 100 against a reference of "hello" - every
    reference word is present, and the two extras cost nothing - so a learner
    could type the transcript plus arbitrary filler and still be marked
    perfect.

    The larger denominator penalises each surplus word exactly once, and it does
    not double-charge substitutions: a wrong word leaves both sequences the same
    length, so only the lost ``correct`` count applies. Omissions are unaffected
    too, since ``expected`` is then the larger of the two.
    """
    denominator = max(expected_total, submitted_total)
    if denominator == 0:
        # Degenerate input (a blank transcript is rejected upstream). An empty
        # answer to an empty prompt is trivially complete.
        return 100.0

    return round(min(100.0, max(0.0, 100.0 * correct_count / denominator)), 1)
