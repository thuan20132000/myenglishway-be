import pytest
from django.urls import reverse

from listening.models import ExerciseStatus, TranscriptSegment
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


def segments_url(exercise_id) -> str:
    return reverse("v1:exercise-segments", args=[exercise_id])


def reorder_url(exercise_id) -> str:
    return reverse("v1:exercise-segments-reorder", args=[exercise_id])


def segment_url(segment_id) -> str:
    return reverse("v1:segment-detail", args=[segment_id])


def payload(**overrides) -> dict:
    """Build a segment payload. Pass ``key=None`` to omit a field entirely."""
    body = {
        "sequence": 1,
        "start_time": 12.4,
        "end_time": 18.7,
        "text": "I'd like to book a room for next weekend.",
    }
    body.update(overrides)
    return {key: value for key, value in body.items() if value is not None}


# ------------------------------------------------------------- authentication


def test_segment_list_requires_authentication(api_client):
    exercise = ExerciseFactory()
    assert api_client.get(segments_url(exercise.id)).status_code == 401


def test_segment_detail_requires_authentication(api_client):
    segment = SegmentFactory()
    assert api_client.get(segment_url(segment.id)).status_code == 401


# ---------------------------------------------------------------- permissions


def test_student_cannot_list_segments_of_published_exercise(auth_client, student):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, text="hidden transcript")

    response = auth_client(student).get(segments_url(exercise.id))
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert "hidden transcript" not in response.content.decode()


def test_student_cannot_read_a_single_segment(auth_client, student):
    segment = SegmentFactory(exercise=ExerciseFactory(published=True), text="hidden transcript")

    response = auth_client(student).get(segment_url(segment.id))
    assert response.status_code == 403
    assert "hidden transcript" not in response.content.decode()


def test_other_creator_cannot_read_segments(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), published=True)
    SegmentFactory(exercise=exercise, sequence=1)

    assert auth_client(creator).get(segments_url(exercise.id)).status_code == 403


def test_admin_can_read_any_segments(auth_client):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1)

    assert auth_client(AdminFactory()).get(segments_url(exercise.id)).status_code == 200


def test_student_cannot_create_segment(auth_client, student):
    exercise = ExerciseFactory(published=True)
    response = auth_client(student).post(segments_url(exercise.id), payload(), format="json")
    assert response.status_code == 403
    assert TranscriptSegment.objects.count() == 0


# ----------------------------------------------------------------------- list


def test_owner_lists_segments_in_sequence_order(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=exercise, sequence=3, text="third")
    SegmentFactory(exercise=exercise, sequence=1, text="first")
    SegmentFactory(exercise=exercise, sequence=2, text="second")

    body = auth_client(creator).get(segments_url(exercise.id)).json()
    assert [row["text"] for row in body] == ["first", "second", "third"]
    assert body[0]["word_count"] == 1


def test_segment_list_is_not_paginated(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=exercise, sequence=1)

    body = auth_client(creator).get(segments_url(exercise.id)).json()
    assert isinstance(body, list)


def test_list_for_missing_exercise_returns_404(auth_client, creator):
    assert auth_client(creator).get(segments_url(999999)).status_code == 404


# --------------------------------------------------------------------- create


