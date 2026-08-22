import pytest
from django.urls import reverse

from listening.models import CollectionMembership, ExerciseCollection, ListeningExercise
from tests.factories import (
    AdminFactory,
    CollectionFactory,
    CollectionMembershipFactory,
    CreatorFactory,
    ExerciseFactory,
)

pytestmark = pytest.mark.django_db

LIST_URL = reverse("v1:collection-list")
EXERCISE_LIST_URL = reverse("v1:exercise-list")


def detail_url(collection_id) -> str:
    return reverse("v1:collection-detail", args=[collection_id])


def members_url(collection_id) -> str:
    return reverse("v1:collection-members", args=[collection_id])


def member_url(collection_id, exercise_id) -> str:
    return reverse("v1:collection-member", args=[collection_id, exercise_id])


def publish_url(collection_id) -> str:
    return reverse("v1:collection-publish", args=[collection_id])


def unpublish_url(collection_id) -> str:
    return reverse("v1:collection-unpublish", args=[collection_id])


def exercise_detail_url(exercise_id) -> str:
    return reverse("v1:exercise-detail", args=[exercise_id])


# ------------------------------------------------------------- permissions


def test_anonymous_is_rejected(api_client):
    assert api_client.get(LIST_URL).status_code == 401


def test_student_cannot_create_a_collection(auth_client, student):
    response = auth_client(student).post(LIST_URL, {"title": "Mine"}, format="json")

    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert ExerciseCollection.objects.count() == 0


