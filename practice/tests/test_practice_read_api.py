"""Learner-facing read endpoints.

The most important tests here are the leak checks: no practice response may
contain transcript text before the learner has earned it. They assert both the
absence of a ``text`` key and the absence of the transcript string anywhere in
the raw response body, because a leak could arrive through a nested object as
easily as through the obvious field.
"""

import pytest
from django.urls import reverse

from practice.models import AttemptKind, PracticeAttempt
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

TRANSCRIPTS = [
    "Good morning, how can I help you?",
    "I would like accommodation near the university.",
    "Certainly, for how many nights?",
]


def start_url(exercise_id):
    return reverse("v1:practice-start", args=[exercise_id])


def segments_url(exercise_id):
    return reverse("v1:practice-segments", args=[exercise_id])


def segment_url(exercise_id, segment_id):
    return reverse("v1:practice-segment-detail", args=[exercise_id, segment_id])


def reveal_url(segment_id):
    return reverse("v1:segment-reveal", args=[segment_id])


def submit_url(segment_id):
    return reverse("v1:segment-submit", args=[segment_id])


@pytest.fixture
def published_exercise():
    exercise = ExerciseFactory(published=True)
    for index, text in enumerate(TRANSCRIPTS, start=1):
        SegmentFactory(
            exercise=exercise,
            sequence=index,
            start_time=(index - 1) * 10.0,
            end_time=(index - 1) * 10.0 + 8.0,
            text=text,
        )
    return exercise


def assert_no_transcript(response):
    body = response.content.decode()
    for transcript in TRANSCRIPTS:
        assert transcript not in body, f"transcript leaked: {transcript!r}"
    for phrase in ["good morning", "accommodation", "how many nights"]:
        assert phrase not in body.lower(), f"transcript fragment leaked: {phrase!r}"


# ------------------------------------------------------------- hidden transcript


def test_start_response_hides_the_transcript(auth_client, student, published_exercise):
    response = auth_client(student).post(start_url(published_exercise.id))
    assert response.status_code == 200
    assert "text" not in response.json()["current_segment"]
    assert_no_transcript(response)


def test_segment_list_hides_the_transcript(auth_client, student, published_exercise):
    response = auth_client(student).get(segments_url(published_exercise.id))
    assert response.status_code == 200
    assert all("text" not in row for row in response.json())
    assert_no_transcript(response)


def test_segment_detail_hides_the_transcript(auth_client, student, published_exercise):
    segment = published_exercise.segments.first()
    response = auth_client(student).get(segment_url(published_exercise.id, segment.id))

    assert response.status_code == 200
    assert "text" not in response.json()
    assert set(response.json()) == {
        "id", "sequence", "start_time", "end_time", "word_count",
    }
    assert_no_transcript(response)


def test_transcript_stays_hidden_even_for_the_owner(auth_client, creator):
    """The practice representation has no text field for anyone."""
    exercise = ExerciseFactory(owner=creator, ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPTS[0])

    response = auth_client(creator).get(segment_url(exercise.id, segment.id))
    assert response.status_code == 200
    assert "text" not in response.json()


def test_word_count_is_exposed_without_the_words(auth_client, student, published_exercise):
    response = auth_client(student).get(segments_url(published_exercise.id))
    # "Good / morning / how / can / I / help / you"
    assert response.json()[0]["word_count"] == 7
    assert_no_transcript(response)


# ------------------------------------------------------------------------ start


def test_start_returns_the_first_segment_for_a_new_learner(
    auth_client, student, published_exercise
):
    body = auth_client(student).post(start_url(published_exercise.id)).json()

    assert body["exercise_id"] == published_exercise.id
    assert body["total_segments"] == 3
    assert body["attempted_segments"] == 0
    assert body["current_segment"]["sequence"] == 1
    assert body["audio_url"].endswith(".mp3")
    # This exercise has no handout attached.
    assert body["pdf_url"] is None


