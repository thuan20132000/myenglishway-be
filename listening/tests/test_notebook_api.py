import pytest
from django.urls import reverse

from listening.models import ListeningNotebook
from tests.factories import ExerciseFactory

pytestmark = pytest.mark.django_db


def notebook_url(exercise_id):
    return reverse("v1:listening-notebook", args=[exercise_id])


def complete_url(exercise_id):
    return reverse("v1:listening-notebook-complete", args=[exercise_id])


NOTEBOOK_LIST_URL = reverse("v1:listening-notebook-list")


# --------------------------------------------------------------------------
# Opening a notebook
# --------------------------------------------------------------------------


def test_opening_a_published_exercise_creates_the_notebook(auth_client, student):
    exercise = ExerciseFactory(published=True, with_pdf=True)

    response = auth_client(student).get(notebook_url(exercise.id))

    assert response.status_code == 200
    body = response.json()
    assert body["exercise_id"] == exercise.id
    assert body["title"] == exercise.title
    assert body["body"] == ""
    assert body["word_count"] == 0
    assert body["completed_at"] is None
    assert body["audio_url"]
    assert body["pdf_url"]
    assert ListeningNotebook.objects.filter(user=student, exercise=exercise).count() == 1


def test_reopening_does_not_create_a_second_notebook(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)

    first = client.get(notebook_url(exercise.id)).json()["id"]
    second = client.get(notebook_url(exercise.id)).json()["id"]

    assert first == second
    assert ListeningNotebook.objects.count() == 1


def test_notebook_on_someone_elses_draft_is_404(auth_client, student):
    exercise = ExerciseFactory()

    assert auth_client(student).get(notebook_url(exercise.id)).status_code == 404
    assert not ListeningNotebook.objects.exists()


def test_owner_can_open_a_notebook_on_their_unpublished_exercise(auth_client, student):
    exercise = ExerciseFactory(owner=student)

    assert auth_client(student).get(notebook_url(exercise.id)).status_code == 200


def test_anonymous_cannot_open_a_notebook(api_client):
    exercise = ExerciseFactory(published=True)
    assert api_client.get(notebook_url(exercise.id)).status_code == 401


def test_two_users_on_the_same_exercise_get_two_notebooks(auth_client, student, creator):
    exercise = ExerciseFactory(published=True)

    student_id = auth_client(student).get(notebook_url(exercise.id)).json()["id"]
    creator_id = auth_client(creator).get(notebook_url(exercise.id)).json()["id"]

    assert student_id != creator_id
    assert ListeningNotebook.objects.filter(exercise=exercise).count() == 2


def test_an_admin_does_not_read_another_learners_notes(
    auth_client, admin_user, student
):
    exercise = ExerciseFactory(published=True)
    auth_client(student).put(
        notebook_url(exercise.id), {"body": "their private notes"}, format="json"
    )

    body = auth_client(admin_user).get(notebook_url(exercise.id)).json()

    assert body["body"] == ""
    assert not ListeningNotebook.objects.filter(
        user=admin_user, exercise=exercise, body="their private notes"
    ).exists()


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


def test_put_writes_body_and_word_count(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)
    opened = client.get(notebook_url(exercise.id)).json()

    response = client.put(
        notebook_url(exercise.id), {"body": "Section 1: name is"}, format="json"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["body"] == "Section 1: name is"
    assert body["word_count"] == 4
    assert body["updated_at"] >= opened["updated_at"]

    notebook = ListeningNotebook.objects.get(user=student, exercise=exercise)
    assert notebook.body == "Section 1: name is"
    assert notebook.word_count == 4


def test_repeated_saves_upsert_rather_than_accumulate(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)

    for text in ("draft", "draft two", "final answer here"):
        response = client.put(notebook_url(exercise.id), {"body": text}, format="json")
        assert response.status_code == 200

    assert ListeningNotebook.objects.count() == 1
    notebook = ListeningNotebook.objects.get()
    assert notebook.body == "final answer here"
    assert notebook.word_count == 3


def test_empty_body_clears_the_note(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)
    client.put(notebook_url(exercise.id), {"body": "written"}, format="json")

    response = client.put(notebook_url(exercise.id), {"body": ""}, format="json")

    assert response.status_code == 200
    assert response.json()["body"] == ""
    assert response.json()["word_count"] == 0


def test_body_longer_than_the_limit_is_rejected(auth_client, student, settings):
    settings.MAX_LISTENING_NOTE_LENGTH = 10
    exercise = ExerciseFactory(published=True)

    response = auth_client(student).put(
        notebook_url(exercise.id), {"body": "x" * 11}, format="json"
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "body" in body


# --------------------------------------------------------------------------
# Completion
# --------------------------------------------------------------------------


def test_marking_complete_stamps_the_notebook(auth_client, student):
    exercise = ExerciseFactory(published=True)

    response = auth_client(student).post(complete_url(exercise.id))

    assert response.status_code == 200
    assert response.json()["completed_at"] is not None


def test_re_completing_keeps_the_original_timestamp(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)

    first = client.post(complete_url(exercise.id)).json()["completed_at"]
    second = client.post(complete_url(exercise.id)).json()["completed_at"]

    assert first == second


def test_completion_can_be_undone(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)
    client.post(complete_url(exercise.id))

    response = client.delete(complete_url(exercise.id))

    assert response.status_code == 200
    assert response.json()["completed_at"] is None


def test_undoing_completion_twice_stays_clear(auth_client, student):
    exercise = ExerciseFactory(published=True)
    client = auth_client(student)

    first = client.delete(complete_url(exercise.id)).json()["completed_at"]
    second = client.delete(complete_url(exercise.id)).json()["completed_at"]

    assert first is None
    assert second is None


# --------------------------------------------------------------------------
# Continue notes
# --------------------------------------------------------------------------


def test_notebook_list_shows_only_the_callers_notebooks(auth_client, student, creator):
    mine_exercise = ExerciseFactory(published=True)
    theirs_exercise = ExerciseFactory(published=True)
    mine = auth_client(student).get(notebook_url(mine_exercise.id)).json()
    auth_client(creator).get(notebook_url(theirs_exercise.id))

    rows = auth_client(student).get(NOTEBOOK_LIST_URL).json()["results"]

    assert [row["id"] for row in rows] == [mine["id"]]


def test_notebook_list_omits_bodies_and_is_newest_first(auth_client, student):
    older = ExerciseFactory(published=True, title="Older")
    newer = ExerciseFactory(published=True, title="Newer")
    client = auth_client(student)
    client.get(notebook_url(older.id))
    client.get(notebook_url(newer.id))
    client.put(notebook_url(older.id), {"body": "back to this one"}, format="json")

    rows = client.get(NOTEBOOK_LIST_URL).json()["results"]

    assert [row["exercise_id"] for row in rows] == [older.id, newer.id]
    assert rows[0]["title"] == "Older"
    assert rows[0]["word_count"] == 4
    assert "body" not in rows[0]


def test_notebook_list_requires_authentication(api_client):
    assert api_client.get(NOTEBOOK_LIST_URL).status_code == 401
