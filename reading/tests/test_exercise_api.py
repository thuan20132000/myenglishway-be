import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from reading.models import ReadingExercise

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:reading-exercise-list")


def detail_url(passage_id):
    return reverse("v1:reading-exercise-detail", args=[passage_id])


def audio_upload(name="lecture.mp3", content=b"fake-audio-bytes", content_type="audio/mpeg"):
    return SimpleUploadedFile(name, content, content_type=content_type)


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_student_can_create_a_passage(auth_client, student):
    """The point of this domain: paste your own transcript. Not creator-gated."""
    response = auth_client(student).post(
        LIST_URL,
        {
            "title": "  AI in education  ",
            "source": "Cambridge IELTS 18 Test 1 Section 4",
            "body": "The development of artificial intelligence has changed many aspects.",
        },
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "AI in education"
    assert body["word_count"] == 9
    assert body["suggested_wpm"] == 250
    assert body["is_published"] is False
    assert body["audio_url"] is None
    assert "The development of artificial intelligence" in body["body"]

    passage = ReadingExercise.objects.get(pk=body["id"])
    assert passage.owner_id == student.id


def test_word_count_is_derived_from_the_body_not_the_request(auth_client, student):
    response = auth_client(student).post(
        LIST_URL,
        {"title": "Count me", "body": "one two three", "word_count": 999},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["word_count"] == 3


def test_empty_body_is_rejected(auth_client, student):
    response = auth_client(student).post(
        LIST_URL, {"title": "Empty", "body": "   "}, format="json"
    )
    assert response.status_code == 400
    assert "body" in response.json()


def test_punctuation_only_body_is_rejected(auth_client, student):
    response = auth_client(student).post(
        LIST_URL, {"title": "Dots", "body": "..."}, format="json"
    )
    assert response.status_code == 400
    assert "body" in response.json()


def test_suggested_wpm_out_of_range_is_rejected(auth_client, student):
    response = auth_client(student).post(
        LIST_URL,
        {"title": "Too fast", "body": "one two three", "suggested_wpm": 900},
        format="json",
    )
    assert response.status_code == 400
    assert "suggested_wpm" in response.json()


def test_anonymous_cannot_create(api_client):
    response = api_client.post(LIST_URL, {"title": "x", "body": "one two"}, format="json")
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_list_shows_own_and_published_only(auth_client, student, passage_factory):
    mine = passage_factory(owner=student)
    published = passage_factory(published=True)
    passage_factory()  # someone else's draft

    ids = {row["id"] for row in auth_client(student).get(LIST_URL).json()["results"]}
    assert ids == {mine.id, published.id}


def test_admin_sees_every_passage(auth_client, admin_user, passage_factory):
    passage_factory()
    passage_factory(published=True)
    assert auth_client(admin_user).get(LIST_URL).json()["count"] == 2


def test_mine_filter_narrows_to_own_uploads(auth_client, student, passage_factory):
    mine = passage_factory(owner=student)
    passage_factory(published=True)

    rows = auth_client(student).get(LIST_URL, {"mine": "true"}).json()["results"]
    assert [row["id"] for row in rows] == [mine.id]


def test_someone_elses_draft_is_404_not_403(auth_client, student, passage_factory):
    other = passage_factory()
    assert auth_client(student).get(detail_url(other.id)).status_code == 404


def test_published_passage_is_readable_by_anyone(auth_client, student, passage_factory):
    published = passage_factory(published=True)
    response = auth_client(student).get(detail_url(published.id))
    assert response.status_code == 200
    assert "development of artificial intelligence" in response.json()["body"]


def test_list_rows_omit_body_and_audio_url(auth_client, student, passage_factory):
    passage_factory(owner=student)
    row = auth_client(student).get(LIST_URL).json()["results"][0]
    assert "body" not in row
    assert "audio_url" not in row


def test_detail_includes_body(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    body = auth_client(student).get(detail_url(passage.id)).json()
    assert body["body"] == passage.body
    assert body["word_count"] == passage.word_count


def test_list_reports_the_callers_own_sessions(
    auth_client, student, passage_factory, session_factory
):
    passage = passage_factory(owner=student)
    session_factory(user=student, exercise=passage, finished=True, target_wpm=200)

    row = auth_client(student).get(LIST_URL).json()["results"][0]
    assert row["session_count"] == 1
    assert row["last_target_wpm"] == 200
    assert row["last_actual_wpm"] is not None


def test_progress_is_per_caller_not_global(
    auth_client, student, creator, passage_factory, session_factory
):
    passage = passage_factory(published=True)
    session_factory(user=creator, exercise=passage, finished=True)

    row = auth_client(student).get(detail_url(passage.id)).json()
    assert row["session_count"] == 0
    assert row["last_actual_wpm"] is None
    assert row["last_target_wpm"] is None


def test_list_does_not_scale_queries_with_rows(
    auth_client, student, passage_factory, django_assert_num_queries
):
    for _ in range(3):
        passage_factory(owner=student)
    client = auth_client(student)

    with django_assert_num_queries(2):  # count + page
        assert client.get(LIST_URL).status_code == 200


# --------------------------------------------------------------------------
# Update and delete
# --------------------------------------------------------------------------


def test_owner_can_rename(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    response = auth_client(student).patch(
        detail_url(passage.id), {"title": "Renamed"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


def test_non_owner_cannot_edit_a_published_passage(auth_client, student, passage_factory):
    passage = passage_factory(published=True)
    response = auth_client(student).patch(
        detail_url(passage.id), {"title": "Hijacked"}, format="json"
    )
    assert response.status_code == 403


def test_editing_body_recomputes_word_count(auth_client, student, passage_factory):
    passage = passage_factory(owner=student, body="one two")
    response = auth_client(student).patch(
        detail_url(passage.id), {"body": "one two three four"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["word_count"] == 4


def test_owner_can_attach_audio(auth_client, student, passage_factory, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    passage = passage_factory(owner=student)

    response = auth_client(student).patch(
        detail_url(passage.id), {"audio_file": audio_upload()}, format="multipart"
    )

    assert response.status_code == 200
    assert response.json()["has_audio"] is True
    assert response.json()["audio_url"].endswith(".mp3")
    passage.refresh_from_db()
    assert passage.audio_file.name.startswith(f"reading-audio/{student.id}/")


def test_replacing_audio_drops_the_old_file(
    auth_client, student, passage_factory, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    passage = passage_factory(owner=student, with_audio=True)
    old_path = passage.audio_file.path

    response = auth_client(student).patch(
        detail_url(passage.id),
        {"audio_file": audio_upload("new.mp3", b"new-bytes")},
        format="multipart",
    )

    assert response.status_code == 200
    passage.refresh_from_db()
    assert passage.audio_file.path != old_path
    assert not tmp_path.joinpath(old_path).exists()


def test_invalid_audio_is_rejected(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    response = auth_client(student).patch(
        detail_url(passage.id),
        {"audio_file": audio_upload("notes.pdf", content_type="application/pdf")},
        format="multipart",
    )
    assert response.status_code == 400
    assert "audio_file" in response.json()


def test_oversize_audio_is_rejected(auth_client, student, passage_factory, settings):
    settings.MAX_AUDIO_FILE_SIZE_MB = 1
    passage = passage_factory(owner=student)
    oversize = audio_upload(content=b"x" * (2 * 1024 * 1024))
    response = auth_client(student).patch(
        detail_url(passage.id), {"audio_file": oversize}, format="multipart"
    )
    assert response.status_code == 400
    assert "too large" in str(response.json()["audio_file"]).lower()


def test_owner_can_delete_audio(auth_client, student, passage_factory, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    passage = passage_factory(owner=student, with_audio=True)
    audio_url = reverse("v1:reading-exercise-audio", args=[passage.id])

    assert auth_client(student).delete(audio_url).status_code == 204
    passage.refresh_from_db()
    assert not passage.audio_file
    # Idempotent.
    assert auth_client(student).delete(audio_url).status_code == 204


def test_owner_can_delete(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    assert auth_client(student).delete(detail_url(passage.id)).status_code == 204
    assert not ReadingExercise.objects.filter(pk=passage.id).exists()


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def publish_url(passage_id):
    return reverse("v1:reading-exercise-publish", args=[passage_id])


def unpublish_url(passage_id):
    return reverse("v1:reading-exercise-unpublish", args=[passage_id])


def test_creator_can_publish_their_passage(auth_client, creator, passage_factory):
    passage = passage_factory(owner=creator)
    response = auth_client(creator).post(publish_url(passage.id))

    assert response.status_code == 200
    body = response.json()
    assert body["is_published"] is True
    assert body["published_at"] is not None


def test_student_cannot_publish_even_their_own_passage(auth_client, student, passage_factory):
    passage = passage_factory(owner=student)
    assert auth_client(student).post(publish_url(passage.id)).status_code == 403


def test_creator_cannot_unpublish_someone_elses_published_passage(
    auth_client, creator, passage_factory
):
    passage = passage_factory(published=True)
    assert auth_client(creator).post(unpublish_url(passage.id)).status_code == 403


def test_publishing_twice_conflicts(auth_client, creator, passage_factory):
    passage = passage_factory(owner=creator, published=True)
    response = auth_client(creator).post(publish_url(passage.id))
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_ALREADY_PUBLISHED"


def test_unpublishing_is_idempotent(auth_client, creator, passage_factory):
    passage = passage_factory(owner=creator)
    for _ in range(2):
        response = auth_client(creator).post(unpublish_url(passage.id))
        assert response.status_code == 200
        assert response.json()["is_published"] is False


def test_unpublished_passage_keeps_learner_sessions(
    auth_client, creator, passage_factory, session_factory
):
    passage = passage_factory(owner=creator, published=True)
    session = session_factory(exercise=passage)

    auth_client(creator).post(unpublish_url(passage.id))

    session.refresh_from_db()
    assert session.pk is not None
