import pytest
from django.urls import reverse

from reading.models import ReadingMode, ReadingSession

pytestmark = pytest.mark.django_db


def sessions_url(passage_id):
    return reverse("v1:reading-session-list", args=[passage_id])


def session_url(session_id):
    return reverse("v1:reading-session-detail", args=[session_id])


def test_start_snapshots_word_count(auth_client, student, passage_factory):
    passage = passage_factory(owner=student, body=" ".join(["word"] * 750))
    assert passage.word_count == 750

    response = auth_client(student).post(
        sessions_url(passage.id),
        {"mode": "paced", "target_wpm": 250, "strict_mode": True},
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["word_count"] == 750
    assert body["target_wpm"] == 250
    assert body["mode"] == ReadingMode.PACED
    assert body["strict_mode"] is True
    assert body["finished_at"] is None
    assert body["actual_wpm"] is None
    assert body["started_at"]


def test_finish_computes_actual_wpm(auth_client, student, passage_factory):
    passage = passage_factory(owner=student, body=" ".join(["word"] * 750))
    started = auth_client(student).post(
        sessions_url(passage.id), {"target_wpm": 250}, format="json"
    ).json()

    response = auth_client(student).patch(
        session_url(started["id"]),
        {"reading_ms": 180_000, "paused_ms": 5_000, "progress": 100},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["actual_wpm"] == 250.0
    assert body["reading_ms"] == 180_000
    assert body["paused_ms"] == 5_000
    assert body["progress"] == 100
    assert body["finished_at"] is not None


def test_body_edit_does_not_rewrite_session_word_count(
    auth_client, student, passage_factory
):
    passage = passage_factory(owner=student, body="one two three")
    started = auth_client(student).post(
        sessions_url(passage.id), {"target_wpm": 200}, format="json"
    ).json()

    auth_client(student).patch(
        reverse("v1:reading-exercise-detail", args=[passage.id]),
        {"body": " ".join(["word"] * 20)},
        format="json",
    )

    finished = auth_client(student).patch(
        session_url(started["id"]),
        {"reading_ms": 60_000, "progress": 100},
        format="json",
    ).json()

    assert finished["word_count"] == 3
    assert finished["actual_wpm"] == 3.0


def test_reading_ms_zero_is_rejected(auth_client, student, passage_factory, session_factory):
    session = session_factory(user=student, exercise=passage_factory(owner=student))
    response = auth_client(student).patch(
        session_url(session.id), {"reading_ms": 0, "progress": 100}, format="json"
    )
    assert response.status_code == 400
    assert "reading_ms" in response.json()


def test_finishing_twice_conflicts(auth_client, student, passage_factory, session_factory):
    session = session_factory(
        user=student, exercise=passage_factory(owner=student), finished=True
    )
    response = auth_client(student).patch(
        session_url(session.id),
        {"reading_ms": 1000, "progress": 100},
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "SESSION_ALREADY_FINISHED"


def test_another_user_cannot_patch_your_session(
    auth_client, student, creator, passage_factory, session_factory
):
    session = session_factory(user=student, exercise=passage_factory(published=True))
    response = auth_client(creator).patch(
        session_url(session.id),
        {"reading_ms": 1000, "progress": 100},
        format="json",
    )
    assert response.status_code == 404


def test_admin_cannot_patch_someone_elses_session(
    auth_client, admin_user, student, passage_factory, session_factory
):
    session = session_factory(user=student, exercise=passage_factory(published=True))
    response = auth_client(admin_user).patch(
        session_url(session.id),
        {"reading_ms": 1000, "progress": 100},
        format="json",
    )
    assert response.status_code == 404


def test_list_returns_only_the_callers_sessions(
    auth_client, student, creator, passage_factory, session_factory
):
    passage = passage_factory(published=True)
    mine = session_factory(user=student, exercise=passage)
    session_factory(user=creator, exercise=passage)

    rows = auth_client(student).get(sessions_url(passage.id)).json()["results"]
    assert [row["id"] for row in rows] == [mine.id]


def test_cannot_start_a_session_on_someone_elses_draft(
    auth_client, student, passage_factory
):
    other = passage_factory()
    response = auth_client(student).post(
        sessions_url(other.id), {"target_wpm": 250}, format="json"
    )
    assert response.status_code == 404


def test_target_wpm_out_of_range_is_rejected(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    response = auth_client(student).post(
        sessions_url(passage.id), {"target_wpm": 50}, format="json"
    )
    assert response.status_code == 400
    assert "target_wpm" in response.json()


def test_start_defaults_to_paced(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    body = auth_client(student).post(
        sessions_url(passage.id), {"target_wpm": 200}, format="json"
    ).json()
    assert body["mode"] == ReadingMode.PACED
    assert body["strict_mode"] is False
    assert ReadingSession.objects.get(pk=body["id"]).word_count == passage.word_count
