import pytest
from django.db import IntegrityError

from practice.models import AttemptKind, PracticeAttempt
from tests.factories import AttemptFactory, ExerciseFactory, SegmentFactory, UserFactory

pytestmark = pytest.mark.django_db


def test_attempt_defaults_to_submission():
    attempt = AttemptFactory()
    assert attempt.kind == AttemptKind.SUBMISSION
    assert attempt.is_submission


def test_attempts_are_ordered_newest_first():
    user = UserFactory()
    first = AttemptFactory(user=user)
    second = AttemptFactory(user=user)
    assert list(PracticeAttempt.objects.all()) == [second, first]


def test_score_above_100_is_rejected():
    with pytest.raises(IntegrityError):
        AttemptFactory(score=100.1)


def test_negative_score_is_rejected():
    with pytest.raises(IntegrityError):
        AttemptFactory(score=-0.1)


def test_reveal_may_not_carry_a_score():
    with pytest.raises(IntegrityError):
        AttemptFactory(kind=AttemptKind.REVEAL, score=50.0)


def test_reveal_with_null_score_is_allowed():
    attempt = AttemptFactory(reveal=True)
    assert attempt.score is None
    assert not attempt.is_submission


def test_exercise_is_denormalised_from_the_segment():
    segment = SegmentFactory()
    attempt = AttemptFactory(segment=segment)
    assert attempt.exercise_id == segment.exercise_id


def test_deleting_the_segment_cascades_to_attempts():
    segment = SegmentFactory()
    AttemptFactory(segment=segment)
    segment.delete()
    assert PracticeAttempt.objects.count() == 0


def test_deleting_the_exercise_cascades_to_attempts():
    exercise = ExerciseFactory(ready=True)
    AttemptFactory(segment=SegmentFactory(exercise=exercise))
    exercise.delete()
    assert PracticeAttempt.objects.count() == 0


def test_deleting_the_user_cascades_to_attempts():
    user = UserFactory()
    AttemptFactory(user=user)
    user.delete()
    assert PracticeAttempt.objects.count() == 0


def test_result_defaults_to_an_empty_dict():
    attempt = AttemptFactory()
    attempt.refresh_from_db()
    assert attempt.result == {}


def test_result_round_trips_structured_json():
    payload = {"missing": [{"expected": "the", "position": 5}], "extra": []}
    attempt = AttemptFactory(result=payload)
    attempt.refresh_from_db()
    assert attempt.result == payload
