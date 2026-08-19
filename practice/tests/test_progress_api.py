import pytest
from django.urls import reverse

from tests.factories import CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


def progress_url(exercise_id):
    return reverse("v1:practice-progress", args=[exercise_id])


def submit_url(segment_id):
    return reverse("v1:segment-submit", args=[segment_id])


def reveal_url(segment_id):
    return reverse("v1:segment-reveal", args=[segment_id])


TEXT = "one two three four"


@pytest.fixture
def exercise_with_segments():
    exercise = ExerciseFactory(published=True)
    for index in range(1, 5):
        SegmentFactory(
            exercise=exercise,
            sequence=index,
            start_time=(index - 1) * 10.0,
            end_time=(index - 1) * 10.0 + 8.0,
            text=TEXT,
        )
    return exercise


def test_fresh_learner_has_empty_progress(auth_client, student, exercise_with_segments):
    body = auth_client(student).get(progress_url(exercise_with_segments.id)).json()

    assert body == {
        "exercise_id": exercise_with_segments.id,
        "total_segments": 4,
        "attempted_segments": 0,
        "completed_segments": 0,
        "revealed_segments": 0,
        "progress_percentage": 0.0,
        "average_score": None,
        "last_segment_sequence": None,
        "last_practiced_at": None,
    }


def test_a_perfect_answer_counts_as_completed(auth_client, student, exercise_with_segments):
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["attempted_segments"] == 1
    assert body["completed_segments"] == 1
    assert body["progress_percentage"] == 25.0
    assert body["average_score"] == 100.0
    assert body["last_segment_sequence"] == 1
    assert body["last_practiced_at"] is not None


def test_a_low_score_is_attempted_but_not_completed(
    auth_client, student, exercise_with_segments
):
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(submit_url(first.id), {"answer": "one"}, format="json")  # 25%

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["attempted_segments"] == 1
    assert body["completed_segments"] == 0
    assert body["progress_percentage"] == 0.0
    assert body["average_score"] == 25.0


def test_completion_threshold_is_configurable(
    auth_client, student, exercise_with_segments, settings
):
    settings.PRACTICE_COMPLETION_THRESHOLD = 20.0
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(submit_url(first.id), {"answer": "one"}, format="json")  # 25%

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["completed_segments"] == 1


def test_average_uses_the_best_attempt_per_segment(
    auth_client, student, exercise_with_segments
):
    """Retrying a segment must never lower a learner's average."""
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")  # 100
    client.post(submit_url(first.id), {"answer": "one"}, format="json")  # 25

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["average_score"] == 100.0
    assert body["attempted_segments"] == 1


def test_average_spans_attempted_segments_only(auth_client, student, exercise_with_segments):
    client = auth_client(student)
    first, second, *_ = exercise_with_segments.segments.all()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")  # 100
    client.post(submit_url(second.id), {"answer": "one two"}, format="json")  # 50

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["average_score"] == 75.0
    assert body["attempted_segments"] == 2


def test_last_segment_sequence_is_the_furthest_answered(
    auth_client, student, exercise_with_segments
):
    client = auth_client(student)
    segments = list(exercise_with_segments.segments.all())
    client.post(submit_url(segments[2].id), {"answer": TEXT}, format="json")
    client.post(submit_url(segments[0].id), {"answer": TEXT}, format="json")

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["last_segment_sequence"] == 3


def test_reveals_are_counted_separately(auth_client, student, exercise_with_segments):
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(reveal_url(first.id))

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["revealed_segments"] == 1
    assert body["attempted_segments"] == 0
    assert body["completed_segments"] == 0
    assert body["average_score"] is None
    assert body["last_practiced_at"] is not None


def test_repeat_reveals_count_one_segment(auth_client, student, exercise_with_segments):
    client = auth_client(student)
    first = exercise_with_segments.segments.first()
    client.post(reveal_url(first.id))
    client.post(reveal_url(first.id))

    assert client.get(progress_url(exercise_with_segments.id)).json()["revealed_segments"] == 1


def test_full_completion_reports_one_hundred_percent(
    auth_client, student, exercise_with_segments
):
    client = auth_client(student)
    for segment in exercise_with_segments.segments.all():
        client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    body = client.get(progress_url(exercise_with_segments.id)).json()
    assert body["progress_percentage"] == 100.0
    assert body["completed_segments"] == 4


def test_progress_is_isolated_per_user(auth_client, student, exercise_with_segments):
    other = CreatorFactory()
    first = exercise_with_segments.segments.first()
    auth_client(student).post(submit_url(first.id), {"answer": TEXT}, format="json")

    body = auth_client(other).get(progress_url(exercise_with_segments.id)).json()
    assert body["attempted_segments"] == 0
    assert body["average_score"] is None


def test_progress_on_an_exercise_without_segments(auth_client, student):
    exercise = ExerciseFactory(published=True)
    exercise.status = "ready"
    exercise.save()

    body = auth_client(student).get(progress_url(exercise.id)).json()
    assert body["total_segments"] == 0
    assert body["progress_percentage"] == 0.0


def test_progress_requires_authentication(api_client, exercise_with_segments):
    assert api_client.get(progress_url(exercise_with_segments.id)).status_code == 401


def test_progress_on_unpublished_exercise_is_refused(auth_client, student):
    exercise = ExerciseFactory(ready=True)
    SegmentFactory(exercise=exercise, sequence=1, text=TEXT)
    assert auth_client(student).get(progress_url(exercise.id)).status_code == 409


def test_progress_query_count_is_constant(
    auth_client, student, exercise_with_segments, django_assert_num_queries
):
    client = auth_client(student)
    for segment in exercise_with_segments.segments.all():
        client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(4):  # user + bests + totals + segment count
        client.get(progress_url(exercise_with_segments.id))

    for index in range(5, 20):
        segment = SegmentFactory(exercise=exercise_with_segments, sequence=index, text=TEXT)
        client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(4):
        client.get(progress_url(exercise_with_segments.id))
