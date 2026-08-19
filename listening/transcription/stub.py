"""A provider that invents a plausible transcript without leaving the process.

Used by the entire test suite and by local development without an API key. It
is deterministic: the same audio name always yields the same segments, so tests
can assert on exact text.
"""

from .base import TranscriptResult, TranscriptSegmentData, TranscriptionProvider

#: Enough lines to exercise multi-segment behaviour, in the register the real
#: product deals with.
_LINES = [
    "Good morning, how can I help you?",
    "I would like accommodation near the university.",
    "Certainly, for how many nights?",
    "Three nights, from Friday to Sunday.",
    "That will be one hundred and twenty pounds.",
]

SEGMENT_SECONDS = 6.0
GAP_SECONDS = 0.5


class StubTranscriptionProvider(TranscriptionProvider):
    name = "stub"

    def __init__(self, *, segment_count: int = 4):
        self.segment_count = min(segment_count, len(_LINES))

    def transcribe(self, audio_file, *, language: str = "en") -> TranscriptResult:
        segments = []
        cursor = 0.0
        for index in range(self.segment_count):
            segments.append(
                TranscriptSegmentData(
                    start_time=round(cursor, 2),
                    end_time=round(cursor + SEGMENT_SECONDS, 2),
                    text=_LINES[index],
                )
            )
            cursor += SEGMENT_SECONDS + GAP_SECONDS

        duration = round(cursor - GAP_SECONDS, 2) if segments else None
        return TranscriptResult(segments=segments, duration=duration, provider=self.name)
