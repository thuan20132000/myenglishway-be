"""Parsing for creator-pasted answer keys.

An answer key arrives the way it is printed - one answer per line, usually
numbered:

    1. library
    2) 9.30
    3 - blue

Kept separate from the models and serializers because the interesting rules
here are textual, and this way they can be tested without a database.
"""

import re

from common import errors

#: "1. answer", "1) answer", "1 - answer", "1: answer".
#:
#: Both the separator and the space after it are required. Without the
#: separator, "12 monkeys" would parse as question 12; without the mandatory
#: space, the unnumbered answer "9.30" - an ordinary IELTS time - would parse
#: as question 9 answered "30".
_NUMBERED_LINE = re.compile(r"^(\d+)\s*[.)\-:]\s+(.+)$")

MAX_ANSWER_LENGTH = 500


class AnswerKeyParseError(errors.DomainError):
    default_code = errors.INVALID_ANSWER_KEY
    default_detail = "The answer key could not be read."


def parse_answer_key(raw: str) -> list[tuple[int, str]]:
    """Turn pasted text into ordered ``(number, answer)`` pairs.

    Blank lines are ignored. Numbering is all-or-none - the same rule the
    ``load_transcript`` command applies to segment sequences - because a
    half-numbered list is a typo rather than an intent. An unnumbered list is
    numbered from 1 by position.

    Returns an empty list for empty input, which callers treat as "clear the
    key".
    """
    lines = [line.strip() for line in (raw or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []

    matches = [_NUMBERED_LINE.match(line) for line in lines]
    numbered = [match for match in matches if match]

    if numbered and len(numbered) != len(lines):
        unnumbered = next(line for line, m in zip(lines, matches, strict=True) if not m)
        raise AnswerKeyParseError(
            "Number every line or none of them. This line has no number: "
            f"{unnumbered!r}.",
            extra={"line": unnumbered},
        )

    if numbered:
        pairs = [(int(m.group(1)), m.group(2).strip()) for m in matches]
    else:
        pairs = list(enumerate(lines, start=1))

    _validate(pairs)
    return sorted(pairs)


def _validate(pairs: list[tuple[int, str]]) -> None:
    seen: set[int] = set()
    for number, text in pairs:
        # Question numbers are not required to start at 1 or to be contiguous:
        # an IELTS Section 2 sheet is questions 11-20.
        if number < 1:
            raise AnswerKeyParseError(
                f"Question numbers start at 1, got {number}.",
                extra={"number": number},
            )
        if number in seen:
            raise AnswerKeyParseError(
                f"Question {number} is listed more than once.",
                extra={"number": number},
            )
        seen.add(number)

        if not text:
            raise AnswerKeyParseError(
                f"Question {number} has no answer.",
                extra={"number": number},
            )
        if len(text) > MAX_ANSWER_LENGTH:
            raise AnswerKeyParseError(
                f"The answer to question {number} is longer than "
                f"{MAX_ANSWER_LENGTH} characters.",
                extra={"number": number},
            )


def format_answer_key(answers) -> str:
    """Render stored answers back into the pasteable form.

    So an edit form can load the key as text rather than making the client
    reassemble it.
    """
    return "\n".join(f"{answer.number}. {answer.text}" for answer in answers)
