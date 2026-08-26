"""Worksheet mode: PDF + player + answer key + transcript, nothing recorded."""

import pytest
from django.urls import reverse

from practice.models import PracticeAttempt
from tests.factories import (
    ExerciseAnswerFactory,
    ExerciseFactory,
    SegmentFactory,
)

pytestmark = pytest.mark.django_db


def worksheet_url(exercise_id) -> str:
    return reverse("v1:practice-worksheet", args=[exercise_id])


def answers_url(exercise_id) -> str:
    return reverse("v1:practice-answers", args=[exercise_id])


def transcript_url(exercise_id) -> str:
    return reverse("v1:practice-transcript", args=[exercise_id])


def public_transcript_url(exercise_id) -> str:
    return reverse("v1:public-practice-transcript", args=[exercise_id])


@pytest.fixture
def worksheet_exercise(tmp_path, settings):
    """A published exercise with audio, a PDF, a transcript and an 11-20 key."""
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(published=True, with_pdf=True)
    for index, text in enumerate(["Good morning.", "How can I help?"], start=1):
        SegmentFactory(
            exercise=exercise,
            sequence=index,
            start_time=(index - 1) * 10.0,
            end_time=(index - 1) * 10.0 + 8.0,
            text=text,
        )
    for number, text in [(11, "library"), (12, "9.30"), (13, "blue")]:
        ExerciseAnswerFactory(exercise=exercise, number=number, text=text)
    return exercise


# ------------------------------------------------------------------ worksheet


def test_worksheet_returns_everything_the_page_needs(
    auth_client, student, worksheet_exercise
):
    body = auth_client(student).get(worksheet_url(worksheet_exercise.id)).json()

    assert body["exercise_id"] == worksheet_exercise.id
    assert body["title"] == worksheet_exercise.title
    assert body["audio_url"].endswith(".mp3")
    assert body["pdf_url"].endswith(".pdf")
    assert body["duration"] == 184.2
    assert body["has_transcript"] is True


def test_question_numbers_are_the_printed_ones(auth_client, student, worksheet_exercise):
    """A Section 2 sheet is questions 11-20, not 1-10."""
    body = auth_client(student).get(worksheet_url(worksheet_exercise.id)).json()

    assert body["question_numbers"] == [11, 12, 13]


def test_worksheet_without_an_answer_key_is_still_usable(auth_client, student, tmp_path,
                                                        settings):
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(published=True, with_pdf=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    body = auth_client(student).get(worksheet_url(exercise.id)).json()

    assert body["question_numbers"] == []
    assert body["pdf_url"].endswith(".pdf")


def test_worksheet_reports_a_missing_pdf_as_null(auth_client, student):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    assert auth_client(student).get(worksheet_url(exercise.id)).json()["pdf_url"] is None


# -------------------------------------------------------------------- answers


def test_learner_can_read_the_answer_key(auth_client, student, worksheet_exercise):
    response = auth_client(student).get(answers_url(worksheet_exercise.id))

    assert response.status_code == 200
    assert response.json() == [
        {"number": 11, "text": "library"},
        {"number": 12, "text": "9.30"},
        {"number": 13, "text": "blue"},
    ]


def test_answers_are_empty_when_there_is_no_key(auth_client, student):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    assert auth_client(student).get(answers_url(exercise.id)).json() == []


def test_reading_answers_records_nothing(auth_client, student, worksheet_exercise):
    auth_client(student).get(answers_url(worksheet_exercise.id))

    assert PracticeAttempt.objects.count() == 0


# ----------------------------------------------------------------- transcript


def test_learner_can_read_the_whole_transcript(auth_client, student, worksheet_exercise):
    response = auth_client(student).get(transcript_url(worksheet_exercise.id))

    assert response.status_code == 200
    body = response.json()
    assert [line["text"] for line in body] == ["Good morning.", "How can I help?"]
    assert [line["sequence"] for line in body] == [1, 2]
    assert body[0]["start_time"] == 0.0


def test_reading_the_transcript_records_no_attempt(
    auth_client, student, worksheet_exercise
):
    """The point of the feature: unlike reveal/, looking is not an event."""
    auth_client(student).get(transcript_url(worksheet_exercise.id))

    assert PracticeAttempt.objects.count() == 0


def test_transcript_conflicts_when_there_are_no_segments(auth_client, student, tmp_path,
                                                         settings):
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(published=True)

    response = auth_client(student).get(transcript_url(exercise.id))

    assert response.status_code == 409
    assert response.json()["code"] == "TRANSCRIPT_NOT_AVAILABLE"


def test_visitor_can_read_a_published_transcript(api_client, worksheet_exercise):
    response = api_client.get(public_transcript_url(worksheet_exercise.id))

    assert response.status_code == 200
    assert [line["text"] for line in response.json()] == [
        "Good morning.",
        "How can I help?",
    ]


def test_visitor_cannot_see_an_unpublished_transcript(api_client):
    exercise = ExerciseFactory(ready=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    assert api_client.get(public_transcript_url(exercise.id)).status_code == 404


# --------------------------------------------------------------------- access


@pytest.mark.parametrize("url_for", [worksheet_url, answers_url, transcript_url])
def test_requires_authentication(api_client, worksheet_exercise, url_for):
    assert api_client.get(url_for(worksheet_exercise.id)).status_code == 401


@pytest.mark.parametrize("url_for", [worksheet_url, answers_url, transcript_url])
def test_student_cannot_open_an_unpublished_exercise(auth_client, student, url_for):
    exercise = ExerciseFactory(ready=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    response = auth_client(student).get(url_for(exercise.id))

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_NOT_PUBLISHED"


def test_owner_can_preview_their_unpublished_worksheet(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)
    ExerciseAnswerFactory(exercise=exercise, number=1, text="library")

    response = auth_client(creator).get(worksheet_url(exercise.id))

    assert response.status_code == 200
    assert response.json()["question_numbers"] == [1]
