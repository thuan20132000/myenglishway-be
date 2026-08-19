"""Provider contract tests. Nothing here touches the network."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ImproperlyConfigured

from listening.transcription import (
    OpenAIWhisperProvider,
    StubTranscriptionProvider,
    TranscriptionRejected,
    TranscriptionUnavailable,
    get_provider,
)
from listening.transcription.base import TranscriptResult, TranscriptSegmentData


def fake_audio(size=1024, name="audio.mp3"):
    audio = MagicMock()
    audio.size = size
    audio.name = name
    return audio


def whisper_response(segments=None, words=None, duration=24.0):
    return SimpleNamespace(
        duration=duration,
        segments=segments if segments is not None else [
            SimpleNamespace(start=0.0, end=5.8, text=" Good morning, how can I help you?"),
            SimpleNamespace(start=6.0, end=12.4, text=" I would like accommodation."),
        ],
        words=words or [],
    )


# --------------------------------------------------------------- result shape


def test_result_converts_to_the_ingest_payload():
    result = TranscriptResult(
        segments=[TranscriptSegmentData(start_time=0.0, end_time=5.0, text="hello there")],
        duration=5.0,
        provider="stub",
    )
    assert result.as_ingest_payload() == [
        {"start_time": 0.0, "end_time": 5.0, "text": "hello there"}
    ]


def test_ingest_payload_omits_sequence():
    """Ingest numbers segments by start time; a provider must not guess."""
    result = TranscriptResult(
        segments=[TranscriptSegmentData(start_time=0.0, end_time=5.0, text="a b")],
    )
    assert "sequence" not in result.as_ingest_payload()[0]


def test_ingest_payload_carries_word_timings_when_present():
    words = [{"word": "hello", "start_time": 0.0, "end_time": 0.4}]
    result = TranscriptResult(
        segments=[
            TranscriptSegmentData(start_time=0.0, end_time=5.0, text="hello", words=words)
        ]
    )
    assert result.as_ingest_payload()[0]["words"] == words


def test_empty_result_is_detectable():
    assert TranscriptResult(segments=[]).is_empty


# ---------------------------------------------------------------------- stub


def test_stub_returns_deterministic_segments():
    provider = StubTranscriptionProvider()
    first = provider.transcribe(fake_audio())
    second = provider.transcribe(fake_audio())

    assert [s.text for s in first.segments] == [s.text for s in second.segments]
    assert first.duration == second.duration


def test_stub_segments_are_ordered_and_non_overlapping():
    result = StubTranscriptionProvider().transcribe(fake_audio())
    for earlier, later in zip(result.segments, result.segments[1:]):
        assert earlier.end_time <= later.start_time
        assert earlier.end_time > earlier.start_time


def test_stub_payload_is_accepted_by_ingest_validation():
    """The stub must produce something ingest will actually take."""
    from listening.services import _validate_ingest_payload

    result = StubTranscriptionProvider().transcribe(fake_audio())
    cleaned = _validate_ingest_payload(result.as_ingest_payload(), result.duration)
    assert [row["sequence"] for row in cleaned] == [1, 2, 3, 4]


def test_stub_segment_count_is_configurable():
    assert len(StubTranscriptionProvider(segment_count=2).transcribe(fake_audio()).segments) == 2


# -------------------------------------------------------------------- openai


def build_provider(response=None, error=None):
    client = MagicMock()
    if error is not None:
        client.audio.transcriptions.create.side_effect = error
    else:
        client.audio.transcriptions.create.return_value = response or whisper_response()
    return OpenAIWhisperProvider(client=client), client


def test_openai_maps_segments_and_duration():
    provider, _ = build_provider()
    result = provider.transcribe(fake_audio())

    assert result.provider == "openai-whisper"
    assert result.duration == 24.0
    assert [s.text for s in result.segments] == [
        "Good morning, how can I help you?",  # leading space stripped
        "I would like accommodation.",
    ]
    assert result.segments[0].start_time == 0.0
    assert result.segments[0].end_time == 5.8


def test_openai_passes_language_and_model():
    provider, client = build_provider()
    provider.transcribe(fake_audio(), language="en")

    kwargs = client.audio.transcriptions.create.call_args.kwargs
    assert kwargs["model"] == "whisper-1"
    assert kwargs["language"] == "en"
    assert kwargs["response_format"] == "verbose_json"


def test_openai_skips_empty_and_zero_length_segments():
    """Whisper emits these for silence; ingest would reject the whole payload."""
    response = whisper_response(
        segments=[
            SimpleNamespace(start=0.0, end=5.0, text="  real speech  "),
            SimpleNamespace(start=5.0, end=6.0, text="   "),
            SimpleNamespace(start=7.0, end=7.0, text="zero length"),
            SimpleNamespace(start=9.0, end=8.0, text="inverted"),
        ]
    )
    provider, _ = build_provider(response)
    result = provider.transcribe(fake_audio())

    assert [s.text for s in result.segments] == ["real speech"]


def test_openai_assigns_words_to_the_segment_they_start_in():
    response = whisper_response(
        segments=[
            SimpleNamespace(start=0.0, end=5.0, text="first"),
            SimpleNamespace(start=6.0, end=12.0, text="second"),
        ],
        words=[
            SimpleNamespace(word="first", start=0.1, end=0.5),
            SimpleNamespace(word="second", start=6.2, end=6.9),
            SimpleNamespace(word="third", start=7.0, end=7.4),
        ],
    )
    provider, _ = build_provider(response)
    result = provider.transcribe(fake_audio())

    assert [w["word"] for w in result.segments[0].words] == ["first"]
    assert [w["word"] for w in result.segments[1].words] == ["second", "third"]


def test_openai_handles_dict_responses():
    """The SDK may return models or plain dicts depending on version."""
    response = {
        "duration": 10.0,
        "segments": [{"start": 0.0, "end": 5.0, "text": "dict shaped"}],
        "words": [],
    }
    provider, _ = build_provider(response)
    result = provider.transcribe(fake_audio())
    assert [s.text for s in result.segments] == ["dict shaped"]


def test_openai_empty_transcript_yields_empty_result():
    provider, _ = build_provider(whisper_response(segments=[], duration=3.0))
    result = provider.transcribe(fake_audio())
    assert result.is_empty


def test_openai_rejects_oversized_audio_before_calling_the_api(settings):
    settings.TRANSCRIPTION_MAX_FILE_SIZE_MB = 1
    provider, client = build_provider()

    with pytest.raises(TranscriptionRejected, match="above the 1 MB limit"):
        provider.transcribe(fake_audio(size=5 * 1024 * 1024))

    client.audio.transcriptions.create.assert_not_called()


def test_openai_closes_the_file_even_on_failure():
    audio = fake_audio()
    provider, _ = build_provider(error=RuntimeError("boom"))

    with pytest.raises(TranscriptionUnavailable):
        provider.transcribe(audio)

    audio.close.assert_called_once()


@pytest.mark.parametrize("exc_name", ["APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"])
def test_transient_sdk_errors_are_retryable(exc_name):
    import openai

    exc_class = getattr(openai, exc_name, None)
    if exc_class is None:
        pytest.skip(f"{exc_name} not in this SDK version")

    provider, _ = build_provider(error=exc_class.__new__(exc_class))
    with pytest.raises(TranscriptionUnavailable) as excinfo:
        provider.transcribe(fake_audio())
    assert excinfo.value.retryable is True


@pytest.mark.parametrize("exc_name", ["AuthenticationError", "BadRequestError", "PermissionDeniedError"])
def test_permanent_sdk_errors_are_not_retryable(exc_name):
    import openai

    exc_class = getattr(openai, exc_name, None)
    if exc_class is None:
        pytest.skip(f"{exc_name} not in this SDK version")

    provider, _ = build_provider(error=exc_class.__new__(exc_class))
    with pytest.raises(TranscriptionRejected) as excinfo:
        provider.transcribe(fake_audio())
    assert excinfo.value.retryable is False


def test_unreadable_audio_is_rejected():
    audio = fake_audio()
    audio.open.side_effect = OSError("file is gone")
    provider, _ = build_provider()

    with pytest.raises(TranscriptionRejected, match="could not be read"):
        provider.transcribe(audio)


def test_missing_api_key_is_rejected(settings):
    settings.OPENAI_API_KEY = ""
    with pytest.raises(TranscriptionRejected, match="OPENAI_API_KEY"):
        OpenAIWhisperProvider().transcribe(fake_audio())


# ------------------------------------------------------------------ registry


def test_get_provider_resolves_from_settings(settings):
    settings.TRANSCRIPTION_PROVIDER = "stub"
    assert isinstance(get_provider(), StubTranscriptionProvider)


def test_get_provider_accepts_an_explicit_name():
    assert isinstance(get_provider("openai"), OpenAIWhisperProvider)


def test_unknown_provider_is_a_configuration_error(settings):
    settings.TRANSCRIPTION_PROVIDER = "nonsense"
    with pytest.raises(ImproperlyConfigured, match="Unknown TRANSCRIPTION_PROVIDER"):
        get_provider()
