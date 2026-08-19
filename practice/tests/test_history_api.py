import datetime as dt

import pytest
from django.urls import reverse

from tests.factories import CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

HISTORY_URL = reverse("v1:practice-history")
ATTEMPTS_URL = reverse("v1:practice-attempts")
TEXT = "one two three four"


def submit_url(segment_id):
    return reverse("v1:segment-submit", args=[segment_id])


def reveal_url(segment_id):
    return reverse("v1:segment-reveal", args=[segment_id])


def make_exercise(title="Accommodation Practice", segments=3):
    exercise = ExerciseFactory(published=True, title=title)
    for index in range(1, segments + 1):
        SegmentFactory(
            exercise=exercise,
            sequence=index,
            start_time=(index - 1) * 10.0,
            end_time=(index - 1) * 10.0 + 8.0,
            text=TEXT,
        )
    return exercise


# -------------------------------------------------------------------- history


def test_history_is_empty_for_a_new_learner(auth_client, student):
    body = auth_client(student).get(HISTORY_URL).json()
    assert body["count"] == 0
    assert body["results"] == []


def test_history_summarises_one_exercise(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    first, second, _ = exercise.segments.all()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")  # 100
    client.post(submit_url(second.id), {"answer": "one two"}, format="json")  # 50

    body = client.get(HISTORY_URL).json()

    assert body["count"] == 1
    row = body["results"][0]
    assert row["exercise"] == {
        "id": exercise.id,
        "title": "Accommodation Practice",
        "total_segments": 3,
    }
    assert row["attempted_segments"] == 2
    assert row["completed_segments"] == 1
    assert row["total_attempts"] == 2
    assert row["average_score"] == 75.0
    assert row["last_practiced_at"] is not None


def test_history_lists_most_recently_practised_first(auth_client, student):
    older = make_exercise("Older")
    newer = make_exercise("Newer")
    client = auth_client(student)
    client.post(submit_url(older.segments.first().id), {"answer": TEXT}, format="json")
    client.post(submit_url(newer.segments.first().id), {"answer": TEXT}, format="json")

    body = client.get(HISTORY_URL).json()
    assert [row["exercise"]["title"] for row in body["results"]] == ["Newer", "Older"]


def test_history_counts_reveals_as_attempts_but_not_as_segments(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    client.post(reveal_url(exercise.segments.first().id))

    row = client.get(HISTORY_URL).json()["results"][0]
    assert row["total_attempts"] == 1
    assert row["attempted_segments"] == 0
    assert row["average_score"] is None


def test_history_filters_by_exercise(auth_client, student):
    wanted = make_exercise("Wanted")
    other = make_exercise("Other")
    client = auth_client(student)
    client.post(submit_url(wanted.segments.first().id), {"answer": TEXT}, format="json")
    client.post(submit_url(other.segments.first().id), {"answer": TEXT}, format="json")

    body = client.get(HISTORY_URL, {"exercise": wanted.id}).json()
    assert [row["exercise"]["id"] for row in body["results"]] == [wanted.id]


def test_history_filters_by_date_range(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    client.post(submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json")

    today = dt.date.today().isoformat()
    assert client.get(HISTORY_URL, {"date_from": today}).json()["count"] == 1

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert client.get(HISTORY_URL, {"date_from": tomorrow}).json()["count"] == 0

    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert client.get(HISTORY_URL, {"date_to": yesterday}).json()["count"] == 0


def test_history_rejects_a_malformed_date(auth_client, student):
    response = auth_client(student).get(HISTORY_URL, {"date_from": "not-a-date"})
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "date_from" in response.json()


def test_history_rejects_an_inverted_date_range(auth_client, student):
    response = auth_client(student).get(
        HISTORY_URL, {"date_from": "2026-08-19", "date_to": "2026-08-01"}
    )
    assert response.status_code == 400


def test_history_is_isolated_per_user(auth_client, student):
    exercise = make_exercise()
    other = CreatorFactory()
    auth_client(student).post(
        submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json"
    )

    assert auth_client(other).get(HISTORY_URL).json()["count"] == 0


def test_history_survives_the_exercise_being_unpublished(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    client.post(submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json")

    exercise.is_published = False
    exercise.save()

    body = client.get(HISTORY_URL).json()
    assert body["count"] == 1
    assert body["results"][0]["exercise"]["title"] == "Accommodation Practice"


def test_history_requires_authentication(api_client):
    assert api_client.get(HISTORY_URL).status_code == 401


def test_history_query_count_is_constant(auth_client, student, django_assert_num_queries):
    client = auth_client(student)
    for index in range(3):
        exercise = make_exercise(f"Exercise {index}")
        client.post(submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(4):  # count + grouped page + bests + exercises
        client.get(HISTORY_URL)

    for index in range(3, 9):
        exercise = make_exercise(f"Exercise {index}")
        client.post(submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(4):
        client.get(HISTORY_URL)


# ------------------------------------------------------------------- attempts


def test_attempts_lists_the_callers_own_attempts(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    segment = exercise.segments.first()
    client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    body = client.get(ATTEMPTS_URL).json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["kind"] == "submission"
    assert row["score"] == 100.0
    assert row["exercise"] == {"id": exercise.id, "title": exercise.title}
    assert row["segment"] == {"id": segment.id, "sequence": 1}
    assert row["result"]["counts"]["expected_total"] == 4


def test_attempts_never_expose_another_user(auth_client, student):
    exercise = make_exercise()
    other = CreatorFactory()
    auth_client(student).post(
        submit_url(exercise.segments.first().id), {"answer": TEXT}, format="json"
    )

    assert auth_client(other).get(ATTEMPTS_URL).json()["count"] == 0


def test_attempts_are_newest_first(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    first, second, _ = exercise.segments.all()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")
    client.post(submit_url(second.id), {"answer": TEXT}, format="json")

    body = client.get(ATTEMPTS_URL).json()
    assert [row["segment"]["sequence"] for row in body["results"]] == [2, 1]


def test_attempts_filter_by_kind(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    first, second, _ = exercise.segments.all()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")
    client.post(reveal_url(second.id))

    body = client.get(ATTEMPTS_URL, {"kind": "reveal"}).json()
    assert body["count"] == 1
    assert body["results"][0]["kind"] == "reveal"
    assert body["results"][0]["score"] is None


def test_attempts_filter_by_exercise_and_segment(auth_client, student):
    exercise = make_exercise()
    client = auth_client(student)
    first, second, _ = exercise.segments.all()
    client.post(submit_url(first.id), {"answer": TEXT}, format="json")
    client.post(submit_url(second.id), {"answer": TEXT}, format="json")

    assert client.get(ATTEMPTS_URL, {"exercise": exercise.id}).json()["count"] == 2
    assert client.get(ATTEMPTS_URL, {"segment": first.id}).json()["count"] == 1


def test_attempts_query_count_is_constant(auth_client, student, django_assert_num_queries):
    exercise = make_exercise(segments=10)
    client = auth_client(student)
    for segment in exercise.segments.all()[:3]:
        client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(2):  # count + page
        client.get(ATTEMPTS_URL)

    for segment in exercise.segments.all()[3:]:
        client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    with django_assert_num_queries(2):
        client.get(ATTEMPTS_URL)


def test_attempts_require_authentication(api_client):
    assert api_client.get(ATTEMPTS_URL).status_code == 401