def test_owner_can_create_segment(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    response = auth_client(creator).post(segments_url(exercise.id), payload(), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["sequence"] == 1
    assert body["word_count"] == 9
    assert body["text"] == "I'd like to book a room for next weekend."


def test_creating_first_segment_makes_exercise_ready(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    auth_client(creator).post(segments_url(exercise.id), payload(), format="json")

    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.READY


def test_segment_on_audioless_exercise_leaves_it_draft(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).post(segments_url(exercise.id), payload(), format="json")

    assert response.status_code == 201
    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.DRAFT


def test_sequence_is_appended_when_omitted(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    SegmentFactory(exercise=exercise, sequence=1)
    SegmentFactory(exercise=exercise, sequence=2)

    body = auth_client(creator).post(
        segments_url(exercise.id), payload(sequence=None), format="json"
    ).json()
    assert body["sequence"] == 3


def test_duplicate_sequence_is_rejected_cleanly(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(creator).post(segments_url(exercise.id), payload(sequence=1), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "DUPLICATE_SEGMENT_SEQUENCE"
    assert TranscriptSegment.objects.filter(exercise=exercise).count() == 1


def test_end_time_before_start_time_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    response = auth_client(creator).post(
        segments_url(exercise.id), payload(start_time=10.0, end_time=5.0), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SEGMENT_RANGE"


def test_equal_start_and_end_time_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).post(
        segments_url(exercise.id), payload(start_time=5.0, end_time=5.0), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SEGMENT_RANGE"


def test_negative_start_time_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).post(
        segments_url(exercise.id), payload(start_time=-1.0), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SEGMENT_RANGE"
    assert response.json()["extra"]["field"] == "start_time"


def test_segment_beyond_audio_duration_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=20.0)

    response = auth_client(creator).post(
        segments_url(exercise.id), payload(start_time=10.0, end_time=25.0), format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "SEGMENT_OUTSIDE_AUDIO"
    assert response.json()["extra"]["duration"] == 20.0


def test_segment_within_duration_tolerance_is_accepted(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=20.0)

    response = auth_client(creator).post(
        segments_url(exercise.id), payload(start_time=10.0, end_time=20.4), format="json"
    )
    assert response.status_code == 201


def test_unknown_duration_skips_the_bounds_check(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).post(
        segments_url(exercise.id), payload(end_time=9999.0), format="json"
    )
    assert response.status_code == 201


def test_blank_text_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).post(segments_url(exercise.id), payload(text="   "), format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "text" in response.json()


def test_text_is_trimmed(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    body = auth_client(creator).post(
        segments_url(exercise.id), payload(text="  spaced out  "), format="json"
    ).json()
    assert body["text"] == "spaced out"


def test_cannot_edit_segments_while_processing(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, status=ExerciseStatus.PROCESSING)

    response = auth_client(creator).post(segments_url(exercise.id), payload(), format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_PROCESSING"


# ------------------------------------------------------------------- retrieve


def test_owner_can_retrieve_segment(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator), text="the transcript")

    body = auth_client(creator).get(segment_url(segment.id)).json()
    assert body["text"] == "the transcript"
    assert body["id"] == segment.id


def test_retrieve_missing_segment_returns_404(auth_client, creator):
    assert auth_client(creator).get(segment_url(999999)).status_code == 404


# --------------------------------------------------------------------- update


def test_owner_can_correct_transcript_text(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator), text="acommodation")

    response = auth_client(creator).patch(
        segment_url(segment.id), {"text": "accommodation is fine"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["word_count"] == 3
    segment.refresh_from_db()
    assert segment.text == "accommodation is fine"


def test_owner_can_adjust_timestamps(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator), start_time=1.0, end_time=5.0)

    response = auth_client(creator).patch(
        segment_url(segment.id), {"start_time": 2.5, "end_time": 8.0}, format="json"
    )
    assert response.status_code == 200
    segment.refresh_from_db()
    assert (segment.start_time, segment.end_time) == (2.5, 8.0)


def test_patch_rejects_range_inversion_against_stored_value(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator), start_time=10.0, end_time=20.0)

    response = auth_client(creator).patch(segment_url(segment.id), {"end_time": 5.0}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SEGMENT_RANGE"


def test_patch_to_taken_sequence_is_rejected(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    SegmentFactory(exercise=exercise, sequence=1)
    second = SegmentFactory(exercise=exercise, sequence=2)

    response = auth_client(creator).patch(segment_url(second.id), {"sequence": 1}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "DUPLICATE_SEGMENT_SEQUENCE"


def test_patch_to_own_sequence_is_allowed(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator), sequence=1)

    response = auth_client(creator).patch(segment_url(segment.id), {"sequence": 1}, format="json")
    assert response.status_code == 200


def test_non_owner_cannot_patch_segment(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=CreatorFactory(), published=True))

    response = auth_client(creator).patch(segment_url(segment.id), {"text": "hijack"}, format="json")
    assert response.status_code == 403


def test_put_is_not_allowed_on_segments(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=creator))
    assert auth_client(creator).put(segment_url(segment.id), payload(), format="json").status_code == 405


# --------------------------------------------------------------------- delete


def test_owner_can_delete_segment(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    first = SegmentFactory(exercise=exercise, sequence=1)
    SegmentFactory(exercise=exercise, sequence=2)

    response = auth_client(creator).delete(segment_url(first.id))
    assert response.status_code == 204
    assert not TranscriptSegment.objects.filter(pk=first.id).exists()


def test_deleting_last_segment_unpublishes_the_exercise(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, published=True)
    only = SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(creator).delete(segment_url(only.id))

    assert response.status_code == 204
    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.UPLOADED
    assert exercise.is_published is False
    assert exercise.published_at is None


def test_non_owner_cannot_delete_segment(auth_client, creator):
    segment = SegmentFactory(exercise=ExerciseFactory(owner=CreatorFactory(), published=True))
    assert auth_client(creator).delete(segment_url(segment.id)).status_code == 403


# -------------------------------------------------------------------- reorder


def test_owner_can_swap_two_segments(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    first = SegmentFactory(exercise=exercise, sequence=1, text="first")
    second = SegmentFactory(exercise=exercise, sequence=2, text="second")

    response = auth_client(creator).post(
        reorder_url(exercise.id),
        {"ordering": [{"id": first.id, "sequence": 2}, {"id": second.id, "sequence": 1}]},
        format="json",
    )

    assert response.status_code == 200
    assert [row["text"] for row in response.json()] == ["second", "first"]
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.sequence, second.sequence) == (2, 1)


def test_reorder_reverses_a_longer_list(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    segments = [SegmentFactory(exercise=exercise, sequence=i, text=f"s{i}") for i in range(1, 5)]

    ordering = [
        {"id": segment.id, "sequence": 5 - segment.sequence} for segment in segments
    ]
    response = auth_client(creator).post(reorder_url(exercise.id), {"ordering": ordering}, format="json")

    assert response.status_code == 200
    assert [row["text"] for row in response.json()] == ["s4", "s3", "s2", "s1"]


def test_reorder_rejects_incomplete_ordering(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    first = SegmentFactory(exercise=exercise, sequence=1)
    SegmentFactory(exercise=exercise, sequence=2)

    response = auth_client(creator).post(
        reorder_url(exercise.id), {"ordering": [{"id": first.id, "sequence": 1}]}, format="json"
    )
    assert response.status_code == 400
    assert "ordering" in response.json()


def test_reorder_rejects_duplicate_sequences(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    first = SegmentFactory(exercise=exercise, sequence=1)
    second = SegmentFactory(exercise=exercise, sequence=2)

    response = auth_client(creator).post(
        reorder_url(exercise.id),
        {"ordering": [{"id": first.id, "sequence": 1}, {"id": second.id, "sequence": 1}]},
        format="json",
    )
    assert response.status_code == 400


def test_reorder_rejects_segment_from_another_exercise(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    mine = SegmentFactory(exercise=exercise, sequence=1)
    theirs = SegmentFactory(exercise=ExerciseFactory(owner=creator), sequence=1)

    response = auth_client(creator).post(
        reorder_url(exercise.id),
        {"ordering": [{"id": mine.id, "sequence": 1}, {"id": theirs.id, "sequence": 2}]},
        format="json",
    )
    assert response.status_code == 400


def test_non_owner_cannot_reorder(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), published=True)
    segment = SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(creator).post(
        reorder_url(exercise.id), {"ordering": [{"id": segment.id, "sequence": 1}]}, format="json"
    )
    assert response.status_code == 403
