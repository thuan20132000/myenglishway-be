import pytest
from django.db import IntegrityError, connection, transaction

from listening.models import ExerciseStatus, ListeningExercise, TranscriptSegment
from tests.factories import ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- exercise


def test_exercise_defaults_to_draft():
    exercise = ExerciseFactory()
    assert exercise.status == ExerciseStatus.DRAFT
    assert exercise.is_published is False
    assert exercise.has_audio is False
    assert exercise.duration is None


def test_exercise_default_ordering_is_newest_first():
    first = ExerciseFactory()
    second = ExerciseFactory()
    assert list(ListeningExercise.objects.all()) == [second, first]


def test_exercise_duration_must_be_positive():
    with pytest.raises(IntegrityError):
        ExerciseFactory(duration=0)


def test_exercise_duration_may_be_null():
    exercise = ExerciseFactory(duration=None)
    assert exercise.duration is None


def test_publishing_requires_ready_status():
    with pytest.raises(IntegrityError):
        ExerciseFactory(status=ExerciseStatus.UPLOADED, is_published=True)


def test_published_ready_exercise_is_allowed():
    exercise = ExerciseFactory(published=True)
    assert exercise.is_published
    assert exercise.is_available_for_practice


def test_ready_but_unpublished_is_not_available_for_practice():
    exercise = ExerciseFactory(ready=True)
    assert exercise.is_available_for_practice is False


def test_audio_upload_path_is_namespaced_by_owner():
    exercise = ExerciseFactory(uploaded=True)
    assert exercise.audio_file.name.startswith(f"audio/{exercise.owner_id}/")
    assert exercise.audio_file.name.endswith(".mp3")
    assert exercise.has_audio


def test_pdf_upload_path_is_namespaced_by_owner():
    exercise = ExerciseFactory(with_pdf=True)
    assert exercise.pdf_file.name.startswith(f"pdf/{exercise.owner_id}/")
    assert exercise.pdf_file.name.endswith(".pdf")
    assert exercise.has_pdf


def test_exercise_without_pdf_reports_no_pdf():
    assert ExerciseFactory().has_pdf is False


def test_deleting_exercise_cascades_to_segments():
    exercise = ExerciseFactory()
    SegmentFactory(exercise=exercise, sequence=1)
    SegmentFactory(exercise=exercise, sequence=2)
    exercise.delete()
    assert TranscriptSegment.objects.count() == 0


def test_deleting_owner_cascades_to_exercises():
    exercise = ExerciseFactory()
    exercise.owner.delete()
    assert ListeningExercise.objects.count() == 0


# ----------------------------------------------------------------- segment


def check_constraints_immediately():
    """Force deferred constraints to be validated on statement execution.

    The unique constraint on (exercise, sequence) is DEFERRABLE INITIALLY
    DEFERRED so reordering works, which means violations normally surface at
    COMMIT. Inside a test the outer transaction never commits, so the check has
    to be requested explicitly.
    """
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_segment_sequence_is_unique_per_exercise():
    exercise = ExerciseFactory()
    SegmentFactory(exercise=exercise, sequence=1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            SegmentFactory(exercise=exercise, sequence=1)
            check_constraints_immediately()


def test_same_sequence_allowed_across_different_exercises():
    SegmentFactory(exercise=ExerciseFactory(), sequence=1)
    SegmentFactory(exercise=ExerciseFactory(), sequence=1)
    assert TranscriptSegment.objects.count() == 2


def test_segment_start_time_cannot_be_negative():
    with pytest.raises(IntegrityError):
        SegmentFactory(start_time=-0.1, end_time=5.0)


def test_segment_end_time_must_exceed_start_time():
    with pytest.raises(IntegrityError):
        SegmentFactory(start_time=5.0, end_time=5.0)


def test_segment_end_time_before_start_time_rejected():
    with pytest.raises(IntegrityError):
        SegmentFactory(start_time=5.0, end_time=2.0)


def test_segment_sequence_must_be_at_least_one():
    with pytest.raises(IntegrityError):
        SegmentFactory(sequence=0)


def test_segments_default_to_sequence_order():
    exercise = ExerciseFactory()
    third = SegmentFactory(exercise=exercise, sequence=3)
    first = SegmentFactory(exercise=exercise, sequence=1)
    second = SegmentFactory(exercise=exercise, sequence=2)
    assert list(exercise.segments.all()) == [first, second, third]


def test_segment_word_count_is_derived_on_save():
    segment = SegmentFactory(text="I would like accommodation near the university.")
    assert segment.word_count == 7


def test_segment_word_count_updates_on_edit():
    segment = SegmentFactory(text="One two three")
    segment.text = "One two"
    segment.save()
    segment.refresh_from_db()
    assert segment.word_count == 2


def test_segment_word_count_ignores_punctuation_and_extra_spaces():
    segment = SegmentFactory(text="  Hello,   world!  ")
    assert segment.word_count == 2


def test_segment_duration_property():
    segment = SegmentFactory(start_time=12.4, end_time=18.7)
    assert segment.duration == pytest.approx(6.3)


def test_deferred_unique_allows_reordering_within_a_transaction():
    """Swapping two sequences must not trip the unique constraint mid-update.

    This is the behaviour the DEFERRABLE INITIALLY DEFERRED constraint exists
    for; with an immediate constraint the first UPDATE below would fail.
    """
    exercise = ExerciseFactory()
    first = SegmentFactory(exercise=exercise, sequence=1)
    second = SegmentFactory(exercise=exercise, sequence=2)

    with transaction.atomic():
        TranscriptSegment.objects.filter(pk=first.pk).update(sequence=2)
        TranscriptSegment.objects.filter(pk=second.pk).update(sequence=1)

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.sequence, second.sequence) == (2, 1)
    assert list(exercise.segments.all()) == [second, first]
