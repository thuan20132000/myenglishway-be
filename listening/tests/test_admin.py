"""The admin's own rules.

The API tests already cover the service layer; what matters here is that the
admin does not quietly route around it. Forms are exercised directly rather
than through the HTTP client, which keeps the assertions about validation
rather than about Django's rendering.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from listening.admin import (
    CollectionMembershipInline,
    ExerciseCollectionAdmin,
    ExerciseCollectionForm,
    ListeningExerciseAdmin,
)
from accounts.models import Role
from listening.models import ExerciseCollection, ExerciseStatus, ListeningExercise
from tests.factories import (
    CollectionFactory,
    CollectionMembershipFactory,
    CreatorFactory,
    ExerciseFactory,
    SegmentFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff():
    """A Django-admin user.

    Distinct from the ``admin_user`` fixture, which carries this app's
    ``Role.ADMIN`` and nothing else: the admin site is gated by ``is_staff``
    and Django's own permissions, and inline formsets silently ignore rows a
    user lacks add/change permission for.
    """
    return UserFactory(role=Role.ADMIN, is_staff=True, is_superuser=True)


def request_with_messages(user):
    request = RequestFactory().post("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def collection_admin() -> ExerciseCollectionAdmin:
    return ExerciseCollectionAdmin(ExerciseCollection, AdminSite())


def exercise_admin() -> ListeningExerciseAdmin:
    return ListeningExerciseAdmin(ListeningExercise, AdminSite())


def collection_form_data(collection, **overrides) -> dict:
    data = {
        "owner": collection.owner_id,
        "title": collection.title,
        "description": collection.description,
        "position": collection.position,
        "is_published": collection.is_published,
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------- placement


def test_admin_rejects_a_third_level(creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator, parent=book)
    deeper = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(deeper, parent=test.id), instance=deeper
    )

    assert not form.is_valid()
    assert "two levels deep" in str(form.errors["parent"])


def test_admin_rejects_a_self_parent(creator):
    collection = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(collection, parent=collection.id), instance=collection
    )

    assert not form.is_valid()
    assert "inside itself" in str(form.errors["parent"])


def test_admin_rejects_a_parent_owned_by_someone_else(creator):
    stranger = CollectionFactory(owner=CreatorFactory())
    collection = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(collection, parent=stranger.id), instance=collection
    )

    assert not form.is_valid()
    assert "same user" in str(form.errors["parent"])


def test_admin_rejects_a_parent_that_holds_exercises(creator):
    test = CollectionFactory(owner=creator)
    CollectionMembershipFactory(collection=test, position=1)
    other = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(other, parent=test.id), instance=other
    )

    assert not form.is_valid()
    assert "cannot hold other collections" in str(form.errors["parent"])


def test_admin_refuses_to_move_a_collection_that_has_children(creator):
    book = CollectionFactory(owner=creator)
    CollectionFactory(owner=creator, parent=book)
    another_root = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(book, parent=another_root.id), instance=book
    )

    assert not form.is_valid()
    assert "cannot itself go inside another" in str(form.errors["parent"])


def test_admin_accepts_a_valid_placement(creator):
    book = CollectionFactory(owner=creator)
    test = CollectionFactory(owner=creator)

    form = ExerciseCollectionForm(
        data=collection_form_data(test, parent=book.id), instance=test
    )

    assert form.is_valid(), form.errors


def test_parent_choices_are_roots_only(creator):
    book = CollectionFactory(owner=creator)
    child = CollectionFactory(owner=creator, parent=book)

    field = collection_admin().formfield_for_foreignkey(
        ExerciseCollection._meta.get_field("parent"), request_with_messages(creator)
    )

    assert book in field.queryset
    assert child not in field.queryset


# ------------------------------------------------------------- membership


def membership_formset(collection, exercises, staff):
    """Build the inline formset the change page would post."""
    inline = CollectionMembershipInline(ExerciseCollection, AdminSite())
    FormSet = inline.get_formset(request_with_messages(staff), collection)
    prefix = FormSet.get_default_prefix()
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(exercises)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, exercise in enumerate(exercises):
        data[f"{prefix}-{index}-position"] = str(index + 1)
        data[f"{prefix}-{index}-exercise"] = str(exercise.id)
        data[f"{prefix}-{index}-id"] = ""
        data[f"{prefix}-{index}-collection"] = str(collection.id)
    return FormSet(data=data, instance=collection)


def test_admin_refuses_exercises_on_a_collection_with_children(creator, staff):
    book = CollectionFactory(owner=creator)
    CollectionFactory(owner=creator, parent=book)
    exercise = ExerciseFactory(owner=creator)

    formset = membership_formset(book, [exercise], staff)

    assert not formset.is_valid()
    assert "cannot hold exercises" in str(formset.non_form_errors())


def test_admin_refuses_an_exercise_owned_by_someone_else(creator, staff):
    collection = CollectionFactory(owner=creator)
    stranger = ExerciseFactory(owner=CreatorFactory())

    formset = membership_formset(collection, [stranger], staff)

    assert not formset.is_valid()
    assert "not to" in str(formset.errors)


def test_admin_refuses_an_exercise_already_filed_elsewhere(creator, staff):
    first = CollectionFactory(owner=creator, title="Test 1")
    second = CollectionFactory(owner=creator, title="Test 2")
    exercise = ExerciseFactory(owner=creator)
    CollectionMembershipFactory(collection=first, exercise=exercise, position=1)

    formset = membership_formset(second, [exercise], staff)

    assert not formset.is_valid()
    assert "already in" in str(formset.errors)


def test_admin_accepts_valid_members(creator, staff):
    collection = CollectionFactory(owner=creator)
    one = ExerciseFactory(owner=creator)
    two = ExerciseFactory(owner=creator)

    formset = membership_formset(collection, [one, two], staff)

    assert formset.is_valid(), formset.errors


# ------------------------------------------------------------- actions


def test_publish_action_uses_the_service(creator, admin_user):
    collection = CollectionFactory(owner=creator)
    request = request_with_messages(admin_user)

    collection_admin().do_publish(
        request, ExerciseCollection.objects.filter(pk=collection.pk)
    )

    collection.refresh_from_db()
    assert collection.is_published is True
    assert collection.published_at is not None


def test_publish_action_reports_a_domain_error_instead_of_raising(creator, admin_user):
    """A draft exercise cannot be published; the action must say so, not 500."""
    exercise = ExerciseFactory(owner=creator)
    request = request_with_messages(admin_user)

    exercise_admin().do_publish(
        request, ListeningExercise.objects.filter(pk=exercise.pk)
    )

    exercise.refresh_from_db()
    assert exercise.is_published is False
    assert any("requirements for publishing" in str(m) for m in request._messages)


def test_publish_action_publishes_what_it_can_and_skips_the_rest(creator, admin_user):
    ready = ExerciseFactory(owner=creator, ready=True)
    SegmentFactory(exercise=ready, sequence=1)
    blocked = ExerciseFactory(owner=creator)
    request = request_with_messages(admin_user)

    exercise_admin().do_publish(
        request, ListeningExercise.objects.filter(pk__in=[ready.pk, blocked.pk])
    )

    ready.refresh_from_db()
    blocked.refresh_from_db()
    assert ready.is_published is True
    assert blocked.is_published is False


def test_unpublish_action_is_idempotent(creator, admin_user):
    collection = CollectionFactory(owner=creator)
    request = request_with_messages(admin_user)

    collection_admin().do_unpublish(
        request, ExerciseCollection.objects.filter(pk=collection.pk)
    )

    collection.refresh_from_db()
    assert collection.is_published is False


# ------------------------------------------------------------- changelist


def test_changelist_columns_do_not_query_per_row(creator, admin_user, django_assert_num_queries):
    collection = CollectionFactory(owner=creator)
    for position in range(1, 4):
        CollectionMembershipFactory(
            collection=collection,
            exercise=ExerciseFactory(owner=creator),
            position=position,
        )

    admin = exercise_admin()
    request = request_with_messages(admin_user)
    rows = list(admin.get_queryset(request))

    with django_assert_num_queries(0):
        for row in rows:
            admin.segment_count(row)
            admin.collection_link(row)


def test_collection_counts_come_from_annotations(creator, admin_user):
    book = CollectionFactory(owner=creator)
    CollectionFactory(owner=creator, parent=book)
    CollectionFactory(owner=creator, parent=book)

    admin = collection_admin()
    row = admin.get_queryset(request_with_messages(admin_user)).get(pk=book.pk)

    assert admin.child_count(row) == 2
    assert admin.member_count(row) == 0


def test_exercise_status_choices_are_untouched_by_the_admin():
    """Guards the fieldset listing 'status' against a silent choices change."""
    assert ExerciseStatus.READY in dict(ExerciseStatus.choices)
