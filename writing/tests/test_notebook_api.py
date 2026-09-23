import pytest
from django.urls import reverse

from writing.models import WritingNotebook, WritingPage

pytestmark = pytest.mark.django_db


def notebook_url(workbook_id):
    return reverse("v1:writing-notebook", args=[workbook_id])


def complete_url(workbook_id):
    return reverse("v1:writing-notebook-complete", args=[workbook_id])


def page_url(workbook_id, page_number):
    return reverse("v1:writing-notebook-page", args=[workbook_id, page_number])


NOTEBOOK_LIST_URL = reverse("v1:writing-notebook-list")


# --------------------------------------------------------------------------
# Opening a notebook
# --------------------------------------------------------------------------


def test_opening_a_workbook_creates_the_notebook(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)

    response = auth_client(student).get(notebook_url(workbook.id))

    assert response.status_code == 200
    body = response.json()
    assert body["page_count"] == workbook.page_count
    assert body["last_page"] == 1
    assert body["pages"] == []
    assert body["pages_started"] == 0
    assert body["completed_at"] is None
    assert body["pdf_url"]
    assert WritingNotebook.objects.filter(user=student, exercise=workbook).count() == 1


def test_prompt_only_notebook_has_one_page_and_no_pdf(
    auth_client, student, workbook_factory
):
    workbook = workbook_factory(published=True, without_pdf=True)

    opened = auth_client(student).get(notebook_url(workbook.id)).json()
    assert opened["page_count"] == 1
    assert opened["pdf_url"] is None

    saved = auth_client(student).put(
        page_url(workbook.id, 1), {"body": "In my opinion,"}, format="json"
    )
    assert saved.status_code == 200
    assert saved.json()["word_count"] == 3

    beyond = auth_client(student).put(
        page_url(workbook.id, 2), {"body": "x"}, format="json"
    )
    assert beyond.status_code == 400
    assert beyond.json()["code"] == "PAGE_OUT_OF_RANGE"


def test_reopening_does_not_create_a_second_notebook(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)
    client = auth_client(student)

    first = client.get(notebook_url(workbook.id)).json()["id"]
    second = client.get(notebook_url(workbook.id)).json()["id"]

    assert first == second
    assert WritingNotebook.objects.count() == 1


def test_notebook_on_someone_elses_draft_is_404(auth_client, student, workbook_factory):
    workbook = workbook_factory()
    assert auth_client(student).get(notebook_url(workbook.id)).status_code == 404
    assert not WritingNotebook.objects.exists()


def test_owner_can_keep_a_notebook_on_their_own_unpublished_workbook(
    auth_client, student, workbook_factory
):
    workbook = workbook_factory(owner=student)
    assert auth_client(student).get(notebook_url(workbook.id)).status_code == 200


def test_anonymous_cannot_open_a_notebook(api_client, workbook_factory):
    workbook = workbook_factory(published=True)
    assert api_client.get(notebook_url(workbook.id)).status_code == 401


# --------------------------------------------------------------------------
# Saving pages
# --------------------------------------------------------------------------


