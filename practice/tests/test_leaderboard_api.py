import datetime as dt

import pytest
from django.urls import reverse

from tests.factories import AttemptFactory, ExerciseFactory, SegmentFactory, UserFactory

pytestmark = pytest.mark.django_db

LEADERBOARD_URL = reverse("v1:practice-leaderboard")
TEXT = "one two three four"


def submit_url(segment_id):
    return reverse("v1:segment-submit", args=[segment_id])


def reveal_url(segment_id):
    return reverse("v1:segment-reveal", args=[segment_id])


def test_leaderboard_is_empty_when_nobody_has_submitted(auth_client, student):
    body = auth_client(student).get(LEADERBOARD_URL).json()

    assert body["count"] == 0
    assert body["results"] == []
    assert body["me"] is None


def test_leaderboard_ranks_by_submission_count(auth_client, student):
    segment = SegmentFactory()
    other = UserFactory(full_name="Ada Nguyen")
    student.full_name = "Lin Tran"
    student.save(update_fields=["full_name"])

    for _ in range(3):
        AttemptFactory(user=other, segment=segment)
    AttemptFactory(user=student, segment=segment)

    body = auth_client(student).get(LEADERBOARD_URL).json()

    assert body["count"] == 2
    assert [row["user"]["id"] for row in body["results"]] == [other.id, student.id]
    assert body["results"][0]["rank"] == 1
    assert body["results"][0]["practice_count"] == 3
    assert body["results"][1]["rank"] == 2
    assert body["results"][1]["practice_count"] == 1
    assert body["me"]["user"]["id"] == student.id
    assert body["me"]["rank"] == 2


def test_reveals_do_not_increase_practice_count(auth_client, student):
    segment = SegmentFactory()
    AttemptFactory(user=student, segment=segment, reveal=True)
    AttemptFactory(user=student, segment=segment)

    body = auth_client(student).get(LEADERBOARD_URL).json()

    assert body["count"] == 1
    assert body["results"][0]["practice_count"] == 1
    assert body["results"][0]["attempted_segments"] == 1


def test_retrying_a_segment_raises_count_but_not_coverage(auth_client, student):
    segment = SegmentFactory(text=TEXT)
    AttemptFactory(user=student, segment=segment, score=50.0)
    AttemptFactory(user=student, segment=segment, score=100.0)

    body = auth_client(student).get(LEADERBOARD_URL).json()
    row = body["results"][0]

    assert row["practice_count"] == 2
    assert row["attempted_segments"] == 1
    assert row["completed_segments"] == 1
    assert row["average_score"] == 100.0


def test_leaderboard_filters_by_date_range(auth_client, student):
    exercise = ExerciseFactory(published=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TEXT)
    client = auth_client(student)
    client.post(submit_url(segment.id), {"answer": TEXT}, format="json")

    today = dt.date.today().isoformat()
    assert client.get(LEADERBOARD_URL, {"date_from": today}).json()["count"] == 1

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert client.get(LEADERBOARD_URL, {"date_from": tomorrow}).json()["count"] == 0

    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert client.get(LEADERBOARD_URL, {"date_to": yesterday}).json()["count"] == 0


def test_leaderboard_rejects_an_inverted_date_range(auth_client, student):
    response = auth_client(student).get(
        LEADERBOARD_URL, {"date_from": "2026-08-19", "date_to": "2026-08-01"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_leaderboard_never_exposes_email(auth_client, student):
    AttemptFactory(user=student)
    body = auth_client(student).get(LEADERBOARD_URL).json()

    assert "email" not in body["results"][0]["user"]
    assert body["results"][0]["user"].keys() == {"id", "full_name"}
    assert "email" not in body["me"]["user"]


def test_leaderboard_requires_authentication(api_client):
    assert api_client.get(LEADERBOARD_URL).status_code == 401


def test_me_is_present_when_the_caller_is_off_the_page(auth_client, student):
    segment = SegmentFactory()
    for count in (3, 2):
        other = UserFactory()
        for _ in range(count):
            AttemptFactory(user=other, segment=segment)
    AttemptFactory(user=student, segment=segment)

    body = auth_client(student).get(LEADERBOARD_URL, {"page_size": 2}).json()

    assert student.id not in [row["user"]["id"] for row in body["results"]]
    assert body["me"]["user"]["id"] == student.id
    assert body["me"]["rank"] == 3
    assert body["me"]["practice_count"] == 1