def test_start_returns_the_pdf_handout_when_one_is_attached(auth_client, student):
    exercise = ExerciseFactory(published=True, with_pdf=True)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=8.0)

    body = auth_client(student).post(start_url(exercise.id)).json()

    assert body["pdf_url"].endswith(".pdf")


def test_start_resumes_after_previous_attempts(auth_client, student, published_exercise):
    client = auth_client(student)
    first, second, _ = published_exercise.segments.all()
    client.post(submit_url(first.id), {"answer": "x"}, format="json")
    client.post(submit_url(second.id), {"answer": "y"}, format="json")

    body = client.post(start_url(published_exercise.id)).json()

    assert body["attempted_segments"] == 2
    assert body["current_segment"]["sequence"] == 3


def test_start_skips_only_answered_segments(auth_client, student, published_exercise):
    """A gap is resumed at the earliest unanswered segment, not the latest."""
    client = auth_client(student)
    segments = list(published_exercise.segments.all())
    client.post(submit_url(segments[1].id), {"answer": "x"}, format="json")

    body = client.post(start_url(published_exercise.id)).json()
    assert body["current_segment"]["sequence"] == 1


def test_start_returns_null_when_everything_is_attempted(
    auth_client, student, published_exercise
):
    client = auth_client(student)
    for segment in published_exercise.segments.all():
        client.post(submit_url(segment.id), {"answer": "x"}, format="json")

    body = client.post(start_url(published_exercise.id)).json()
    assert body["current_segment"] is None
    assert body["attempted_segments"] == 3


def test_reveals_do_not_count_as_attempted(auth_client, student, published_exercise):
    client = auth_client(student)
    first = published_exercise.segments.first()
    client.post(reveal_url(first.id))

    body = client.post(start_url(published_exercise.id)).json()
    assert body["attempted_segments"] == 0
    assert body["current_segment"]["sequence"] == 1


def test_progress_is_per_user(auth_client, student, published_exercise):
    other = CreatorFactory()
    first = published_exercise.segments.first()
    auth_client(student).post(submit_url(first.id), {"answer": "x"}, format="json")

    body = auth_client(other).post(start_url(published_exercise.id)).json()
    assert body["attempted_segments"] == 0
    assert body["current_segment"]["sequence"] == 1


def test_start_on_unpublished_exercise_is_refused(auth_client, student):
    exercise = ExerciseFactory(ready=True)
    SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(student).post(start_url(exercise.id))
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_NOT_PUBLISHED"


def test_start_on_missing_exercise_returns_404(auth_client, student):
    assert auth_client(student).post(start_url(999999)).status_code == 404


def test_start_requires_authentication(api_client, published_exercise):
    assert api_client.post(start_url(published_exercise.id)).status_code == 401


# --------------------------------------------------------------- segment list


def test_segment_list_is_ordered_and_unpaginated(auth_client, student, published_exercise):
    body = auth_client(student).get(segments_url(published_exercise.id)).json()
    assert isinstance(body, list)
    assert [row["sequence"] for row in body] == [1, 2, 3]


def test_segment_list_reports_per_segment_progress(auth_client, student, published_exercise):
    client = auth_client(student)
    first = published_exercise.segments.first()
    client.post(submit_url(first.id), {"answer": TRANSCRIPTS[0]}, format="json")

    body = client.get(segments_url(published_exercise.id)).json()
    assert body[0]["attempted"] is True
    assert body[0]["best_score"] == 100.0
    assert body[1]["attempted"] is False
    assert body[1]["best_score"] is None


def test_best_score_keeps_the_highest_attempt(auth_client, student, published_exercise):
    client = auth_client(student)
    first = published_exercise.segments.first()
    client.post(submit_url(first.id), {"answer": TRANSCRIPTS[0]}, format="json")
    client.post(submit_url(first.id), {"answer": "wrong"}, format="json")

    body = client.get(segments_url(published_exercise.id)).json()
    assert body[0]["best_score"] == 100.0


