import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from listening.models import ExerciseStatus, ListeningExercise
from tests.factories import AdminFactory, CreatorFactory, ExerciseFactory, SegmentFactory

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:exercise-list")


def detail_url(exercise_id) -> str:
    return reverse("v1:exercise-detail", args=[exercise_id])


def audio_upload(name="lesson.mp3", content=b"fake-audio-bytes", content_type="audio/mpeg"):
    return SimpleUploadedFile(name, content, content_type=content_type)


# ------------------------------------------------------------ authentication


def test_list_requires_authentication(api_client):
    assert api_client.get(LIST_URL).status_code == 401


def test_create_requires_authentication(api_client):
    assert api_client.post(LIST_URL, {"title": "x"}).status_code == 401


# -------------------------------------------------------------------- create


def test_creator_can_create_exercise_with_audio(auth_client, creator, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    client = auth_client(creator)

    response = client.post(
        LIST_URL,
        {
            "title": "  Accommodation Practice  ",
            "description": "IELTS Section 1 practice",
            "audio_file": audio_upload(),
        },
        format="multipart",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Accommodation Practice"  # trimmed
    assert body["status"] == ExerciseStatus.UPLOADED
    assert body["is_published"] is False
    assert body["audio_url"].endswith(".mp3")
    assert body["segment_count"] == 0
    assert body["owner"]["id"] == creator.id

    exercise = ListeningExercise.objects.get(pk=body["id"])
    assert exercise.owner == creator


def test_create_without_audio_yields_draft(auth_client, creator):
    response = auth_client(creator).post(LIST_URL, {"title": "No audio yet"}, format="multipart")
    assert response.status_code == 201
    assert response.json()["status"] == ExerciseStatus.DRAFT
    assert response.json()["audio_url"] is None


def test_student_cannot_create_exercise(auth_client, student):
    response = auth_client(student).post(LIST_URL, {"title": "Mine"}, format="multipart")
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert ListeningExercise.objects.count() == 0


def test_create_rejects_blank_title(auth_client, creator):
    response = auth_client(creator).post(LIST_URL, {"title": "   "}, format="multipart")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "title" in response.json()


def test_create_rejects_unsupported_audio_extension(auth_client, creator, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    response = auth_client(creator).post(
        LIST_URL,
        {"title": "Bad file", "audio_file": audio_upload("notes.pdf", content_type="application/pdf")},
        format="multipart",
    )
    assert response.status_code == 400
    assert "audio_file" in response.json()


def test_create_rejects_oversized_audio(auth_client, creator, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.MAX_AUDIO_FILE_SIZE_MB = 1
    oversized = audio_upload(content=b"x" * (2 * 1024 * 1024))

    response = auth_client(creator).post(
        LIST_URL, {"title": "Too big", "audio_file": oversized}, format="multipart"
    )
    assert response.status_code == 400
    assert "too large" in str(response.json()["audio_file"]).lower()


def test_create_rejects_unsupported_language(auth_client, creator):
    response = auth_client(creator).post(
        LIST_URL, {"title": "Français", "language": "fr"}, format="multipart"
    )
    assert response.status_code == 400
    assert "language" in response.json()


def test_create_ignores_client_supplied_status_and_publication(auth_client, creator):
    response = auth_client(creator).post(
        LIST_URL,
        {"title": "Sneaky", "status": "ready", "is_published": "true"},
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["status"] == ExerciseStatus.DRAFT
    assert response.json()["is_published"] is False


# ---------------------------------------------------------------------- list


def test_student_sees_only_published_exercises(auth_client, student):
    published = ExerciseFactory(published=True)
    ExerciseFactory()  # someone's draft
    ExerciseFactory(ready=True)  # ready but unpublished

    body = auth_client(student).get(LIST_URL).json()
    assert [row["id"] for row in body["results"]] == [published.id]


def test_creator_sees_published_plus_their_own(auth_client, creator):
    own_draft = ExerciseFactory(owner=creator)
    other_published = ExerciseFactory(published=True)
    ExerciseFactory()  # another creator's draft - invisible

    body = auth_client(creator).get(LIST_URL).json()
    assert {row["id"] for row in body["results"]} == {own_draft.id, other_published.id}


def test_admin_sees_everything(auth_client):
    admin = AdminFactory()
    a = ExerciseFactory()
    b = ExerciseFactory(published=True)

    body = auth_client(admin).get(LIST_URL).json()
    assert {row["id"] for row in body["results"]} == {a.id, b.id}


def test_list_never_includes_segments_or_description(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=exercise, sequence=1, text="secret transcript")

    body = auth_client(creator).get(LIST_URL).json()
    row = body["results"][0]
    assert "segments" not in row
    assert "description" not in row
    assert "secret transcript" not in str(body)


def test_list_reports_segment_count(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    SegmentFactory(exercise=exercise, sequence=1)
    SegmentFactory(exercise=exercise, sequence=2)

    row = auth_client(creator).get(LIST_URL).json()["results"][0]
    assert row["segment_count"] == 2


def test_list_is_ordered_newest_first(auth_client, creator):
    first = ExerciseFactory(owner=creator)
    second = ExerciseFactory(owner=creator)
    body = auth_client(creator).get(LIST_URL).json()
    assert [row["id"] for row in body["results"]] == [second.id, first.id]


# ------------------------------------------------------------------- filters


def test_filter_by_status(auth_client, creator):
    draft = ExerciseFactory(owner=creator)
    ExerciseFactory(owner=creator, ready=True)

    body = auth_client(creator).get(LIST_URL, {"status": "draft"}).json()
    assert [row["id"] for row in body["results"]] == [draft.id]


def test_filter_by_is_published(auth_client, creator):
    published = ExerciseFactory(owner=creator, published=True)
    ExerciseFactory(owner=creator)

    body = auth_client(creator).get(LIST_URL, {"is_published": "true"}).json()
    assert [row["id"] for row in body["results"]] == [published.id]


def test_filter_by_owner(auth_client, creator):
    other = CreatorFactory()
    ExerciseFactory(owner=creator)
    theirs = ExerciseFactory(owner=other, published=True)

    body = auth_client(creator).get(LIST_URL, {"owner": other.id}).json()
    assert [row["id"] for row in body["results"]] == [theirs.id]


def test_search_matches_title_and_description(auth_client, creator):
    match = ExerciseFactory(owner=creator, title="Accommodation Booking")
    ExerciseFactory(owner=creator, title="Library Tour", description="unrelated")

    body = auth_client(creator).get(LIST_URL, {"search": "accommodation"}).json()
    assert [row["id"] for row in body["results"]] == [match.id]


def test_ordering_by_title(auth_client, creator):
    b = ExerciseFactory(owner=creator, title="Bravo")
    a = ExerciseFactory(owner=creator, title="Alpha")

    body = auth_client(creator).get(LIST_URL, {"ordering": "title"}).json()
    assert [row["id"] for row in body["results"]] == [a.id, b.id]


def test_invalid_filter_value_is_rejected(auth_client, creator):
    response = auth_client(creator).get(LIST_URL, {"status": "nonsense"})
    assert response.status_code == 400


# ------------------------------------------------------------------ retrieve


def test_owner_sees_segments_in_detail(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=exercise, sequence=1, text="the transcript")

    body = auth_client(creator).get(detail_url(exercise.id)).json()
    assert body["segments"][0]["text"] == "the transcript"
    assert body["description"] == exercise.description


def test_admin_sees_segments_in_detail(auth_client):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, text="the transcript")

    body = auth_client(AdminFactory()).get(detail_url(exercise.id)).json()
    assert body["segments"][0]["text"] == "the transcript"


def test_student_detail_omits_segments_entirely(auth_client, student):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=1, text="hidden transcript")

    response = auth_client(student).get(detail_url(exercise.id))
    assert response.status_code == 200
    assert "segments" not in response.json()
    assert "hidden transcript" not in response.content.decode()


def test_student_cannot_retrieve_unpublished_exercise(auth_client, student):
    exercise = ExerciseFactory(ready=True)
    response = auth_client(student).get(detail_url(exercise.id))
    assert response.status_code == 404


def test_creator_cannot_retrieve_another_creators_draft(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory())
    response = auth_client(creator).get(detail_url(exercise.id))
    assert response.status_code == 404


def test_retrieve_missing_exercise_returns_404(auth_client, creator):
    assert auth_client(creator).get(detail_url(999999)).status_code == 404


# -------------------------------------------------------------------- update


def test_owner_can_patch_metadata(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    response = auth_client(creator).patch(
        detail_url(exercise.id), {"title": "Renamed"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    exercise.refresh_from_db()
    assert exercise.title == "Renamed"


def test_patch_cannot_set_publication_state(auth_client, creator):
    exercise = ExerciseFactory(owner=creator, ready=True)
    response = auth_client(creator).patch(
        detail_url(exercise.id), {"is_published": True, "status": "ready"}, format="json"
    )
    assert response.status_code == 200
    exercise.refresh_from_db()
    assert exercise.is_published is False


def test_replacing_audio_resets_derived_state(auth_client, creator, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, ready=True)

    response = auth_client(creator).patch(
        detail_url(exercise.id), {"audio_file": audio_upload("new.mp3")}, format="multipart"
    )

    assert response.status_code == 200
    exercise.refresh_from_db()
    assert exercise.status == ExerciseStatus.UPLOADED
    assert exercise.duration is None


def test_cannot_replace_audio_while_published(auth_client, creator, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    exercise = ExerciseFactory(owner=creator, published=True)

    response = auth_client(creator).patch(
        detail_url(exercise.id), {"audio_file": audio_upload("new.mp3")}, format="multipart"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_NOT_READY"
    assert response.json()["extra"]["reasons"] == ["exercise_is_published"]


def test_non_owner_cannot_patch_published_exercise(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), published=True)
    response = auth_client(creator).patch(
        detail_url(exercise.id), {"title": "Hijacked"}, format="json"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_student_cannot_patch_published_exercise(auth_client, student):
    exercise = ExerciseFactory(published=True)
    response = auth_client(student).patch(
        detail_url(exercise.id), {"title": "Hijacked"}, format="json"
    )
    assert response.status_code == 403


def test_admin_can_patch_any_exercise(auth_client):
    exercise = ExerciseFactory(published=True)
    response = auth_client(AdminFactory()).patch(
        detail_url(exercise.id), {"title": "Moderated"}, format="json"
    )
    assert response.status_code == 200


def test_put_is_not_allowed(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    response = auth_client(creator).put(detail_url(exercise.id), {"title": "x"}, format="json")
    assert response.status_code == 405


# -------------------------------------------------------------------- delete


def test_owner_can_delete_exercise_and_segments(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)
    SegmentFactory(exercise=exercise, sequence=1)

    response = auth_client(creator).delete(detail_url(exercise.id))
    assert response.status_code == 204
    assert not ListeningExercise.objects.filter(pk=exercise.id).exists()


def test_non_owner_cannot_delete(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory(), published=True)
    assert auth_client(creator).delete(detail_url(exercise.id)).status_code == 403
    assert ListeningExercise.objects.filter(pk=exercise.id).exists()


def test_creator_cannot_delete_invisible_draft(auth_client, creator):
    exercise = ExerciseFactory(owner=CreatorFactory())
    assert auth_client(creator).delete(detail_url(exercise.id)).status_code == 404


# -------------------------------------------------------- query optimization


def test_list_query_count_is_constant(auth_client, creator, django_assert_num_queries):
    for _ in range(3):
        exercise = ExerciseFactory(owner=creator)
        SegmentFactory(exercise=exercise, sequence=1)

    client = auth_client(creator)
    with django_assert_num_queries(2):  # count + page query (owner joined, count annotated)
        client.get(LIST_URL)

    for _ in range(5):
        ExerciseFactory(owner=creator)

    with django_assert_num_queries(2):
        client.get(LIST_URL)
