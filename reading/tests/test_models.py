import pytest
from django.db import IntegrityError, transaction

from reading.models import ReadingExercise, ReadingSession

pytestmark = pytest.mark.django_db


def test_word_count_is_derived_on_save(passage_factory):
    passage = passage_factory(body="one two three")
    assert passage.word_count == 3

    passage.body = "one two three four five"
    passage.save()
    passage.refresh_from_db()
    assert passage.word_count == 5


def test_audio_is_namespaced_by_owner(passage_factory, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    passage = passage_factory(with_audio=True)
    assert passage.audio_file.name.startswith(f"reading-audio/{passage.owner_id}/")
    assert passage.audio_file.name.endswith(".mp3")


def test_suggested_wpm_must_be_in_range(passage_factory):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            passage_factory(suggested_wpm=50)


def test_word_count_must_be_positive(passage_factory):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            passage_factory(body="...")


def test_unfinished_session_has_no_actual_wpm(passage_factory, session_factory, student):
    session = session_factory(user=student, exercise=passage_factory())
    assert session.actual_wpm is None
    assert session.is_finished is False


def test_finished_session_requires_actual_wpm(passage_factory, session_factory, student):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            session_factory(
                user=student,
                exercise=passage_factory(),
                finished_at="2026-09-15T12:00:00Z",
                actual_wpm=None,
                reading_ms=1000,
                progress=100,
            )


def test_deleting_a_passage_cascades_to_sessions(passage_factory, session_factory):
    session = session_factory(exercise=passage_factory())
    session.exercise.delete()
    assert not ReadingExercise.objects.exists()
    assert not ReadingSession.objects.exists()
