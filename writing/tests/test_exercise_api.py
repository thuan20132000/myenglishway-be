import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from writing.models import WritingExercise

from .conftest import pdf_bytes, pdf_upload

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:writing-exercise-list")


def detail_url(workbook_id):
    return reverse("v1:writing-exercise-detail", args=[workbook_id])


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def test_student_can_upload_a_workbook(auth_client, student, tmp_path, settings):
    """The point of this domain: bring your own book. Not creator-gated."""
    settings.MEDIA_ROOT = tmp_path

    response = auth_client(student).post(
        LIST_URL,
        {"title": "Grammar in Use", "source": "Cambridge", "pdf_file": pdf_upload(pages=5)},
        format="multipart",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["page_count"] == 5
    assert body["is_published"] is False
    assert body["pdf_url"].endswith(".pdf")

    workbook = WritingExercise.objects.get(pk=body["id"])
    assert workbook.owner_id == student.id
    assert workbook.pdf_file.name.startswith(f"writing-pdf/{student.id}/")


def test_page_count_is_read_from_the_file_not_the_request(
    auth_client, student, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path

    response = auth_client(student).post(
        LIST_URL,
        {"title": "Workbook", "page_count": 999, "pdf_file": pdf_upload(pages=2)},
        format="multipart",
    )

    assert response.status_code == 201
    assert response.json()["page_count"] == 2


def test_student_can_create_without_a_pdf(auth_client, student):
    """A prompt in the title is enough to start writing. The PDF is optional."""
    response = auth_client(student).post(
        LIST_URL,
        {
            "title": "Task 2 — technology in education",
            "description": "Some people think computers should replace teachers.",
            "source": "Cambridge IELTS 18",
        },
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Task 2 — technology in education"
    assert body["page_count"] == 1
    assert body["pdf_url"] is None
    assert body["has_pdf"] is False
    assert body["is_published"] is False

    workbook = WritingExercise.objects.get(pk=body["id"])
    assert workbook.owner_id == student.id
    assert not workbook.pdf_file


def test_page_count_is_one_when_created_without_a_pdf(auth_client, student):
    response = auth_client(student).post(
        LIST_URL, {"title": "Blank page", "page_count": 999}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["page_count"] == 1


def test_title_is_required(auth_client, student):
    response = auth_client(student).post(LIST_URL, {"description": "No title"}, format="json")
    assert response.status_code == 400
    assert "title" in response.json()


def test_non_pdf_bytes_are_rejected(auth_client, student):
    upload = SimpleUploadedFile("workbook.pdf", b"not a pdf at all", "application/pdf")
    response = auth_client(student).post(
        LIST_URL, {"title": "Fake", "pdf_file": upload}, format="multipart"
    )
    assert response.status_code == 400
    assert "not a PDF" in str(response.json()["pdf_file"])


def test_oversize_pdf_is_rejected(auth_client, student, settings):
    settings.MAX_WRITING_PDF_FILE_SIZE_MB = 1
    oversize = b"%PDF-" + b"x" * (2 * 1024 * 1024)
    upload = SimpleUploadedFile("workbook.pdf", oversize, "application/pdf")
    response = auth_client(student).post(
        LIST_URL, {"title": "Huge", "pdf_file": upload}, format="multipart"
    )
    assert response.status_code == 400
    assert "too large" in str(response.json()["pdf_file"])


def test_listening_handout_limit_does_not_cap_workbooks(
    auth_client, student, tmp_path, settings
):
    """The two ceilings are independent: a book is not a two-page handout."""
    settings.MEDIA_ROOT = tmp_path
    settings.MAX_PDF_FILE_SIZE_MB = 0
    settings.MAX_WRITING_PDF_FILE_SIZE_MB = 60

    response = auth_client(student).post(
        LIST_URL, {"title": "Workbook", "pdf_file": pdf_upload()}, format="multipart"
    )
    assert response.status_code == 201


def test_corrupt_pdf_header_but_unreadable_body_is_rejected(auth_client, student):
    """Passes the magic-byte check, fails when pypdf actually opens it."""
    upload = SimpleUploadedFile("workbook.pdf", b"%PDF-1.4 truncated", "application/pdf")
    response = auth_client(student).post(
        LIST_URL, {"title": "Broken", "pdf_file": upload}, format="multipart"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "UNREADABLE_PDF_FILE"


def test_anonymous_cannot_create(api_client):
    response = api_client.post(LIST_URL, {"title": "x"}, format="json")
    assert response.status_code == 401


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_list_shows_own_and_published_only(auth_client, student, workbook_factory):
    mine = workbook_factory(owner=student)
    published = workbook_factory(published=True)
    workbook_factory()  # someone else's draft

    ids = {row["id"] for row in auth_client(student).get(LIST_URL).json()["results"]}
    assert ids == {mine.id, published.id}


def test_admin_sees_every_workbook(auth_client, admin_user, workbook_factory):
    workbook_factory()
    workbook_factory(published=True)
    assert auth_client(admin_user).get(LIST_URL).json()["count"] == 2


def test_mine_filter_narrows_to_own_uploads(auth_client, student, workbook_factory):
    mine = workbook_factory(owner=student)
    workbook_factory(published=True)

    rows = auth_client(student).get(LIST_URL, {"mine": "true"}).json()["results"]
    assert [row["id"] for row in rows] == [mine.id]


def test_someone_elses_draft_is_404_not_403(auth_client, student, workbook_factory):
    other = workbook_factory()
    assert auth_client(student).get(detail_url(other.id)).status_code == 404


def test_published_workbook_is_readable_by_anyone(auth_client, student, workbook_factory):
    published = workbook_factory(published=True)
    response = auth_client(student).get(detail_url(published.id))
    assert response.status_code == 200
    assert response.json()["pdf_url"]


def test_list_rows_omit_the_pdf_url(auth_client, student, workbook_factory):
    workbook_factory(owner=student)
    row = auth_client(student).get(LIST_URL).json()["results"][0]
    assert "pdf_url" not in row
    assert row["has_pdf"] is True


def test_prompt_only_list_row_reports_no_pdf(auth_client, student, workbook_factory):
    workbook_factory(owner=student, without_pdf=True)
    row = auth_client(student).get(LIST_URL).json()["results"][0]
    assert row["has_pdf"] is False
    assert row["page_count"] == 1


def test_list_reports_the_callers_own_progress(
    auth_client, student, workbook_factory, notebook_factory, page_factory
):
    workbook = workbook_factory(owner=student)
    notebook = notebook_factory(user=student, exercise=workbook, last_page=4)
    page_factory(notebook=notebook, page_number=1, body="written")
    page_factory(notebook=notebook, page_number=2, body="")  # cleared, not started

    row = auth_client(student).get(LIST_URL).json()["results"][0]
    assert row["has_notebook"] is True
    assert row["pages_started"] == 1
    assert row["last_page"] == 4


def test_progress_is_per_caller_not_global(
    auth_client, student, creator, workbook_factory, notebook_factory, page_factory
):
    workbook = workbook_factory(published=True)
    others = notebook_factory(user=creator, exercise=workbook)
    page_factory(notebook=others, page_number=1, body="their writing")

    row = auth_client(student).get(detail_url(workbook.id)).json()
    assert row["has_notebook"] is False
    assert row["pages_started"] == 0
    assert row["last_page"] is None


def test_list_does_not_scale_queries_with_rows(
    auth_client, student, workbook_factory, django_assert_num_queries
):
    for _ in range(3):
        workbook_factory(owner=student)
    client = auth_client(student)

    with django_assert_num_queries(2):  # count + page
        assert client.get(LIST_URL).status_code == 200


# --------------------------------------------------------------------------
# Update and delete
# --------------------------------------------------------------------------


def test_owner_can_rename(auth_client, student, workbook_factory):
    workbook = workbook_factory(owner=student)
    response = auth_client(student).patch(
        detail_url(workbook.id), {"title": "Renamed"}, format="multipart"
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


def test_non_owner_cannot_edit_a_published_workbook(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)
    response = auth_client(student).patch(
        detail_url(workbook.id), {"title": "Hijacked"}, format="multipart"
    )
    assert response.status_code == 403


def test_owner_can_attach_a_pdf_after_creating_without_one(
    auth_client, student, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    created = auth_client(student).post(
        LIST_URL, {"title": "Task 2"}, format="json"
    ).json()

    response = auth_client(student).patch(
        detail_url(created["id"]), {"pdf_file": pdf_upload(pages=4)}, format="multipart"
    )

    assert response.status_code == 200
    assert response.json()["page_count"] == 4
    assert response.json()["has_pdf"] is True
    assert response.json()["pdf_url"].endswith(".pdf")


def test_first_pdf_can_be_attached_after_a_notebook_has_started(
    auth_client, student, workbook_factory, notebook_factory, tmp_path, settings
):
    """Attaching the first sheet expands the page range; it is not a replacement."""
    settings.MEDIA_ROOT = tmp_path
    workbook = workbook_factory(owner=student, without_pdf=True)
    notebook_factory(exercise=workbook)

    response = auth_client(student).patch(
        detail_url(workbook.id), {"pdf_file": pdf_upload(pages=6)}, format="multipart"
    )

    assert response.status_code == 200
    assert response.json()["page_count"] == 6
    assert response.json()["has_pdf"] is True


def test_replacing_the_pdf_recounts_pages_and_drops_the_old_file(
    auth_client, student, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    created = auth_client(student).post(
        LIST_URL, {"title": "Workbook", "pdf_file": pdf_upload(pages=2)}, format="multipart"
    ).json()
    workbook = WritingExercise.objects.get(pk=created["id"])
    old_path = workbook.pdf_file.path

    response = auth_client(student).patch(
        detail_url(workbook.id), {"pdf_file": pdf_upload(pages=7)}, format="multipart"
    )

    assert response.status_code == 200
    assert response.json()["page_count"] == 7
    workbook.refresh_from_db()
    assert workbook.pdf_file.path != old_path
    assert not tmp_path.joinpath(old_path).exists()


def test_replacing_the_pdf_is_refused_once_a_learner_has_started(
    auth_client, student, workbook_factory, notebook_factory, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    workbook = workbook_factory(owner=student)
    notebook_factory(exercise=workbook)

    response = auth_client(student).patch(
        detail_url(workbook.id), {"pdf_file": pdf_upload()}, format="multipart"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "WRITING_EXERCISE_HAS_NOTEBOOKS"
    assert response.json()["extra"]["reasons"] == ["notebooks_exist"]


def test_metadata_still_editable_while_notebooks_exist(
    auth_client, student, workbook_factory, notebook_factory
):
    workbook = workbook_factory(owner=student)
    notebook_factory(exercise=workbook)

    response = auth_client(student).patch(
        detail_url(workbook.id), {"title": "Second edition"}, format="multipart"
    )
    assert response.status_code == 200


def test_owner_can_delete(auth_client, student, workbook_factory):
    workbook = workbook_factory(owner=student)
    assert auth_client(student).delete(detail_url(workbook.id)).status_code == 204
    assert not WritingExercise.objects.filter(pk=workbook.id).exists()


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def publish_url(workbook_id):
    return reverse("v1:writing-exercise-publish", args=[workbook_id])


def unpublish_url(workbook_id):
    return reverse("v1:writing-exercise-unpublish", args=[workbook_id])


def test_creator_can_publish_their_workbook(auth_client, creator, workbook_factory):
    workbook = workbook_factory(owner=creator)
    response = auth_client(creator).post(publish_url(workbook.id))

    assert response.status_code == 200
    body = response.json()
    assert body["is_published"] is True
    assert body["published_at"] is not None


def test_creator_can_publish_a_prompt_only_workbook(auth_client, creator, workbook_factory):
    workbook = workbook_factory(owner=creator, without_pdf=True)
    response = auth_client(creator).post(publish_url(workbook.id))
    assert response.status_code == 200
    assert response.json()["is_published"] is True


def test_student_cannot_publish_even_their_own_workbook(auth_client, student, workbook_factory):
    workbook = workbook_factory(owner=student)
    assert auth_client(student).post(publish_url(workbook.id)).status_code == 403


def test_creator_cannot_publish_someone_elses_published_workbook(
    auth_client, creator, workbook_factory
):
    """Published means visible, not editable - the owner check has to be explicit."""
    workbook = workbook_factory(published=True)
    assert auth_client(creator).post(unpublish_url(workbook.id)).status_code == 403


def test_publishing_twice_conflicts(auth_client, creator, workbook_factory):
    workbook = workbook_factory(owner=creator, published=True)
    response = auth_client(creator).post(publish_url(workbook.id))
    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_ALREADY_PUBLISHED"


def test_unpublishing_is_idempotent(auth_client, creator, workbook_factory):
    workbook = workbook_factory(owner=creator)
    for _ in range(2):
        response = auth_client(creator).post(unpublish_url(workbook.id))
        assert response.status_code == 200
        assert response.json()["is_published"] is False


def test_pdf_cannot_be_replaced_while_published(
    auth_client, creator, workbook_factory, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    workbook = workbook_factory(owner=creator, published=True)

    response = auth_client(creator).patch(
        detail_url(workbook.id), {"pdf_file": pdf_upload()}, format="multipart"
    )
    assert response.status_code == 409
    assert response.json()["extra"]["reasons"] == ["workbook_is_published"]


def test_unpublished_workbook_keeps_learner_notebooks(
    auth_client, creator, workbook_factory, notebook_factory
):
    workbook = workbook_factory(owner=creator, published=True)
    notebook = notebook_factory(exercise=workbook)

    auth_client(creator).post(unpublish_url(workbook.id))

    notebook.refresh_from_db()
    assert notebook.pk is not None


def test_pdf_bytes_helper_produces_a_real_pdf():
    assert pdf_bytes(3).startswith(b"%PDF-")