def test_put_saves_a_page_and_moves_the_bookmark(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True, page_count=10)

    response = auth_client(student).put(
        page_url(workbook.id, 3), {"body": "one two three"}, format="json"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page_number"] == 3
    assert body["body"] == "one two three"
    assert body["word_count"] == 3

    notebook = WritingNotebook.objects.get(user=student, exercise=workbook)
    assert notebook.last_page == 3


def test_repeated_saves_upsert_rather_than_accumulate(auth_client, student, workbook_factory):
    """The autosave contract: a debounced client must not build a history."""
    workbook = workbook_factory(published=True)
    client = auth_client(student)

    for text in ("draft", "draft two", "final answer here"):
        response = client.put(page_url(workbook.id, 2), {"body": text}, format="json")
        assert response.status_code == 200

    assert WritingPage.objects.count() == 1
    page = WritingPage.objects.get()
    assert page.body == "final answer here"
    assert page.word_count == 3


def test_empty_body_clears_the_page(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)
    client = auth_client(student)
    client.put(page_url(workbook.id, 1), {"body": "written"}, format="json")

    response = client.put(page_url(workbook.id, 1), {"body": ""}, format="json")

    assert response.status_code == 200
    assert response.json()["body"] == ""
    assert response.json()["word_count"] == 0


def test_page_beyond_the_workbook_is_rejected(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True, page_count=10)

    response = auth_client(student).put(
        page_url(workbook.id, 99), {"body": "x"}, format="json"
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "PAGE_OUT_OF_RANGE"
    assert body["extra"]["page_count"] == 10
    assert not WritingPage.objects.exists()


def test_body_longer_than_the_limit_is_rejected(auth_client, student, workbook_factory, settings):
    settings.MAX_WRITING_PAGE_LENGTH = 10
    workbook = workbook_factory(published=True)

    response = auth_client(student).put(
        page_url(workbook.id, 1), {"body": "x" * 11}, format="json"
    )
    assert response.status_code == 400
    assert "body" in response.json()


def test_get_on_an_unwritten_page_returns_an_empty_body(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)

    response = auth_client(student).get(page_url(workbook.id, 4))

    assert response.status_code == 200
    assert response.json() == {
        "page_number": 4,
        "body": "",
        "word_count": 0,
        "updated_at": None,
    }
    assert not WritingPage.objects.exists()


def test_get_on_an_out_of_range_page_is_rejected(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True, page_count=3)
    response = auth_client(student).get(page_url(workbook.id, 4))
    assert response.status_code == 400
    assert response.json()["code"] == "PAGE_OUT_OF_RANGE"


def test_notebook_lists_saved_pages_in_order(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True, page_count=10)
    client = auth_client(student)
    client.put(page_url(workbook.id, 5), {"body": "five"}, format="json")
    client.put(page_url(workbook.id, 1), {"body": "one"}, format="json")
    client.put(page_url(workbook.id, 3), {"body": ""}, format="json")

    body = client.get(notebook_url(workbook.id)).json()

    assert [page["page_number"] for page in body["pages"]] == [1, 3, 5]
    assert body["pages_started"] == 2  # the cleared page does not count
    assert body["last_page"] == 3


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------


def test_a_learner_never_sees_another_learners_writing(
    auth_client, student, creator, workbook_factory, notebook_factory, page_factory
):
    workbook = workbook_factory(published=True)
    theirs = notebook_factory(user=creator, exercise=workbook)
    page_factory(notebook=theirs, page_number=1, body="their private writing")

    body = auth_client(student).get(notebook_url(workbook.id)).json()

    assert body["pages"] == []
    assert body["id"] != theirs.id


def test_an_admin_does_not_get_a_back_door_into_a_notebook(
    auth_client, admin_user, student, workbook_factory, notebook_factory, page_factory
):
    """Deliberately stricter than listening: a notebook is personal writing."""
    workbook = workbook_factory(published=True)
    theirs = notebook_factory(user=student, exercise=workbook)
    page_factory(notebook=theirs, page_number=1, body="their private writing")

    body = auth_client(admin_user).get(notebook_url(workbook.id)).json()

    assert body["pages"] == []
    assert body["id"] != theirs.id


def test_the_workbook_owner_cannot_read_a_learners_pages(
    auth_client, creator, student, workbook_factory, notebook_factory, page_factory
):
    workbook = workbook_factory(owner=creator, published=True)
    theirs = notebook_factory(user=student, exercise=workbook)
    page_factory(notebook=theirs, page_number=1, body="their private writing")

    body = auth_client(creator).get(page_url(workbook.id, 1)).json()

    assert body["body"] == ""


# --------------------------------------------------------------------------
# Completion
# --------------------------------------------------------------------------


def test_marking_complete_stamps_the_notebook(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)

    response = auth_client(student).post(complete_url(workbook.id))

    assert response.status_code == 200
    assert response.json()["completed_at"] is not None


def test_re_completing_keeps_the_original_timestamp(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)
    client = auth_client(student)

    first = client.post(complete_url(workbook.id)).json()["completed_at"]
    second = client.post(complete_url(workbook.id)).json()["completed_at"]

    assert first == second


def test_completion_can_be_undone(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True)
    client = auth_client(student)
    client.post(complete_url(workbook.id))

    response = client.delete(complete_url(workbook.id))

    assert response.status_code == 200
    assert response.json()["completed_at"] is None


def test_completion_is_never_inferred_from_pages(auth_client, student, workbook_factory):
    workbook = workbook_factory(published=True, page_count=1)
    client = auth_client(student)
    client.put(page_url(workbook.id, 1), {"body": "every page done"}, format="json")

    body = client.get(notebook_url(workbook.id)).json()
    assert body["pages_started"] == body["page_count"]
    assert body["completed_at"] is None


# --------------------------------------------------------------------------
# Continue writing
# --------------------------------------------------------------------------


def test_notebook_list_shows_only_the_callers_notebooks(
    auth_client, student, creator, workbook_factory, notebook_factory
):
    mine = notebook_factory(user=student, exercise=workbook_factory(published=True))
    notebook_factory(user=creator, exercise=workbook_factory(published=True))

    rows = auth_client(student).get(NOTEBOOK_LIST_URL).json()["results"]

    assert [row["id"] for row in rows] == [mine.id]


def test_notebook_list_carries_progress_and_no_page_bodies(
    auth_client, student, workbook_factory, notebook_factory, page_factory
):
    workbook = workbook_factory(published=True, page_count=8, title="Unit 3")
    notebook = notebook_factory(user=student, exercise=workbook, last_page=6)
    page_factory(notebook=notebook, page_number=1, body="written")
    page_factory(notebook=notebook, page_number=2, body="also written")
    page_factory(notebook=notebook, page_number=3, body="")

    row = auth_client(student).get(NOTEBOOK_LIST_URL).json()["results"][0]

    assert row["title"] == "Unit 3"
    assert row["page_count"] == 8
    assert row["pages_started"] == 2
    assert row["last_page"] == 6
    assert "pages" not in row


def test_notebook_list_is_most_recently_touched_first(
    auth_client, student, workbook_factory
):
    older = workbook_factory(published=True)
    newer = workbook_factory(published=True)
    client = auth_client(student)
    client.get(notebook_url(older.id))
    client.get(notebook_url(newer.id))
    client.put(page_url(older.id, 1), {"body": "back to this one"}, format="json")

    rows = client.get(NOTEBOOK_LIST_URL).json()["results"]

    assert [row["exercise_id"] for row in rows] == [older.id, newer.id]
