import pytest
from django.urls import reverse

from listening.models import ExerciseStatus
from listening.services import check_publish_preconditions
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db


def publish_url(exercise_id) -> str:
    return reverse("v1:exercise-publish", args=[exercise_id])


def unpublish_url(exercise_id) -> str:
    return reverse("v1:exercise-unpublish", args=[exercise_id])


def publishable(owner, **overrides):
    """An exercise that satisfies every publish precondition."""
    exercise = ExerciseFactory(owner=owner, ready=True, duration=60.0, **overrides)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)
    return exercise


# ------------------------------------------------------------------- publish


def test_owner_can_publish_a_ready_exercise(auth_client, creator):
    exercise = publishable(creator)

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 200
    body = response.json()
    assert body["is_published"] is True
    assert body["status"] == ExerciseStatus.READY
    assert body["published_at"] is not None

    exercise.refresh_from_db()
    assert exercise.is_published is True
    assert exercise.published_at is not None


def test_published_exercise_becomes_visible_to_students(auth_client, creator, student):
    exercise = publishable(creator)
    assert auth_client(creator).post(publish_url(exercise.id)).status_code == 200

    listing = auth_client(student).get(reverse("v1:exercise-list")).json()
    assert [row["id"] for row in listing["results"]] == [exercise.id]


def test_publish_without_segments_reports_reason(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=60.0)

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "EXERCISE_NOT_READY"
    assert "no_segments" in body["extra"]["reasons"]


def test_publish_without_audio_reports_reason(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, duration=60.0)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 409
    assert "missing_audio" in response.json()["extra"]["reasons"]


def test_publish_without_duration_reports_reason(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=None)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 409
    assert "missing_duration" in response.json()["extra"]["reasons"]


def test_publish_reports_every_failing_reason_at_once(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)  # no audio, no duration, no segments

    response = auth_client(creator).post(publish_url(exercise.id))

    reasons = response.json()["extra"]["reasons"]
    assert {"missing_audio", "missing_duration", "no_segments"} <= set(reasons)


def test_publish_rejects_segments_beyond_the_audio(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=10.0)
    SegmentFactory(exercise=exercise, sequence=1, start_time=0.0, end_time=5.0)
    # Duration was shortened after this segment was created.
    exercise.segments.filter(sequence=1).update(end_time=30.0)

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 409
    assert "segments_exceed_duration" in response.json()["extra"]["reasons"]


def test_publish_while_processing_is_refused(auth_client, creator):
    exercise = publishable(creator)
    exercise.status = ExerciseStatus.PROCESSING
    exercise.save()

    response = auth_client(creator).post(publish_url(exercise.id))

    assert response.status_code == 409
    assert "status_is_processing" in response.json()["extra"]["reasons"]


def test_publishing_twice_is_a_conflict(auth_client, creator):
    exercise = publishable(creator)
    client = auth_client(creator)
    assert client.post(publish_url(exercise.id)).status_code == 200

    response = client.post(publish_url(exercise.id))
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_ALREADY_PUBLISHED"


def test_non_owner_cannot_publish(auth_client, creator):
    exercise = publishable(CreatorFactory())
    exercise.is_published = True
    exercise.save()

    response = auth_client(creator).post(publish_url(exercise.id))
    assert response.status_code == 403


def test_creator_cannot_publish_an_invisible_draft(auth_client, creator):
    exercise = publishable(CreatorFactory())
    assert auth_client(creator).post(publish_url(exercise.id)).status_code == 404


def test_student_cannot_publish(auth_client, student, creator):
    exercise = publishable(creator)
    exercise.is_published = True
    exercise.save()

    assert auth_client(student).post(publish_url(exercise.id)).status_code == 403


def test_admin_can_publish_any_exercise(auth_client, creator):
    exercise = publishable(creator)
    assert auth_client(AdminFactory()).post(publish_url(exercise.id)).status_code == 200


def test_publish_requires_authentication(api_client, creator):
    exercise = publishable(creator)
    assert api_client.post(publish_url(exercise.id)).status_code == 401


def test_publish_is_post_only(auth_client, creator):
    exercise = publishable(creator)
    assert auth_client(creator).get(publish_url(exercise.id)).status_code == 405


