"""Automatic transcription on upload, plus the transcribe/ and status/ endpoints."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.urls import reverse

from listening.models import ExerciseStatus
from listening.services import create_exercise, schedule_transcription
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:exercise-list")


def detail_url(pk):
    return reverse("v1:exercise-detail", args=[pk])


def transcribe_url(pk):
    return reverse("v1:exercise-transcribe", args=[pk])


def status_url(pk):
    return reverse("v1:exercise-status", args=[pk])


def audio(name="lesson.mp3"):
    return SimpleUploadedFile(name, b"fake-audio-bytes", "audio/mpeg")


@pytest.fixture
def auto_start(settings):
    """Auto transcription is off by default in tests; switch it on here."""
    settings.TRANSCRIPTION_AUTO_START = True
    return settings


@pytest.fixture
def queued():
    """Capture enqueued tasks instead of running them.

    Note that the trigger uses ``transaction.on_commit``, and the transaction
    pytest-django wraps each test in never commits - so a test that merely
    calls the endpoint will see nothing queued. Tests that assert on queueing
    must run inside ``django_capture_on_commit_callbacks(execute=True)``; the
    ``on_commit`` fixture below packages that up.
    """
    with patch("listening.tasks.transcribe_exercise.delay") as delay:
        yield delay


@pytest.fixture
def on_commit(django_capture_on_commit_callbacks):
    """Run on-commit callbacks that a request registered."""
    from contextlib import contextmanager

    @contextmanager
    def _run():
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            yield callbacks

    return _run


# ----------------------------------------------------------------- the trigger


def test_upload_with_audio_queues_transcription(
    auth_client, creator, auto_start, queued, on_commit, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path

    with on_commit():
        response = auth_client(creator).post(
            LIST_URL, {"title": "Auto", "audio_file": audio()}, format="multipart"
        )

    assert response.status_code == 201
    queued.assert_called_once()
    exercise_id, audio_name = queued.call_args.args
    assert exercise_id == response.json()["id"]
    assert audio_name.endswith(".mp3")


def test_upload_without_audio_queues_nothing(auth_client, creator, auto_start, queued):
    response = auth_client(creator).post(LIST_URL, {"title": "No audio"}, format="multipart")
    assert response.status_code == 201
    queued.assert_not_called()


def test_replacing_audio_queues_transcription(
    auth_client, creator, auto_start, queued, on_commit, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, ready=True)

    with on_commit():
        response = auth_client(creator).patch(
            detail_url(exercise.id), {"audio_file": audio("new.mp3")}, format="multipart"
        )

    assert response.status_code == 200
    queued.assert_called_once()


def test_editing_metadata_alone_queues_nothing(auth_client, creator, auto_start, queued):
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    auth_client(creator).patch(detail_url(exercise.id), {"title": "Renamed"}, format="json")

    queued.assert_not_called()


def test_auto_start_can_be_disabled(auth_client, creator, queued, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.TRANSCRIPTION_AUTO_START = False

    auth_client(creator).post(
        LIST_URL, {"title": "Manual only", "audio_file": audio()}, format="multipart"
    )
    queued.assert_not_called()


def test_the_queued_name_is_the_new_file_not_the_old_one(
    auth_client, creator, auto_start, queued, on_commit, tmp_path, settings
):
    """The stale-run guard is only useful if the name captured is current."""
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    original_name = exercise.audio_file.name

    with on_commit():
        auth_client(creator).patch(
            detail_url(exercise.id), {"audio_file": audio("replacement.mp3")}, format="multipart"
        )

    _, queued_name = queued.call_args.args
    assert queued_name != original_name
    exercise.refresh_from_db()
    assert queued_name == exercise.audio_file.name


def test_nothing_is_queued_when_the_transaction_rolls_back(
    auto_start, queued, creator, on_commit, tmp_path, settings
):
    """on_commit, not delay: a rolled-back upload must not leave a task behind.

    Asserting on the captured callback list rather than on `queued` - with
    on_commit the callback is registered but discarded on rollback, so checking
    the mock alone would pass even if the trigger were wrong.
    """
    settings.MEDIA_ROOT = tmp_path

    class Rollback(Exception):
        pass

    with on_commit() as callbacks:
        with pytest.raises(Rollback):
            with transaction.atomic():
                create_exercise(owner=creator, title="Doomed", audio_file=audio())
                raise Rollback

    assert callbacks == []
    queued.assert_not_called()


def test_a_committed_upload_registers_exactly_one_callback(
    auto_start, queued, creator, on_commit, tmp_path, settings
):
    """The counterpart to the rollback case: a good upload does queue."""
    settings.MEDIA_ROOT = tmp_path

    with on_commit() as callbacks:
        create_exercise(owner=creator, title="Fine", audio_file=audio())

    assert len(callbacks) == 1
    queued.assert_called_once()


def test_schedule_reports_whether_it_queued(creator, auto_start, queued, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    with_audio = ExerciseFactory(owner=creator, uploaded=True)
    without_audio = ExerciseFactory(owner=creator)

    assert schedule_transcription(with_audio) is True
    assert schedule_transcription(without_audio) is False


# ------------------------------------------------------------- transcribe/


def test_owner_can_request_transcription(auth_client, creator, auto_start, queued, on_commit):
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    with on_commit():
        response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 202
    assert response.json()["status"] == ExerciseStatus.UPLOADED
    queued.assert_called_once()


def test_transcribe_reruns_after_a_failure(auth_client, creator, auto_start, queued, on_commit):
    exercise = ExerciseFactory(owner=creator, uploaded=True, status=ExerciseStatus.FAILED)
    exercise.processing_error = "provider timed out"
    exercise.save()

    with on_commit():
        response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 202
    queued.assert_called_once()


def test_transcribe_without_audio_is_refused(auth_client, creator, auto_start, queued):
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 409
    assert response.json()["extra"]["reasons"] == ["missing_audio"]
    queued.assert_not_called()


def test_transcribe_while_processing_is_refused(auth_client, creator, auto_start, queued):
    exercise = ExerciseFactory(owner=creator, uploaded=True, status=ExerciseStatus.PROCESSING)

    response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_PROCESSING"
    queued.assert_not_called()


def test_transcribe_a_published_exercise_is_refused(auth_client, creator, auto_start, queued):
    exercise = ExerciseFactory(owner=creator, published=True)
    SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 409
    assert response.json()["extra"]["reasons"] == ["exercise_is_published"]
    queued.assert_not_called()


def test_transcribe_reports_when_the_feature_is_disabled(auth_client, creator, queued, settings):
    settings.TRANSCRIPTION_AUTO_START = False
    exercise = ExerciseFactory(owner=creator, uploaded=True)

    response = auth_client(creator).post(transcribe_url(exercise.id))

    assert response.status_code == 409
    assert response.json()["extra"]["reasons"] == ["transcription_disabled"]


def test_non_owner_cannot_request_transcription(auth_client, creator, auto_start, queued):
    exercise = ExerciseFactory(owner=CreatorFactory(), published=True)
    assert auth_client(creator).post(transcribe_url(exercise.id)).status_code == 403
    queued.assert_not_called()


def test_student_cannot_request_transcription(auth_client, student, auto_start, queued):
    exercise = ExerciseFactory(published=True)
    assert auth_client(student).post(transcribe_url(exercise.id)).status_code == 403


def test_transcribe_requires_authentication(api_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    assert api_client.post(transcribe_url(exercise.id)).status_code == 401


# ----------------------------------------------------------------- status/


def test_owner_sees_transcription_status(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True, status=ExerciseStatus.PROCESSING)

    body = auth_client(creator).get(status_url(exercise.id)).json()

    assert body["id"] == exercise.id
    assert body["status"] == ExerciseStatus.PROCESSING
    assert body["segment_count"] == 0
    assert body["processing_error"] == ""


def test_status_reports_the_failure_reason(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True, status=ExerciseStatus.FAILED)
    exercise.processing_error = "Audio is 41.2 MB, above the 25 MB limit."
    exercise.save()

    body = auth_client(creator).get(status_url(exercise.id)).json()
    assert body["status"] == ExerciseStatus.FAILED
    assert "25 MB" in body["processing_error"]


def test_admin_can_see_any_status(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True)
    assert auth_client(AdminFactory()).get(status_url(exercise.id)).status_code == 200


def test_student_cannot_see_status_of_a_published_exercise(auth_client, student):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(student).get(status_url(exercise.id))
    assert response.status_code == 403


def test_status_of_an_invisible_draft_is_404(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), uploaded=True)
    assert auth_client(creator).get(status_url(exercise.id)).status_code == 404


# ------------------------------------------------- diagnostics on the detail


def test_owner_detail_includes_processing_diagnostics(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, uploaded=True, status=ExerciseStatus.FAILED)
    exercise.processing_error = "provider rejected the audio"
    exercise.save()

    body = auth_client(creator).get(detail_url(exercise.id)).json()

    assert body["processing_error"] == "provider rejected the audio"
    assert "processing_started_at" in body
    assert "transcription_provider" in body


def test_student_detail_hides_processing_diagnostics(auth_client, student):
    exercise = ExerciseFactory(published=True, status=ExerciseStatus.READY)
    SegmentFactory(exercise=exercise, sequence=1)
    exercise.processing_error = "internal provider detail"
    exercise.save()

    response = auth_client(student).get(detail_url(exercise.id))
    body = response.json()

    assert "processing_error" not in body
    assert "transcription_provider" not in body
    assert "internal provider detail" not in response.content.decode()
