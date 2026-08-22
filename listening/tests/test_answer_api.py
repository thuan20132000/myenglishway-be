import pytest
from django.urls import reverse

from listening.models import ExerciseAnswer
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory

pytestmark = pytest.mark.django_db

KEY = "11. library\n12. 9.30\n13. blue"


def answers_url(exercise_id) -> str:
    return reverse("v1:exercise-answer-key", args=[exercise_id])


# -------------------------------------------------------------------- writing


def test_owner_can_write_an_answer_key(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).put(
        answers_url(exercise.id), {"answer_key": KEY}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["answers"] == [
        {"number": 11, "text": "library"},
        {"number": 12, "text": "9.30"},
        {"number": 13, "text": "blue"},
    ]
    assert exercise.answers.count() == 3


def test_key_round_trips_as_text(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    client = auth_client(creator)
    client.put(answers_url(exercise.id), {"answer_key": KEY}, format="json")

    assert client.get(answers_url(exercise.id)).json()["answer_key"] == KEY


def test_writing_replaces_the_whole_key(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    client = auth_client(creator)
    client.put(answers_url(exercise.id), {"answer_key": KEY}, format="json")

    response = client.put(
        answers_url(exercise.id), {"answer_key": "1. one\n2. two"}, format="json"
    )

    assert response.status_code == 200
    # The old 11-13 rows are gone rather than merged with the new 1-2.
    assert list(exercise.answers.values_list("number", flat=True)) == [1, 2]


def test_empty_key_clears_the_answers(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    client = auth_client(creator)
    client.put(answers_url(exercise.id), {"answer_key": KEY}, format="json")

    response = client.put(answers_url(exercise.id), {"answer_key": ""}, format="json")

    assert response.status_code == 200
    assert response.json()["answers"] == []
    assert not exercise.answers.exists()


def test_key_is_editable_while_published(auth_client, creator):
    """Unlike the audio: no segment timing depends on the answer key."""
    exercise = ExerciseFactory(owner=creator, published=True)

    response = auth_client(creator).put(
        answers_url(exercise.id), {"answer_key": KEY}, format="json"
    )

    assert response.status_code == 200
    exercise.refresh_from_db()
    assert exercise.is_published is True


def test_unreadable_key_is_rejected_with_its_code(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).put(
        answers_url(exercise.id), {"answer_key": "1. library\nblue"}, format="json"
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_ANSWER_KEY"
    assert body["extra"] == {"line": "blue"}
    assert not ExerciseAnswer.objects.exists()


# ------------------------------------------------------------------- access


def test_another_creator_cannot_read_the_key(auth_client, creator):
    """Reads are owner-only too: this is the solution to the sheet."""
    exercise = ExerciseFactory(owner=creator, published=True)

    response = auth_client(CreatorFactory()).get(answers_url(exercise.id))

    assert response.status_code == 403


def test_student_cannot_read_the_creator_endpoint(auth_client, student):
    exercise = ExerciseFactory(published=True)

    assert auth_client(student).get(answers_url(exercise.id)).status_code == 403


def test_student_cannot_write_a_key(auth_client, student):
    exercise = ExerciseFactory(published=True)

    response = auth_client(student).put(
        answers_url(exercise.id), {"answer_key": KEY}, format="json"
    )

    assert response.status_code == 403


def test_admin_can_read_any_key(auth_client):
    exercise = ExerciseFactory()

    assert auth_client(AdminFactory()).get(answers_url(exercise.id)).status_code == 200


def test_requires_authentication(api_client):
    exercise = ExerciseFactory()
    assert api_client.get(answers_url(exercise.id)).status_code == 401


# ------------------------------------------------------- exercise detail count


def test_detail_reports_the_answer_count(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    auth_client(creator).put(answers_url(exercise.id), {"answer_key": KEY}, format="json")

    detail = auth_client(creator).get(reverse("v1:exercise-detail", args=[exercise.id]))

    assert detail.json()["answer_count"] == 3


def test_deleting_the_exercise_cascades_to_its_answers(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    auth_client(creator).put(answers_url(exercise.id), {"answer_key": KEY}, format="json")

    exercise.delete()

    assert not ExerciseAnswer.objects.exists()
