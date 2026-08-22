import pytest
from django.db import IntegrityError, connection, transaction

from listening.models import CollectionMembership, ExerciseCollection, ListeningExercise
from tests.factories import (
    CollectionFactory,
    CollectionMembershipFactory,
    ExerciseFactory,
)

pytestmark = pytest.mark.django_db


def check_constraints_immediately():
    """Force deferred constraints to be validated on statement execution."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


# -------------------------------------------------------------- collection


def test_collection_defaults_to_an_unpublished_root():
    collection = CollectionFactory()
    assert collection.parent_id is None
    assert collection.is_root is True
    assert collection.is_published is False
    assert collection.position == 1


def test_collection_position_must_be_at_least_1():
    with pytest.raises(IntegrityError):
        CollectionFactory(position=0)


def test_collections_order_by_parent_then_position():
    book = CollectionFactory(position=1)
    second = CollectionFactory(parent=book, position=2)
    first = CollectionFactory(parent=book, position=1)

    assert list(ExerciseCollection.objects.all()) == [book, first, second]


def test_deleting_a_collection_deletes_its_children():
    book = CollectionFactory()
    CollectionFactory(parent=book)

    book.delete()

    assert ExerciseCollection.objects.count() == 0


# -------------------------------------------------------------- membership


def test_membership_position_must_be_at_least_1():
    with pytest.raises(IntegrityError):
        CollectionMembershipFactory(position=0)


def test_an_exercise_belongs_to_at_most_one_collection():
    exercise = ExerciseFactory()
    CollectionMembershipFactory(exercise=exercise, position=1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CollectionMembershipFactory(exercise=exercise, position=1)


def test_member_position_is_unique_per_collection():
    collection = CollectionFactory()
    CollectionMembershipFactory(collection=collection, position=1)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CollectionMembershipFactory(collection=collection, position=1)
            check_constraints_immediately()


def test_member_positions_may_collide_mid_transaction():
    """The point of deferring: a reorder can swap two positions in one go."""
    collection = CollectionFactory()
    first = CollectionMembershipFactory(collection=collection, position=1)
    second = CollectionMembershipFactory(collection=collection, position=2)

    with transaction.atomic():
        first.position = 2
        first.save(update_fields=["position", "updated_at"])
        second.position = 1
        second.save(update_fields=["position", "updated_at"])

    assert list(collection.memberships.values_list("exercise_id", flat=True)) == [
        second.exercise_id,
        first.exercise_id,
    ]


def test_deleting_a_collection_keeps_its_exercises():
    collection = CollectionFactory()
    membership = CollectionMembershipFactory(collection=collection)
    exercise_id = membership.exercise_id

    collection.delete()

    assert CollectionMembership.objects.count() == 0
    assert ListeningExercise.objects.filter(pk=exercise_id).exists()