def test_segment_list_query_count_is_constant(
    auth_client, student, published_exercise, django_assert_num_queries
):
    client = auth_client(student)
    with django_assert_num_queries(2):  # exercise lookup + annotated segment page
        client.get(segments_url(published_exercise.id))

    for index in range(4, 12):
        SegmentFactory(exercise=published_exercise, sequence=index, text="more words here")

    with django_assert_num_queries(2):
        client.get(segments_url(published_exercise.id))


# ------------------------------------------------------------- segment detail


def test_segment_detail_from_another_exercise_returns_404(
    auth_client, student, published_exercise
):
    other = ExerciseFactory(published=True)
    stray = SegmentFactory(exercise=other, sequence=1, text="elsewhere")

    response = auth_client(student).get(segment_url(published_exercise.id, stray.id))
    assert response.status_code == 404


def test_segment_detail_on_unpublished_exercise_is_refused(auth_client, student):
    exercise = ExerciseFactory(ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPTS[0])

    response = auth_client(student).get(segment_url(exercise.id, segment.id))
    assert response.status_code == 409
    assert TRANSCRIPTS[0] not in response.content.decode()


# --------------------------------------------------------------------- reveal


def test_reveal_returns_the_transcript(auth_client, student, published_exercise):
    segment = published_exercise.segments.first()

    response = auth_client(student).post(reveal_url(segment.id))

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == TRANSCRIPTS[0]
    assert body["revealed"] is True
    assert body["segment_id"] == segment.id


def test_reveal_records_an_attempt_without_a_score(auth_client, student, published_exercise):
    segment = published_exercise.segments.first()
    auth_client(student).post(reveal_url(segment.id))

    attempt = PracticeAttempt.objects.get()
    assert attempt.kind == AttemptKind.REVEAL
    assert attempt.score is None
    assert attempt.user_answer == ""
    assert attempt.expected_snapshot == TRANSCRIPTS[0]


def test_reveal_does_not_mark_the_segment_attempted(auth_client, student, published_exercise):
    client = auth_client(student)
    segment = published_exercise.segments.first()
    client.post(reveal_url(segment.id))

    body = client.get(segments_url(published_exercise.id)).json()
    assert body[0]["attempted"] is False
    assert body[0]["best_score"] is None


def test_repeat_reveals_are_each_recorded(auth_client, student, published_exercise):
    client = auth_client(student)
    segment = published_exercise.segments.first()
    client.post(reveal_url(segment.id))
    client.post(reveal_url(segment.id))

    assert PracticeAttempt.objects.filter(kind=AttemptKind.REVEAL).count() == 2


def test_reveal_on_unpublished_exercise_is_refused(auth_client, student):
    exercise = ExerciseFactory(ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPTS[0])

    response = auth_client(student).post(reveal_url(segment.id))
    assert response.status_code == 409
    assert TRANSCRIPTS[0] not in response.content.decode()


def test_reveal_on_missing_segment_returns_404(auth_client, student):
    assert auth_client(student).post(reveal_url(999999)).status_code == 404


def test_reveal_requires_authentication(api_client, published_exercise):
    segment = published_exercise.segments.first()
    assert api_client.post(reveal_url(segment.id)).status_code == 401


def test_reveal_is_post_only(auth_client, student, published_exercise):
    segment = published_exercise.segments.first()
    assert auth_client(student).get(reveal_url(segment.id)).status_code == 405


def test_admin_can_reveal_on_an_unpublished_exercise(auth_client):
    exercise = ExerciseFactory(ready=True)
    segment = SegmentFactory(exercise=exercise, sequence=1, text=TRANSCRIPTS[0])

    response = auth_client(AdminFactory()).post(reveal_url(segment.id))
    assert response.status_code == 200
    assert response.json()["text"] == TRANSCRIPTS[0]
