"""OpenAI Whisper transcription.

Maps `verbose_json` responses onto :class:`TranscriptResult` and translates
the SDK's exception hierarchy into the retryable / non-retryable split the
worker depends on.
"""

import logging

from django.conf import settings

from .base import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionRejected,
    TranscriptionUnavailable,
    TranscriptResult,
    TranscriptSegmentData,
)
from pathlib import Path

logger = logging.getLogger(__name__)
MODEL = "whisper-1"

class OpenAIWhisperProvider(TranscriptionProvider):
    name = "openai-whisper"

    def __init__(self, client=None):
        # Injected in tests; built lazily otherwise so importing this module
        # never requires an API key.
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            api_key = settings.OPENAI_API_KEY
            if not api_key:
                raise TranscriptionRejected(
                    "OPENAI_API_KEY is not configured.", provider=self.name
                )
            self._client = OpenAI(api_key=api_key, timeout=settings.TRANSCRIPTION_TIMEOUT)
        return self._client

    def transcribe(self, audio_file, *, language: str = "en") -> TranscriptResult:
        self._assert_within_size_limit(audio_file)

        try:
            audio_file.open("rb")
        except (ValueError, OSError) as exc:
            raise TranscriptionRejected(
                f"The audio file could not be read: {exc}", provider=self.name
            ) from exc

        # Resolved before the try block: a configuration failure here is
        # permanent, and must not be swallowed by the translation below and
        # turned into a retryable error.
        client = self.client

        filename = Path(audio_file.name).name  # e.g. 1b34db25....mp3
        response = client.audio.transcriptions.create(
            model=MODEL,
            file=(filename, audio_file.file),
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
            language=language or None,
        )
        
        print("Transcription response: ", response)

        return self._to_result(response)

    # ------------------------------------------------------------------ size

    def _assert_within_size_limit(self, audio_file) -> None:
        """Fail before uploading rather than paying for a 413.

        OpenAI caps Whisper uploads at 25 MB, which is below this project's
        default upload limit - so this is a reachable state, not a theoretical
        one.
        """
        limit_mb = settings.TRANSCRIPTION_MAX_FILE_SIZE_MB
        size = getattr(audio_file, "size", None)
        if size is not None and size > limit_mb * 1024 * 1024:
            raise TranscriptionRejected(
                f"Audio is {size / 1024 / 1024:.1f} MB, above the {limit_mb} MB "
                f"limit for automatic transcription. Shorten the audio or add "
                f"the transcript manually.",
                provider=self.name,
            )

    # --------------------------------------------------------------- mapping

    def _to_result(self, response) -> TranscriptResult:
        raw_segments = _attr(response, "segments") or []
        all_words = self._clean_words(_attr(response, "words") or [])

        segments = []
        for raw in raw_segments:
            text = (_attr(raw, "text") or "").strip()
            start = _attr(raw, "start")
            end = _attr(raw, "end")
            if not text or start is None or end is None:
                # Whisper occasionally emits an empty or zero-length segment for
                # silence; ingest would reject it and fail the whole transcript.
                continue
            start, end = float(start), float(end)
            if end <= start:
                continue
            segments.append(
                TranscriptSegmentData(
                    start_time=round(start, 3),
                    end_time=round(end, 3),
                    text=text,
                    words=_words_within(all_words, start, end),
                )
            )

        duration = _attr(response, "duration")
        return TranscriptResult(
            segments=segments,
            duration=round(float(duration), 3) if duration else None,
            provider=self.name,
        )

    @staticmethod
    def _clean_words(raw_words) -> list[dict]:
        """Normalise the flat word list Whisper returns alongside segments."""
        words = []
        for raw in raw_words:
            word, start, end = _attr(raw, "word"), _attr(raw, "start"), _attr(raw, "end")
            if word is None or start is None or end is None:
                continue
            words.append(
                {"word": word, "start_time": float(start), "end_time": float(end)}
            )
        return words

    # ------------------------------------------------------------ exceptions

    def _translate(self, exc: Exception):
        """Turn an SDK exception into a retryable or permanent failure."""
        import openai

        # Already classified by us - keep the classification.
        if isinstance(exc, TranscriptionError):
            return exc

        transient = (
            getattr(openai, "APIConnectionError", ()),
            getattr(openai, "APITimeoutError", ()),
            getattr(openai, "RateLimitError", ()),
            getattr(openai, "InternalServerError", ()),
        )
        permanent = (
            getattr(openai, "AuthenticationError", ()),
            getattr(openai, "PermissionDeniedError", ()),
            getattr(openai, "BadRequestError", ()),
            getattr(openai, "NotFoundError", ()),
            getattr(openai, "UnprocessableEntityError", ()),
        )

        if isinstance(exc, tuple(t for t in transient if isinstance(t, type))):
            return TranscriptionUnavailable(
                f"Transcription service unavailable: {exc}", provider=self.name
            )
        if isinstance(exc, tuple(p for p in permanent if isinstance(p, type))):
            return TranscriptionRejected(
                f"Transcription request rejected: {exc}", provider=self.name
            )

        status = getattr(exc, "status_code", None)
        if status is not None and 500 <= int(status) < 600:
            return TranscriptionUnavailable(f"Provider error {status}: {exc}", provider=self.name)

        logger.exception("Unexpected transcription failure")
        return TranscriptionUnavailable(f"Unexpected transcription failure: {exc}", provider=self.name)


def _words_within(words: list[dict], start: float, end: float) -> list[dict]:
    """Words belonging to one segment.

    Whisper reports words as a single flat list rather than nested under their
    segment, so they are matched back by start time. A word is assigned to the
    segment it begins in.
    """
    return [w for w in words if start <= w["start_time"] < end]


def _attr(obj, name, default=None):
    """Read a field from an SDK model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
