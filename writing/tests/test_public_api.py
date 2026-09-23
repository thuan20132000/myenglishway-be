"""The anonymous catalogue.

Publishing shares the workbook's PDF and nothing else: there is no public
notebook route, and no published payload ever carries anyone's writing.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

PUBLIC_LIST_URL = reverse("v1:public-writing-exercise-list")


def public_detail_url(workbook_id):
    return reverse("v1:public-writing-exercise-detail", args=[workbook_id])


def test_anonymous_visitor_sees_published_workbooks(api_client, workbook_factory):
    published = workbook_factory(published=True)
    workbook_factory()  # a draft

    body = api_client.get(PUBLIC_LIST_URL).json()

    assert [row["id"] for row in body["results"]] == [published.id]


def test_unpublished_workbook_is_404_not_403(api_client, workbook_factory):
    draft = workbook_factory()
    assert api_client.get(public_detail_url(draft.id)).status_code == 404


def test_public_detail_carries_the_pdf_url(api_client, workbook_factory):
    published = workbook_factory(published=True)

    body = api_client.get(public_detail_url(published.id)).json()

    assert body["pdf_url"].endswith(".pdf")
    assert body["page_count"] == published.page_count
    assert body["has_pdf"] is True


def test_public_prompt_only_workbook_has_no_pdf(api_client, workbook_factory):
    published = workbook_factory(published=True, without_pdf=True)

    body = api_client.get(public_detail_url(published.id)).json()

    assert body["pdf_url"] is None
    assert body["has_pdf"] is False
    assert body["page_count"] == 1


def test_public_rows_report_no_progress(
    api_client, workbook_factory, notebook_factory, page_factory
):
    """The shared serializers ask for annotations an anonymous caller has none of."""
    published = workbook_factory(published=True)
    notebook = notebook_factory(exercise=published)
    page_factory(notebook=notebook, page_number=1, body="somebody's writing")

    row = api_client.get(PUBLIC_LIST_URL).json()["results"][0]

    assert row["has_notebook"] is False
    assert row["pages_started"] == 0
    assert row["last_page"] is None


def test_public_surface_is_read_only(api_client, workbook_factory):
    published = workbook_factory(published=True)
    assert api_client.post(PUBLIC_LIST_URL, {"title": "x"}).status_code == 405
    assert api_client.delete(public_detail_url(published.id)).status_code == 405


def test_public_search_matches_title_and_source(api_client, workbook_factory):
    match = workbook_factory(published=True, title="Phrasal verbs", source="Murphy")
    workbook_factory(published=True, title="Articles", source="Swan")

    rows = api_client.get(PUBLIC_LIST_URL, {"search": "Murphy"}).json()["results"]

    assert [row["id"] for row in rows] == [match.id]


def test_a_bearer_token_is_not_required_or_used(api_client, workbook_factory, student):
    """authentication_classes = [] - a stale token must not 401 the catalogue."""
    published = workbook_factory(published=True)
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert api_client.get(public_detail_url(published.id)).status_code == 200
