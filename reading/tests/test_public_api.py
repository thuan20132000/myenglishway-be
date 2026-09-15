"""The anonymous catalogue.

Publishing shares the passage body and nothing else: there is no public
session route, and no published payload ever carries anyone's WPM history.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

PUBLIC_LIST_URL = reverse("v1:public-reading-exercise-list")


def public_detail_url(passage_id):
    return reverse("v1:public-reading-exercise-detail", args=[passage_id])


def test_anonymous_visitor_sees_published_passages(api_client, passage_factory):
    published = passage_factory(published=True)
    passage_factory()  # a draft

    body = api_client.get(PUBLIC_LIST_URL).json()

    assert [row["id"] for row in body["results"]] == [published.id]


def test_unpublished_passage_is_404_not_403(api_client, passage_factory):
    draft = passage_factory()
    assert api_client.get(public_detail_url(draft.id)).status_code == 404


def test_public_detail_carries_the_body(api_client, passage_factory):
    published = passage_factory(published=True)

    body = api_client.get(public_detail_url(published.id)).json()

    assert body["body"] == published.body
    assert body["word_count"] == published.word_count


def test_public_list_omits_body(api_client, passage_factory):
    passage_factory(published=True)
    row = api_client.get(PUBLIC_LIST_URL).json()["results"][0]
    assert "body" not in row
    assert "audio_url" not in row


def test_public_rows_report_no_sessions(api_client, passage_factory, session_factory):
    published = passage_factory(published=True)
    session_factory(exercise=published, finished=True)

    row = api_client.get(PUBLIC_LIST_URL).json()["results"][0]

    assert row["session_count"] == 0
    assert row["last_actual_wpm"] is None
    assert row["last_target_wpm"] is None


def test_public_surface_is_read_only(api_client, passage_factory):
    published = passage_factory(published=True)
    assert api_client.post(PUBLIC_LIST_URL, {"title": "x"}).status_code == 405
    assert api_client.delete(public_detail_url(published.id)).status_code == 405


def test_public_search_matches_title_and_source(api_client, passage_factory):
    match = passage_factory(published=True, title="Section 4 lecture", source="IELTS 18")
    passage_factory(published=True, title="Section 1 forms", source="IELTS 17")

    rows = api_client.get(PUBLIC_LIST_URL, {"search": "IELTS 18"}).json()["results"]

    assert [row["id"] for row in rows] == [match.id]


def test_a_bearer_token_is_not_required_or_used(api_client, passage_factory, student):
    """authentication_classes = [] - a stale token must not 401 the catalogue."""
    published = passage_factory(published=True)
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert api_client.get(public_detail_url(published.id)).status_code == 200


def test_anonymous_cannot_start_a_session(api_client, passage_factory):
    published = passage_factory(published=True)
    url = reverse("v1:reading-session-list", args=[published.id])
    assert api_client.post(url, {"target_wpm": 250}, format="json").status_code == 401
