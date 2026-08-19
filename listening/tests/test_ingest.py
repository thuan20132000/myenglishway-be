"""The transcription seam.

These tests define the contract a future Celery task must satisfy: call
mark_processing, run a provider, then either ingest_transcript with its output
or mark_failed with the error. Nothing here imports Celery - that is the point.
"""

import pytest

from listening.models import ExerciseStatus, TranscriptSegment
from listening.services import (
    ExerciseLocked,
    TranscriptIngestError,
    ingest_transcript,
    mark_failed,
    mark_processing,
)
from tests.factories import ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


def payload(count=3):
    return [
        {
            "start_time": index * 10.0,
            "end_time": index * 10.0 + 8.0,
            "text": f"segment {index} text here",
        }
        for index in range(count)
    ]


# ----------------------------------------------------------- status transitions


def test_mark_processing_sets_status_and_timestamp():
    exercise = ExerciseFactory(uploaded=True)
    mark_processing(exercise)

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.PROCESSING
    assert exercise.processing_started_at is not None
    assert exercise.processing_error == ""


def test_mark_processing_requires_audio():
    exercise = ExerciseFactory()  # no audio
    with pytest.raises(Exception) as excinfo:
        mark_processing(exercise)
    assert "missing_audio" in str(excinfo.value.extra["reasons"])


def test_mark_failed_records_the_reason():
    exercise = ExerciseFactory(uploaded=True)
    mark_processing(exercise)
    mark_failed(exercise, "provider timed out")

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert exercise.processing_error == "provider timed out"


def test_mark_failed_truncates_a_huge_error():
    exercise = ExerciseFactory(uploaded=True)
    mark_failed(exercise, "x" * 5000)
    exercise.refresh_from_db()
    assert len(exercise.processing_error) == 2000


def test_segments_cannot_be_edited_while_processing(auth_client, creator):
    """The pipeline owns the segment set until it finishes."""
    from django.urls import reverse

    exercise = ExerciseFactory(owner=creator, uploaded=True)
    mark_processing(exercise)

    response = auth_client(creator).post(
        reverse("v1:exercise-segments", args=[exercise.id]),
        {"start_time": 0.0, "end_time": 5.0, "text": "manual edit"},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_PROCESSING"


# -------------------------------------------------------------- happy path


def test_ingest_creates_segments_and_makes_the_exercise_ready():
    exercise = ExerciseFactory(uploaded=True)

    ingest_transcript(exercise, segments=payload(), duration=60.0)

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.duration == 60.0
    assert exercise.segments.count() == 3


def test_ingest_numbers_segments_from_one_by_start_time():
    exercise = ExerciseFactory(uploaded=True)
    unordered = [
        {"start_time": 20.0, "end_time": 28.0, "text": "third"},
        {"start_time": 0.0, "end_time": 8.0, "text": "first"},
        {"start_time": 10.0, "end_time": 18.0, "text": "second"},
    ]

    ingest_transcript(exercise, segments=unordered, duration=60.0)

    assert [(s.sequence, s.text) for s in exercise.segments.all()] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def test_ingest_computes_word_count():
    """bulk_create bypasses save(), so the derived field must be set explicitly."""
    exercise = ExerciseFactory(uploaded=True)
    ingest_transcript(
        exercise,
        segments=[{"start_time": 0.0, "end_time": 5.0, "text": "one two three four"}],
        duration=60.0,
    )
    assert exercise.segments.first().word_count == 4


def test_ingest_accepts_and_drops_word_level_timings():
    """Forward compatibility: provider word timings load without a schema change."""
    exercise = ExerciseFactory(uploaded=True)
    with_words = [
        {
            "start_time": 0.0,
            "end_time": 5.0,
            "text": "hello there",
            "words": [{"word": "hello", "start_time": 0.0, "end_time": 0.4}],
        }
    ]

    ingest_transcript(exercise, segments=with_words, duration=60.0)
    assert exercise.segments.count() == 1


def test_ingest_honours_explicit_sequences():
    exercise = ExerciseFactory(uploaded=True)
    rows = [
        {"sequence": 2, "start_time": 0.0, "end_time": 5.0, "text": "second"},
        {"sequence": 1, "start_time": 10.0, "end_time": 15.0, "text": "first"},
    ]

    ingest_transcript(exercise, segments=rows, duration=60.0)
    assert [(s.sequence, s.text) for s in exercise.segments.all()] == [
        (1, "first"),
        (2, "second"),
    ]


def test_ingest_replaces_any_existing_transcript():
    exercise = ExerciseFactory(uploaded=True)
    stale = SegmentFactory(exercise=exercise, sequence=1, text="stale text")

    ingest_transcript(exercise, segments=payload(2), duration=60.0)

    assert not TranscriptSegment.objects.filter(pk=stale.pk).exists()
    assert exercise.segments.count() == 2


def test_ingest_clears_a_previous_failure():
    exercise = ExerciseFactory(uploaded=True)
    mark_failed(exercise, "earlier failure")

    ingest_transcript(exercise, segments=payload(1), duration=60.0)

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.processing_error == ""


def test_ingest_without_duration_leaves_it_unset():
    exercise = ExerciseFactory(uploaded=True)
    ingest_transcript(exercise, segments=payload(1))
    exercise.refresh_from_db()
    assert exercise.duration is None
    assert exercise.status == ExerciseStatus.READY


# --------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "segments,reason",
    [
        ([], "empty transcript"),
        ([{"start_time": 0.0, "end_time": 5.0}], "missing text"),
        ([{"start_time": 0.0, "end_time": 5.0, "text": "   "}], "blank text"),
        ([{"end_time": 5.0, "text": "x y"}], "missing start_time"),
        ([{"start_time": 0.0, "text": "x y"}], "missing end_time"),
        ([{"start_time": "0", "end_time": 5.0, "text": "x y"}], "non-numeric start_time"),
        ([{"start_time": -1.0, "end_time": 5.0, "text": "x y"}], "negative start_time"),
        ([{"start_time": 5.0, "end_time": 5.0, "text": "x y"}], "zero-length segment"),
        ([{"start_time": 5.0, "end_time": 2.0, "text": "x y"}], "inverted range"),
        ([{"start_time": 0.0, "end_time": 5.0, "text": "x", "bogus": 1}], "unknown key"),
        (["not an object"], "non-object row"),
    ],
)
def test_invalid_payloads_are_rejected(segments, reason):
    exercise = ExerciseFactory(uploaded=True)
    with pytest.raises(TranscriptIngestError):
        ingest_transcript(exercise, segments=segments, duration=60.0)
    assert exercise.segments.count() == 0, reason


