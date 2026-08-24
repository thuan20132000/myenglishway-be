"""The anonymous catalogue: what an unauthenticated visitor may see."""

import pytest
from django.urls import reverse

from tests.factories import (
    CollectionFactory,
    CollectionMembershipFactory,
    ExerciseFactory,
    SegmentFactory,
)

pytestmark = pytest.mark.django_db

COLLECTION_LIST_URL = reverse("v1:public-collection-list")
EXERCISE_LIST_URL = reverse("v1:public-exercise-list")


def collection_detail_url(collection_id) -> str:
    return reverse("v1:public-collection-detail", args=[collection_id])


def exercise_detail_url(exercise_id) -> str:
    return reverse("v1:public-exercise-detail", args=[exercise_id])


def public_segments_url(exercise_id) -> str:
    return reverse("v1:public-practice-segments", args=[exercise_id])


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------


def test_lists_published_root_collections_without_a_token(api_client):
    published = CollectionFactory(published=True, title="Cambridge IELTS 18")
    CollectionFactory(title="Unfinished book")

    response = api_client.get(COLLECTION_LIST_URL)

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [published.id]


def test_child_collections_are_not_listed_as_roots(api_client):
    root = CollectionFactory(published=True)
    CollectionFactory(published=True, parent=root)

    response = api_client.get(COLLECTION_LIST_URL)

    assert [row["id"] for row in response.data["results"]] == [root.id]


def test_collection_detail_shows_only_published_contents(api_client):
    collection = CollectionFactory(published=True)
    member = ExerciseFactory(published=True, owner=collection.owner)
    draft = ExerciseFactory(owner=collection.owner)
    CollectionMembershipFactory(collection=collection, exercise=member, position=1)
    CollectionMembershipFactory(collection=collection, exercise=draft, position=2)

    response = api_client.get(collection_detail_url(collection.id))

    assert response.status_code == 200
    assert [row["id"] for row in response.data["members"]] == [member.id]


def test_unpublished_collection_is_not_found(api_client):
    collection = CollectionFactory()

    assert api_client.get(collection_detail_url(collection.id)).status_code == 404


# --------------------------------------------------------------------------
# Exercises
# --------------------------------------------------------------------------


def test_lists_published_exercises_without_a_token(api_client):
    published = ExerciseFactory(published=True)
    ExerciseFactory()

    response = api_client.get(EXERCISE_LIST_URL)

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [published.id]


def test_exercises_can_be_filtered_by_collection_in_member_order(api_client):
    collection = CollectionFactory(published=True)
    first = ExerciseFactory(published=True, owner=collection.owner)
    second = ExerciseFactory(published=True, owner=collection.owner)
    CollectionMembershipFactory(collection=collection, exercise=second, position=1)
    CollectionMembershipFactory(collection=collection, exercise=first, position=2)

    response = api_client.get(
        EXERCISE_LIST_URL, {"collection": collection.id, "ordering": "position"}
    )

    assert [row["id"] for row in response.data["results"]] == [second.id, first.id]


def test_exercise_detail_never_includes_the_transcript(api_client):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise)

    response = api_client.get(exercise_detail_url(exercise.id))

    assert response.status_code == 200
    assert "segments" not in response.data
    assert "processing_error" not in response.data
    assert response.data["audio_url"]


def test_unpublished_exercise_is_not_found(api_client):
    exercise = ExerciseFactory(ready=True)

    assert api_client.get(exercise_detail_url(exercise.id)).status_code == 404


def test_breadcrumb_hides_a_collection_the_visitor_cannot_see(api_client):
    collection = CollectionFactory()
    exercise = ExerciseFactory(published=True, owner=collection.owner)
    CollectionMembershipFactory(collection=collection, exercise=exercise, position=1)

    response = api_client.get(exercise_detail_url(exercise.id))

    assert response.data["collection"] is None


def test_public_endpoints_are_read_only(api_client):
    assert api_client.post(EXERCISE_LIST_URL, {}).status_code == 405
    assert api_client.post(COLLECTION_LIST_URL, {}).status_code == 405


# --------------------------------------------------------------------------
# Segments for practice
# --------------------------------------------------------------------------


def test_segment_timings_are_public_without_transcript(api_client):
    exercise = ExerciseFactory(published=True)
    SegmentFactory(exercise=exercise, sequence=2)
    SegmentFactory(exercise=exercise, sequence=1)

    response = api_client.get(public_segments_url(exercise.id))

    assert response.status_code == 200
    assert [row["sequence"] for row in response.data] == [1, 2]
    assert all("text" not in row for row in response.data)


def test_segments_of_an_unpublished_exercise_are_not_found(api_client):
    exercise = ExerciseFactory(ready=True)
    SegmentFactory(exercise=exercise)

    assert api_client.get(public_segments_url(exercise.id)).status_code == 404
