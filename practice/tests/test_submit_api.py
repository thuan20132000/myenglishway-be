import pytest
from django.urls import reverse

from listening.models import ExerciseStatus
from practice.models import AttemptKind, PracticeAttempt
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

TRANSCRIPT = "I would like accommodation near the university."


def submit_url(segment_id) -> str:
    return reverse("v1:segment-submit", args=[segment_id])


@pytest.fixture
def published_segment():
    exercise = ExerciseFactory(published=True)
    return SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPT)


# ------------------------------------------------------------- authentication


def test_submit_requires_authentication(api_client, published_segment):
    response = api_client.post(
        submit_url(published_segment.id), {"answer": "anything"}, format="json"
    )
    assert response.status_code == 401


# -------------------------------------------------------------------- scoring


def test_correct_answer_scores_full_marks(auth_client, student, published_segment):
    response = auth_client(student).post(
        submit_url(published_segment.id), {"answer": TRANSCRIPT}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["score"] == 100.0
    assert body["correct_answer"] == TRANSCRIPT
    assert body["result"]["missing"] == []
    assert body["attempt_id"] > 0
    assert body["segment_id"] == published_segment.id


def test_answer_missing_one_word(auth_client, student, published_segment):
    response = auth_client(student).post(
        submit_url(published_segment.id),
        {"answer": "I would like accommodation near university"},
        format="json",
    )

    body = response.json()
    assert body["score"] == 85.7
    assert body["result"]["missing"] == [{"expected": "the", "position": 5}]
    assert body["result"]["counts"]["expected_total"] == 7


def test_case_and_punctuation_differences_still_score_full(auth_client, student, published_segment):
    response = auth_client(student).post(
        submit_url(published_segment.id),
        {"answer": "i would like accommodation near the university"},
        format="json",
    )
    assert response.json()["score"] == 100.0


def test_empty_answer_is_valid_and_scores_zero(auth_client, student, published_segment):
    response = auth_client(student).post(
        submit_url(published_segment.id), {"answer": ""}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["score"] == 0.0
    assert response.json()["result"]["counts"]["missing"] == 7


def test_null_answer_is_rejected(auth_client, student, published_segment):
    response = auth_client(student).post(
        submit_url(published_segment.id), {"answer": None}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "answer" in response.json()


def test_missing_answer_field_is_rejected(auth_client, student, published_segment):
    response = auth_client(student).post(submit_url(published_segment.id), {}, format="json")
    assert response.status_code == 400
    assert "answer" in response.json()


def test_overlong_answer_is_rejected(auth_client, student, published_segment, settings):
    settings.PRACTICE_MAX_ANSWER_LENGTH = 20
    response = auth_client(student).post(
        submit_url(published_segment.id), {"answer": "x" * 50}, format="json"
    )
    assert response.status_code == 400


# ------------------------------------------------------------------ persistence


def test_attempt_is_persisted_with_counts_and_snapshot(auth_client, student, published_segment):
    auth_client(student).post(
        submit_url(published_segment.id),
        {"answer": "I would like accommodation near university"},
        format="json",
    )

    attempt = PracticeAttempt.objects.get()
    assert attempt.user == student
    assert attempt.segment == published_segment
    assert attempt.exercise_id == published_segment.exercise_id
    assert attempt.kind == AttemptKind.SUBMISSION
    assert attempt.score == 85.7
    assert attempt.correct_count == 6
    assert attempt.missing_count == 1
    assert attempt.extra_count == 0
    assert attempt.incorrect_count == 0
    assert attempt.expected_snapshot == TRANSCRIPT
    assert attempt.normalized_answer == "i would like accommodation near university"
    assert attempt.scoring_version == "v1"


def test_raw_answer_is_stored_unmodified(auth_client, student, published_segment):
    raw = "  I  would LIKE accommodation!!  "
    auth_client(student).post(submit_url(published_segment.id), {"answer": raw}, format="json")

    attempt = PracticeAttempt.objects.get()
    assert attempt.user_answer == raw
    assert attempt.normalized_answer == "i would like accommodation"


def test_repeat_submissions_each_create_an_attempt(auth_client, student, published_segment):
    client = auth_client(student)
    client.post(submit_url(published_segment.id), {"answer": "first try"}, format="json")
    client.post(submit_url(published_segment.id), {"answer": TRANSCRIPT}, format="json")

    assert PracticeAttempt.objects.filter(user=student).count() == 2
    assert PracticeAttempt.objects.filter(user=student).first().score == 100.0


def test_editing_the_transcript_does_not_alter_past_attempts(
    auth_client, student, published_segment
):
    auth_client(student).post(
        submit_url(published_segment.id), {"answer": TRANSCRIPT}, format="json"
    )

    published_segment.text = "Something else entirely now."
    published_segment.save()

    attempt = PracticeAttempt.objects.get()
    assert attempt.expected_snapshot == TRANSCRIPT
    assert attempt.score == 100.0


# ------------------------------------------------------------------ availability


def test_cannot_submit_to_an_unpublished_exercise(auth_client, student):
    segment = SegmentFactory(exercise=ExerciseFactory(ready=True), text=TRANSCRIPT)

    response = auth_client(student).post(submit_url(segment.id), {"answer": "x"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_NOT_PUBLISHED"
    assert PracticeAttempt.objects.count() == 0


def test_unpublished_exercise_response_leaks_no_transcript(auth_client, student):
    segment = SegmentFactory(exercise=ExerciseFactory(ready=True), text=TRANSCRIPT)
    response = auth_client(student).post(submit_url(segment.id), {"answer": "x"}, format="json")
    assert TRANSCRIPT not in response.content.decode()


def test_cannot_submit_to_a_missing_segment(auth_client, student):
    response = auth_client(student).post(submit_url(999999), {"answer": "x"}, format="json")
    assert response.status_code == 404


def test_cannot_submit_when_transcript_is_blank(auth_client, student):
    exercise = ExerciseFactory(published=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text="placeholder")
    segment.__class__.objects.filter(pk=segment.pk).update(text="   ")

    response = auth_client(student).post(submit_url(segment.id), {"answer": "x"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "TRANSCRIPT_NOT_AVAILABLE"


def test_creator_can_practise_their_own_unpublished_exercise(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPT)

    response = auth_client(creator).post(
        submit_url(segment.id), {"answer": TRANSCRIPT}, format="json"
    )
    assert response.status_code == 201


def test_other_creator_cannot_practise_an_unpublished_exercise(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPT)

    response = auth_client(creator).post(submit_url(segment.id), {"answer": "x"}, format="json")
    assert response.status_code == 409


def test_admin_can_practise_an_unpublished_exercise(auth_client):
    exercise = ExerciseFactory(ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPT)

    response = auth_client(AdminFactory()).post(
        submit_url(segment.id), {"answer": TRANSCRIPT}, format="json"
    )
    assert response.status_code == 201


def test_cannot_submit_while_exercise_is_processing(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, status=ExerciseStatus.PROCESSING)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPT)

    response = auth_client(creator).post(submit_url(segment.id), {"answer": "x"}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_NOT_READY"


# ------------------------------------------------------------------- ownership


def test_attempts_are_recorded_against_the_submitting_user(
    auth_client, student, published_segment
):
    other = CreatorFactory()
    auth_client(student).post(
        submit_url(published_segment.id), {"answer": TRANSCRIPT}, format="json"
    )

    assert PracticeAttempt.objects.filter(user=student).count() == 1
    assert PracticeAttempt.objects.filter(user=other).count() == 0


def test_submit_is_post_only(auth_client, student, published_segment):
    assert auth_client(student).get(submit_url(published_segment.id)).status_code == 405
