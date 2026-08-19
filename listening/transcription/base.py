"""Provider-independent transcription contract.

Everything above this module speaks in :class:`TranscriptResult`; only the
concrete provider modules know what an API response looks like. That boundary
is what lets the OpenAI provider be swapped for a local model, or stubbed
entirely in tests, without the task or the services changing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranscriptSegmentData:
    """One timestamped chunk of speech, as a provider reported it."""

    start_time: float
    end_time: float
    text: str
    #: Word-level timings when the provider supplies them. Carried through to
    #: the ingest payload, which currently drops them - the seam for a future
    #: TranscriptWord model.
    words: list[dict] = field(default_factory=list)

    def as_ingest_row(self) -> dict:
        row = {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "text": self.text,
        }
        if self.words:
            row["words"] = self.words
        return row


@dataclass(frozen=True)
class TranscriptResult:
    """A complete transcription run, ready to hand to ``ingest_transcript``."""

    segments: list[TranscriptSegmentData]
    duration: float | None = None
    provider: str = "unknown"

    def as_ingest_payload(self) -> list[dict]:
        """The exact shape ``listening.services.ingest_transcript`` accepts.

        Sequences are deliberately omitted: ingest numbers segments by start
        time, which is the order a listener hears them.
        """
        return [segment.as_ingest_row() for segment in self.segments]

    @property
    def is_empty(self) -> bool:
        return not self.segments


class TranscriptionError(Exception):
    """Base class for transcription failures.

    ``retryable`` is the field that matters: it decides whether the worker
    tries again or gives up and reports to the creator. Retrying a corrupt
    file forever costs money and buries the real reason.
    """

    retryable = False

    def __init__(self, message: str, *, provider: str = "unknown"):
        super().__init__(message)
        self.provider = provider


class TranscriptionUnavailable(TranscriptionError):
    """A transient failure: network, rate limit, provider 5xx."""

    retryable = True


class TranscriptionRejected(TranscriptionError):
    """The request will never succeed as-is: bad audio, too large, bad key."""

    retryable = False


class TranscriptionProvider(ABC):
    """Turns an audio file into timestamped segments."""

    #: Recorded on the exercise so it is knowable which engine produced a
    #: transcript when comparing quality or re-running later.
    name: str = "unknown"

    @abstractmethod
    def transcribe(self, audio_file, *, language: str = "en") -> TranscriptResult:
        """Transcribe an open audio file.

        Raises TranscriptionUnavailable for transient problems and
        TranscriptionRejected for permanent ones. Must not raise provider-native
        exceptions - callers depend on this two-way split.
        """
