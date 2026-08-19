"""The background transcription task.

Runs inline (CELERY_TASK_ALWAYS_EAGER) against the stub provider, so no broker
and no network are involved.
"""

from unittest.mock import patch

import pytest

from listening.models import ExerciseStatus, TranscriptSegment
from listening.tasks import transcribe_exercise
from listening.transcription import (
    TranscriptResult,
    TranscriptSegmentData,
    TranscriptionRejected,
    TranscriptionUnavailable,
)
from tests.factories import ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


def run(exercise, audio_name=None):
    """Invoke the task the way Celery would, inline."""
    return transcribe_exercise.apply(
        args=[exercise.pk, audio_name or exercise.audio_file.name]
    ).get()


# ----------------------------------------------------------------- happy path


def test_transcription_produces_segments_and_marks_ready():
    exercise = ExerciseFactory(uploaded=True)

    assert run(exercise) == "ready"

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.segments.count() == 4
    assert exercise.duration is not None
    assert exercise.processing_error == ""


def test_segments_are_numbered_and_carry_word_counts():
    exercise = ExerciseFactory(uploaded=True)
    run(exercise)

    segments = list(exercise.segments.all())
    assert [s.sequence for s in segments] == [1, 2, 3, 4]
    assert all(s.word_count > 0 for s in segments)
    assert segments[0].text == "Good morning, how can I help you?"


def test_provider_name_is_recorded():
    exercise = ExerciseFactory(uploaded=True)
    run(exercise)
    exercise.refresh_from_db()
    assert exercise.transcription_provider == "stub"


def test_language_is_passed_to_the_provider():
    exercise = ExerciseFactory(uploaded=True, language="en")

    with patch("listening.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.transcribe.return_value = TranscriptResult(
            segments=[TranscriptSegmentData(start_time=0.0, end_time=5.0, text="a b c")],
            duration=5.0,
            provider="stub",
        )
        run(exercise)

    assert provider.transcribe.call_args.kwargs["language"] == "en"


def test_rerunning_replaces_the_previous_transcript():
    exercise = ExerciseFactory(uploaded=True)
    stale = SegmentFactory(exercise=exercise, sequence=1, text="stale text")

    run(exercise)

    assert not TranscriptSegment.objects.filter(pk=stale.pk).exists()
    assert exercise.segments.count() == 4


# --------------------------------------------------------------------- guards


def test_missing_exercise_is_ignored():
    assert transcribe_exercise.apply(args=[999999, "audio.mp3"]).get() == "missing"


def test_exercise_without_audio_fails_cleanly():
    exercise = ExerciseFactory()  # no audio
    assert transcribe_exercise.apply(args=[exercise.pk, "audio.mp3"]).get() == "no-audio"

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert "no audio" in exercise.processing_error


def test_a_superseded_run_does_not_touch_the_exercise():
    """Audio replaced after queueing: the stale task must abort."""
    exercise = ExerciseFactory(uploaded=True)

    result = transcribe_exercise.apply(args=[exercise.pk, "audio/old-file.mp3"]).get()

    assert result == "superseded"
    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.UPLOADED
    assert exercise.segments.count() == 0


def test_a_superseded_run_leaves_a_newer_transcript_intact():
    exercise = ExerciseFactory(uploaded=True)
    run(exercise)  # the current audio is transcribed
    exercise.refresh_from_db()
    assert exercise.segments.count() == 4

    # An older task for a previous file now fires late.
    transcribe_exercise.apply(args=[exercise.pk, "audio/previous.mp3"]).get()

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.segments.count() == 4


def test_audio_name_is_optional():
    """Called without a name (e.g. a manual re-run), the guard is skipped."""
    exercise = ExerciseFactory(uploaded=True)
    assert transcribe_exercise.apply(args=[exercise.pk]).get() == "ready"


# -------------------------------------------------------------------- failure


def test_empty_transcript_fails_with_an_explanation():
    exercise = ExerciseFactory(uploaded=True)

    with patch("listening.tasks.get_provider") as get_provider:
        get_provider.return_value.transcribe.return_value = TranscriptResult(
            segments=[], duration=10.0, provider="stub"
        )
        assert run(exercise) == "empty"

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert "no speech" in exercise.processing_error
    assert exercise.segments.count() == 0


def test_permanent_provider_error_fails_without_retrying():
    exercise = ExerciseFactory(uploaded=True)

    with patch("listening.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.transcribe.side_effect = TranscriptionRejected("audio is corrupt")
        assert run(exercise) == "failed"
        assert provider.transcribe.call_count == 1  # no retry

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert "corrupt" in exercise.processing_error


def test_transient_error_retries_then_gives_up(settings):
    settings.TRANSCRIPTION_RETRY_DELAY = 0
    exercise = ExerciseFactory(uploaded=True)

    with patch("listening.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.transcribe.side_effect = TranscriptionUnavailable("service down")
        transcribe_exercise.apply(args=[exercise.pk, exercise.audio_file.name]).get()

        # Initial attempt plus the configured retries.
        assert provider.transcribe.call_count == settings.TRANSCRIPTION_MAX_RETRIES + 1

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert "service down" in exercise.processing_error


def test_transient_error_that_later_succeeds_ends_ready(settings):
    settings.TRANSCRIPTION_RETRY_DELAY = 0
    exercise = ExerciseFactory(uploaded=True)
    good = TranscriptResult(
        segments=[TranscriptSegmentData(start_time=0.0, end_time=5.0, text="one two three")],
        duration=5.0,
        provider="stub",
    )

    with patch("listening.tasks.get_provider") as get_provider:
        get_provider.return_value.transcribe.side_effect = [
            TranscriptionUnavailable("blip"),
            good,
        ]
        transcribe_exercise.apply(args=[exercise.pk, exercise.audio_file.name]).get()

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.segments.count() == 1


def test_an_invalid_payload_fails_without_retrying():
    """A malformed transcript is the provider's fault, not the network's."""
    exercise = ExerciseFactory(uploaded=True)

    with patch("listening.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.transcribe.return_value = TranscriptResult(
            # end_time before start_time: ingest rejects it.
            segments=[TranscriptSegmentData(start_time=10.0, end_time=2.0, text="bad")],
            duration=30.0,
            provider="stub",
        )
        assert run(exercise) == "rejected"
        assert provider.transcribe.call_count == 1

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert "could not be stored" in exercise.processing_error
    assert exercise.segments.count() == 0


def test_failure_leaves_a_previous_transcript_untouched():
    exercise = ExerciseFactory(uploaded=True)
    run(exercise)
    exercise.refresh_from_db()

    with patch("listening.tasks.get_provider") as get_provider:
        get_provider.return_value.transcribe.side_effect = TranscriptionRejected("nope")
        transcribe_exercise.apply(args=[exercise.pk]).get()

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    # The old segments survive: a failed re-run must not destroy good work.
    assert exercise.segments.count() == 4


def test_published_exercise_is_left_alone():
    """Replacing a live transcript under learners is never right.

    It is also impossible at the database level: `published => ready` means
    moving to PROCESSING would raise IntegrityError, so the task refuses before
    changing any state.
    """
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, text="live transcript")

    assert transcribe_exercise.apply(args=[exercise.pk]).get() == "published"

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.is_published is True
    assert exercise.segments.count() == 1
    assert exercise.segments.first().text == "live transcript"


def test_mark_processing_refuses_a_published_exercise():
    """The guard lives in the service too, protecting any future caller."""
    from listening.services import ExerciseLocked, mark_processing

    exercise = ExerciseFactory(published=True)
    with pytest.raises(ExerciseLocked):
        mark_processing(exercise)