def test_segment_beyond_duration_is_rejected():
    exercise = ExerciseFactory(uploaded=True)
    rows = [{"start_time": 0.0, "end_time": 90.0, "text": "too long"}]

    with pytest.raises(TranscriptIngestError) as excinfo:
        ingest_transcript(exercise, segments=rows, duration=60.0)
    assert excinfo.value.detail.code == "SEGMENT_OUTSIDE_AUDIO"


def test_partial_sequences_are_rejected():
    exercise = ExerciseFactory(uploaded=True)
    rows = [
        {"sequence": 1, "start_time": 0.0, "end_time": 5.0, "text": "one two"},
        {"start_time": 10.0, "end_time": 15.0, "text": "three four"},
    ]
    with pytest.raises(TranscriptIngestError):
        ingest_transcript(exercise, segments=rows, duration=60.0)


def test_duplicate_sequences_are_rejected():
    exercise = ExerciseFactory(uploaded=True)
    rows = [
        {"sequence": 1, "start_time": 0.0, "end_time": 5.0, "text": "one two"},
        {"sequence": 1, "start_time": 10.0, "end_time": 15.0, "text": "three four"},
    ]
    with pytest.raises(TranscriptIngestError) as excinfo:
        ingest_transcript(exercise, segments=rows, duration=60.0)
    assert excinfo.value.detail.code == "DUPLICATE_SEGMENT_SEQUENCE"


def test_negative_duration_is_rejected():
    exercise = ExerciseFactory(uploaded=True)
    with pytest.raises(TranscriptIngestError):
        ingest_transcript(exercise, segments=payload(1), duration=-5.0)


def test_a_rejected_payload_leaves_the_existing_transcript_intact():
    """The transaction must not leave a half-replaced transcript behind."""
    exercise = ExerciseFactory(uploaded=True)
    SegmentFactory(exercise=exercise, sequence=1, text="original text")

    with pytest.raises(TranscriptIngestError):
        ingest_transcript(
            exercise,
            segments=[{"start_time": 0.0, "end_time": 5.0, "text": ""}],
            duration=60.0,
        )

    assert exercise.segments.count() == 1
    assert exercise.segments.first().text == "original text"


def test_cannot_ingest_into_a_published_exercise():
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1)

    with pytest.raises(ExerciseLocked):
        ingest_transcript(exercise, segments=payload(), duration=60.0)


# ---------------------------------------------- the full pipeline shape


def test_the_shape_a_celery_task_will_follow():
    """mark_processing -> provider -> ingest. No Celery involved."""
    exercise = ExerciseFactory(uploaded=True)

    mark_processing(exercise)
    assert exercise.status == ExerciseStatus.PROCESSING

    provider_output = {"duration": 60.0, "segments": payload(3)}
    ingest_transcript(
        exercise,
        segments=provider_output["segments"],
        duration=provider_output["duration"],
        provider="whisper",
    )

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY
    assert exercise.segments.count() == 3


def test_the_shape_when_the_provider_fails():
    exercise = ExerciseFactory(uploaded=True)
    mark_processing(exercise)

    try:
        raise RuntimeError("provider unavailable")
    except RuntimeError as exc:
        mark_failed(exercise, str(exc))

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.FAILED
    assert exercise.processing_error == "provider unavailable"
    assert exercise.segments.count() == 0