# ----------------------------------------------------------------- unpublish


def test_owner_can_unpublish(auth_client, creator):
    exercise = publishable(creator)
    client = auth_client(creator)
    client.post(publish_url(exercise.id))

    response = client.post(unpublish_url(exercise.id))

    assert response.status_code == 200
    assert response.json()["is_published"] is False
    assert response.json()["published_at"] is None
    exercise.refresh_from_db()
    assert exercise.is_published is False
    assert exercise.status == ExerciseStatus.READY  # status is unchanged


def test_unpublish_is_idempotent(auth_client, creator):
    exercise = publishable(creator)
    response = auth_client(creator).post(unpublish_url(exercise.id))
    assert response.status_code == 200
    assert response.json()["is_published"] is False


def test_unpublished_exercise_disappears_for_students(auth_client, creator, student):
    exercise = publishable(creator)
    client = auth_client(creator)
    client.post(publish_url(exercise.id))
    client.post(unpublish_url(exercise.id))

    student_client = auth_client(student)
    assert student_client.get(reverse("v1:exercise-list")).json()["results"] == []
    assert student_client.get(reverse("v1:exercise-detail", args=[exercise.id])).status_code == 404


def test_non_owner_cannot_unpublish(auth_client, creator):
    exercise = publishable(CreatorFactory())
    exercise.is_published = True
    exercise.save()

    assert auth_client(creator).post(unpublish_url(exercise.id)).status_code == 403


def test_unpublish_then_republish(auth_client, creator):
    exercise = publishable(creator)
    client = auth_client(creator)
    client.post(publish_url(exercise.id))
    client.post(unpublish_url(exercise.id))

    response = client.post(publish_url(exercise.id))
    assert response.status_code == 200
    assert response.json()["is_published"] is True


# ------------------------------------------------------- preconditions unit


def test_check_publish_preconditions_returns_empty_when_ready(creator):
    assert check_publish_preconditions(publishable(creator)) == []


def test_segment_within_tolerance_does_not_block_publishing(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True, duration=20.0)
    SegmentFactory(exercise=exercise, sequence=1, start_time=10.0, end_time=20.4)

    assert check_publish_preconditions(exercise) == []
    assert auth_client(creator).post(publish_url(exercise.id)).status_code == 200


# ---------------------------------------------------- duration writability


def test_creator_can_set_duration_on_create(auth_client, creator):
    response = auth_client(creator).post(
        reverse("v1:exercise-list"), {"title": "With duration", "duration": 42.5}, format="multipart"
    )
    assert response.status_code == 201
    assert response.json()["duration"] == 42.5


def test_creator_can_set_duration_by_patch(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).patch(
        reverse("v1:exercise-detail", args=[exercise.id]), {"duration": 99.5}, format="json"
    )
    assert response.status_code == 200
    exercise.refresh_from_db()
    assert exercise.duration == 99.5


def test_duration_must_be_positive(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    response = auth_client(creator).patch(
        reverse("v1:exercise-detail", args=[exercise.id]), {"duration": 0}, format="json"
    )
    assert response.status_code == 400
    assert "duration" in response.json()


def test_duration_supplied_with_new_audio_survives_the_reset(
    auth_client, creator, tmp_path, settings
):
    """Replacing audio clears the old duration; an explicit new one must stick."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, ready=True, duration=60.0)

    response = auth_client(creator).patch(
        reverse("v1:exercise-detail", args=[exercise.id]),
        {
            "audio_file": SimpleUploadedFile("new.mp3", b"new-audio-bytes", "audio/mpeg"),
            "duration": 120.0,
        },
        format="multipart",
    )

    assert response.status_code == 200
    exercise.refresh_from_db()
    assert exercise.duration == 120.0
    assert exercise.status == ExerciseStatus.UPLOADED


def test_replacing_audio_alone_still_clears_duration(auth_client, creator, tmp_path, settings):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, ready=True, duration=60.0)

    auth_client(creator).patch(
        reverse("v1:exercise-detail", args=[exercise.id]),
        {"audio_file": SimpleUploadedFile("new.mp3", b"new-audio-bytes", "audio/mpeg")},
        format="multipart",
    )

    exercise.refresh_from_db()
    assert exercise.duration is None