def test_creator_can_create_a_root_collection(auth_client, creator):
    response = auth_client(creator).post(
        LIST_URL, {"title": "  Cambridge IELTS 18  "}, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Cambridge IELTS 18"
    assert body["parent"] is None
    assert body["is_published"] is False
    assert body["position"] == 1
    assert body["owner"]["id"] == creator.id


def test_creator_cannot_edit_another_creators_collection(auth_client, creator):
    collection = CollectionFactory(owner=CreatorFactory(), published=True)

    response = auth_client(creator).patch(
        detail_url(collection.id), {"title": "Hijacked"}, format="json"
    )

    assert response.status_code == 403


def test_admin_may_edit_someone_elses_collection(auth_client, admin_user):
    collection = CollectionFactory()

    response = auth_client(admin_user).patch(
        detail_url(collection.id), {"title": "Renamed"}, format="json"
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


# ------------------------------------------------------------- visibility


def test_student_sees_only_published_roots(auth_client, student):
    published = CollectionFactory(published=True)
    CollectionFactory()  # someone's draft
    CollectionFactory(parent=published, published=True)  # a child, not a root row

    body = auth_client(student).get(LIST_URL).json()

    assert [row["id"] for row in body["results"]] == [published.id]


def test_creator_sees_published_plus_their_own(auth_client, creator):
    own_draft = CollectionFactory(owner=creator)
    other_published = CollectionFactory(published=True)
    CollectionFactory()  # another creator's draft - invisible

    body = auth_client(creator).get(LIST_URL).json()

    assert {row["id"] for row in body["results"]} == {own_draft.id, other_published.id}


def test_creator_cannot_retrieve_another_creators_draft(auth_client, creator):
    collection = CollectionFactory(owner=CreatorFactory())

    assert auth_client(creator).get(detail_url(collection.id)).status_code == 404


def test_detail_hides_unpublished_children_from_students(auth_client, student):
    book = CollectionFactory(published=True)
    visible = CollectionFactory(parent=book, published=True, position=1)
    CollectionFactory(parent=book, position=2)  # draft test

    body = auth_client(student).get(detail_url(book.id)).json()

    assert [child["id"] for child in body["children"]] == [visible.id]
    # The count annotation is not visibility-filtered; the list is.
    assert body["child_count"] == 2


def test_detail_lists_members_in_position_order(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    third = ExerciseFactory(owner=creator)
    first = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=collection, exercise=third, position=2)
    CollectionMembershipFactory(collection=collection, exercise=first, position=1)

    body = auth_client(creator).get(detail_url(collection.id)).json()

    assert [member["id"] for member in body["members"]] == [first.id, third.id]


# ------------------------------------------------------------- structure


def test_a_third_level_is_rejected(auth_client, creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator, parent=book)

    response = auth_client(creator).post(
        LIST_URL, {"title": "Part 1", "parent": test.id}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COLLECTION_DEPTH_EXCEEDED"


def test_a_collection_cannot_be_its_own_parent(auth_client, creator):
    collection = CollectionFactory(owner=creator)

    response = auth_client(creator).patch(
        detail_url(collection.id), {"parent": collection.id}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COLLECTION_CYCLE"


def test_moving_a_parent_under_a_child_is_rejected(auth_client, creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator, parent=book)

    response = auth_client(creator).patch(
        detail_url(book.id), {"parent": test.id}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COLLECTION_DEPTH_EXCEEDED"


def test_parent_must_have_the_same_owner(auth_client, creator):
    someone_elses = CollectionFactory(owner=CreatorFactory(), published=True)

    response = auth_client(creator).post(
        LIST_URL, {"title": "Test 1", "parent": someone_elses.id}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COLLECTION_OWNER_MISMATCH"


def test_a_collection_with_members_cannot_gain_children(auth_client, creator):
    test = CollectionFactory(owner=creator)
    CollectionMembershipFactory(collection=test, position=1)

    response = auth_client(creator).post(
        LIST_URL, {"title": "Nested", "parent": test.id}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "COLLECTION_HAS_MEMBERS"


def test_a_collection_with_children_cannot_gain_members(auth_client, creator):
    book = CollectionFactory(owner=creator)
    CollectionFactory(owner=creator, parent=book)
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).put(
        members_url(book.id), {"exercise_ids": [exercise.id]}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "COLLECTION_HAS_CHILDREN"


def test_creating_at_a_position_shifts_later_siblings(auth_client, creator):
    book = CollectionFactory(owner=creator)
    second = CollectionFactory(owner=creator, parent=book, position=1)

    response = auth_client(creator).post(
        LIST_URL, {"title": "Test 1", "parent": book.id, "position": 1}, format="json"
    )

    assert response.status_code == 201
    second.refresh_from_db()
    assert second.position == 2


# ------------------------------------------------------------- membership


def test_members_are_stored_in_the_order_sent(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    one = ExerciseFactory(owner=creator)
    two = ExerciseFactory(owner=creator)
    three = ExerciseFactory(owner=creator)

    response = auth_client(creator).put(
        members_url(collection.id),
        {"exercise_ids": [three.id, one.id, two.id]},
        format="json",
    )

    assert response.status_code == 200
    assert [member["id"] for member in response.json()["members"]] == [
        three.id,
        one.id,
        two.id,
    ]
    assert list(collection.memberships.values_list("position", flat=True)) == [1, 2, 3]


def test_replacing_members_reorders_without_tripping_the_unique(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    one = ExerciseFactory(owner=creator)
    two = ExerciseFactory(owner=creator)
    client = auth_client(creator)
    client.put(members_url(collection.id), {"exercise_ids": [one.id, two.id]}, format="json")

    response = client.put(
        members_url(collection.id), {"exercise_ids": [two.id, one.id]}, format="json"
    )

    assert response.status_code == 200
    assert [member["id"] for member in response.json()["members"]] == [two.id, one.id]


def test_members_must_be_owned_by_the_collection_owner(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    someone_elses = ExerciseFactory(owner=CreatorFactory(), published=True)

    response = auth_client(creator).put(
        members_url(collection.id), {"exercise_ids": [someone_elses.id]}, format="json"
    )

    assert response.status_code == 400
    assert response.json()["code"] == "COLLECTION_OWNER_MISMATCH"


def test_an_exercise_cannot_be_in_two_collections(auth_client, creator):
    first = CollectionFactory(owner=creator)
    second = CollectionFactory(owner=creator)
    exercise = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=first, exercise=exercise, position=1)

    response = auth_client(creator).put(
        members_url(second.id), {"exercise_ids": [exercise.id]}, format="json"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "EXERCISE_ALREADY_IN_COLLECTION"


def test_duplicate_ids_are_a_field_error(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    exercise = ExerciseFactory(owner=creator)

    response = auth_client(creator).put(
        members_url(collection.id),
        {"exercise_ids": [exercise.id, exercise.id]},
        format="json",
    )

    assert response.status_code == 400
    assert "exercise_ids" in response.json()


def test_removing_a_member_closes_the_gap(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    one = ExerciseFactory(owner=creator)
    two = ExerciseFactory(owner=creator)
    three = ExerciseFactory(owner=creator)
    client = auth_client(creator)
    client.put(
        members_url(collection.id),
        {"exercise_ids": [one.id, two.id, three.id]},
        format="json",
    )

    response = client.delete(member_url(collection.id, two.id))

    assert response.status_code == 204
    assert list(
        collection.memberships.order_by("position").values_list("exercise_id", "position")
    ) == [(one.id, 1), (three.id, 2)]
    assert ListeningExercise.objects.filter(pk=two.id).exists()


def test_removing_an_exercise_that_is_not_a_member_is_404(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    stranger = ExerciseFactory(owner=creator)

    response = auth_client(creator).delete(member_url(collection.id, stranger.id))

    assert response.status_code == 404


# ------------------------------------------------------------- publication


def test_publish_then_publishing_again_conflicts(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    client = auth_client(creator)

    first = client.post(publish_url(collection.id))
    assert first.status_code == 200
    assert first.json()["is_published"] is True
    assert first.json()["published_at"] is not None

    second = client.post(publish_url(collection.id))
    assert second.status_code == 409
    assert second.json()["code"] == "COLLECTION_ALREADY_PUBLISHED"


def test_unpublish_is_idempotent(auth_client, creator):
    collection = CollectionFactory(owner=creator, published=True)
    client = auth_client(creator)

    assert client.post(unpublish_url(collection.id)).status_code == 200
    response = client.post(unpublish_url(collection.id))

    assert response.status_code == 200
    assert response.json()["is_published"] is False


def test_publication_cannot_be_set_through_patch(auth_client, creator):
    collection = CollectionFactory(owner=creator)

    response = auth_client(creator).patch(
        detail_url(collection.id), {"is_published": True}, format="json"
    )

    assert response.status_code == 200
    collection.refresh_from_db()
    assert collection.is_published is False


def test_unpublishing_a_collection_leaves_its_exercises_published(auth_client, creator):
    collection = CollectionFactory(owner=creator, published=True)
    exercise = ExerciseFactory(owner=creator, published=True)
    CollectionMembershipFactory(collection=collection, exercise=exercise, position=1)

    auth_client(creator).post(unpublish_url(collection.id))

    exercise.refresh_from_db()
    assert exercise.is_published is True


# ------------------------------------------------------------- deletion


def test_deleting_a_collection_keeps_the_exercises(auth_client, creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator, parent=book)
    exercise = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=test, exercise=exercise, position=1)

    response = auth_client(creator).delete(detail_url(book.id))

    assert response.status_code == 204
    assert ExerciseCollection.objects.count() == 0
    assert CollectionMembership.objects.count() == 0
    assert ListeningExercise.objects.filter(pk=exercise.id).exists()


# ------------------------------------------------------------- exercise API


def test_exercises_can_be_filtered_to_one_collection(auth_client, creator):
    collection = CollectionFactory(owner=creator)
    # Positions deliberately disagree with creation order, so a result in
    # membership order cannot also be the -created_at default.
    first = ExerciseFactory(owner=creator)
    second = ExerciseFactory(owner=creator)
    third = ExerciseFactory(owner=creator)
    ExerciseFactory(owner=creator)  # ungrouped
    CollectionMembershipFactory(collection=collection, exercise=second, position=1)
    CollectionMembershipFactory(collection=collection, exercise=third, position=2)
    CollectionMembershipFactory(collection=collection, exercise=first, position=3)

    body = (
        auth_client(creator)
        .get(f"{EXERCISE_LIST_URL}?collection={collection.id}&ordering=position")
        .json()
    )

    assert [row["id"] for row in body["results"]] == [second.id, third.id, first.id]


def test_collection_filter_without_ordering_keeps_the_default(auth_client, creator):
    """The filter selects the set; only ?ordering=position orders it."""
    collection = CollectionFactory(owner=creator)
    older = ExerciseFactory(owner=creator)
    newer = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=collection, exercise=newer, position=1)
    CollectionMembershipFactory(collection=collection, exercise=older, position=2)

    body = (
        auth_client(creator)
        .get(f"{EXERCISE_LIST_URL}?collection={collection.id}")
        .json()
    )

    assert [row["id"] for row in body["results"]] == [newer.id, older.id]


def test_ungrouped_filter_excludes_members(auth_client, creator):
    loose = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=CollectionFactory(owner=creator), position=1)

    body = auth_client(creator).get(f"{EXERCISE_LIST_URL}?ungrouped=true").json()

    assert [row["id"] for row in body["results"]] == [loose.id]


def test_exercise_detail_carries_the_breadcrumb(auth_client, creator):
    book = CollectionFactory(owner=creator, title="Cambridge IELTS 18")
    test = CollectionFactory(owner=creator, parent=book, title="Test 1")
    exercise = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=test, exercise=exercise, position=1)

    body = auth_client(creator).get(exercise_detail_url(exercise.id)).json()

    assert body["collection"]["title"] == "Test 1"
    assert body["collection"]["parent"]["title"] == "Cambridge IELTS 18"


def test_breadcrumb_is_null_when_ungrouped(auth_client, creator):
    exercise = ExerciseFactory(owner=creator)

    body = auth_client(creator).get(exercise_detail_url(exercise.id)).json()

    assert body["collection"] is None


def test_breadcrumb_hides_a_draft_collection_from_students(auth_client, student, creator):
    draft = CollectionFactory(owner=creator)
    exercise = ExerciseFactory(owner=creator, published=True)
    CollectionMembershipFactory(collection=draft, exercise=exercise, position=1)

    body = auth_client(student).get(exercise_detail_url(exercise.id)).json()

    assert body["collection"] is None


# ------------------------------------------------------- query optimization


def test_collection_list_query_count_is_constant(
    auth_client, creator, django_assert_num_queries
):
    for _ in range(3):
        CollectionFactory(owner=creator)

    client = auth_client(creator)
    with django_assert_num_queries(2):  # count + page
        client.get(LIST_URL)

    for _ in range(5):
        CollectionFactory(owner=creator)

    with django_assert_num_queries(2):
        client.get(LIST_URL)


def test_exercise_list_breadcrumb_does_not_add_queries(
    auth_client, creator, django_assert_num_queries
):
    collection = CollectionFactory(owner=creator)
    for position in range(1, 4):
        CollectionMembershipFactory(
            collection=collection,
            exercise=ExerciseFactory(owner=creator),
            position=position,
        )

    client = auth_client(creator)
    with django_assert_num_queries(2):  # count + page, membership joined
        client.get(EXERCISE_LIST_URL)


def test_admin_sees_every_collection(auth_client, admin_user):
    CollectionFactory()
    CollectionFactory(published=True)

    body = auth_client(admin_user).get(LIST_URL).json()

    assert body["count"] == 2


def test_breadcrumb_drops_a_parent_the_caller_cannot_see(auth_client, student, creator):
    """A withdrawn book must not be named by the published test inside it."""
    book = CollectionFactory(owner=creator, title="Cambridge IELTS 18")
    test = CollectionFactory(owner=creator, parent=book, published=True, title="Test 1")
    exercise = ExerciseFactory(owner=creator, published=True)
    CollectionMembershipFactory(collection=test, exercise=exercise, position=1)

    body = auth_client(student).get(exercise_detail_url(exercise.id)).json()

    assert body["collection"]["title"] == "Test 1"
    assert body["collection"]["parent"] is None


def test_collection_detail_hides_an_invisible_parent(auth_client, student, creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator, parent=book, published=True)

    body = auth_client(student).get(detail_url(test.id)).json()

    assert body["parent"] is None
