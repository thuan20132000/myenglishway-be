"""Transcription providers.

``get_provider()`` is the only entry point the rest of the codebase uses, so
switching engines is a settings change rather than a code change.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import (
    TranscriptionError,
    TranscriptionProvider,
    TranscriptionRejected,
    TranscriptionUnavailable,
    TranscriptResult,
    TranscriptSegmentData,
)
from .openai_whisper import OpenAIWhisperProvider
from .stub import StubTranscriptionProvider

__all__ = [
    "OpenAIWhisperProvider",
    "StubTranscriptionProvider",
    "TranscriptResult",
    "TranscriptSegmentData",
    "TranscriptionError",
    "TranscriptionProvider",
    "TranscriptionRejected",
    "TranscriptionUnavailable",
    "get_provider",
]

_PROVIDERS = {
    "openai": OpenAIWhisperProvider,
    "stub": StubTranscriptionProvider,
}


def get_provider(name: str | None = None) -> TranscriptionProvider:
    """Resolve the configured provider.

    Raises ImproperlyConfigured rather than falling back to a default: silently
    transcribing with the stub in production would fill exercises with
    convincing nonsense.
    """
    name = name or settings.TRANSCRIPTION_PROVIDER
    try:
        return _PROVIDERS[name]()
    except KeyError:
        raise ImproperlyConfigured(
            f"Unknown TRANSCRIPTION_PROVIDER {name!r}. "
            f"Choose one of: {', '.join(sorted(_PROVIDERS))}."
        ) from None
