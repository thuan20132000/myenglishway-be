"""State transitions and structural rules for exercise collections.

Split from ``services.py`` because the two share nothing but the app: that
module is about audio, transcripts and readiness, this one is about the shape
of the folder tree. The same rule applies to both - views parse and serialize,
anything that decides structure or touches more than one model lives here.

The tree is deliberately shallow. A collection is either a *root* (a book, or a
flat category) or a *child* (a test); exercises hang off whichever collection
has no children. Two invariants keep "book" and "test" unambiguous without a
type column:

* a collection with children may not hold exercises, and
* a collection holding exercises may not gain children.
"""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from common import errors

from .models import CollectionMembership, ExerciseCollection, ListeningExercise


class CollectionDepthExceeded(errors.DomainError):
    default_code = errors.COLLECTION_DEPTH_EXCEEDED
    default_detail = "Collections can only be nested two levels deep."


class CollectionCycle(errors.DomainError):
    default_code = errors.COLLECTION_CYCLE
    default_detail = "A collection cannot be placed inside itself."


class CollectionOwnerMismatch(errors.DomainError):
    default_code = errors.COLLECTION_OWNER_MISMATCH
    default_detail = "A collection can only contain items owned by the same user."


class CollectionHasChildren(errors.ConflictError):
    default_code = errors.COLLECTION_HAS_CHILDREN
    default_detail = "This collection holds other collections, so it cannot hold exercises."


class CollectionHasMembers(errors.ConflictError):
    default_code = errors.COLLECTION_HAS_MEMBERS
    default_detail = "This collection holds exercises, so it cannot hold other collections."


class CollectionAlreadyPublished(errors.ConflictError):
    default_code = errors.COLLECTION_ALREADY_PUBLISHED
    default_detail = "This collection is already published."


class ExerciseAlreadyInCollection(errors.ConflictError):
    default_code = errors.EXERCISE_ALREADY_IN_COLLECTION
    default_detail = "That exercise already belongs to another collection."


# --------------------------------------------------------------------------
# Structural checks
# --------------------------------------------------------------------------


def assert_can_be_parent(parent: ExerciseCollection, *, owner_id: int, child_id=None) -> None:
    """Check ``parent`` may gain a child collection owned by ``owner_id``.

    Public because the Django admin validates against the same rules; the API
    reaches it through ``create_collection`` and ``update_collection``.
    """
    if parent.owner_id != owner_id:
        raise CollectionOwnerMismatch()
    if child_id is not None and parent.pk == child_id:
        raise CollectionCycle()
    if parent.parent_id is not None:
        # With a two-level tree this is also the only way to build a cycle
        # deeper than self-parenting: a descendant is always already a child.
        raise CollectionDepthExceeded()
    if parent.memberships.exists():
        raise CollectionHasMembers()


def assert_can_have_members(collection: ExerciseCollection) -> None:
    """Check ``collection`` is a leaf, and so may hold exercises."""
    if collection.children.exists():
        raise CollectionHasChildren()


def _next_position(queryset) -> int:
    return (queryset.aggregate(latest=Max("position"))["latest"] or 0) + 1


def _shift_siblings_from(parent_id, position: int, *, exclude_pk=None) -> None:
    """Make room at ``position`` by pushing later siblings down one place.

    Sibling positions are not backed by a unique constraint - a conditional one
    (roots share a NULL parent) cannot be DEFERRABLE, which reordering needs -
    so uniqueness is maintained here instead.
    """
    siblings = ExerciseCollection.objects.filter(
        parent_id=parent_id, position__gte=position
    ).order_by("-position")
    if exclude_pk is not None:
        siblings = siblings.exclude(pk=exclude_pk)

    for sibling in siblings:
        sibling.position += 1
        sibling.save(update_fields=["position", "updated_at"])


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------


@transaction.atomic
def create_collection(
    *,
    owner,
    title: str,
    description: str = "",
    parent: ExerciseCollection | None = None,
    position: int | None = None,
) -> ExerciseCollection:
    if parent is not None:
        assert_can_be_parent(parent, owner_id=owner.id)

    siblings = ExerciseCollection.objects.filter(parent=parent)
    if position is None:
        position = _next_position(siblings)
    else:
        _shift_siblings_from(parent.pk if parent else None, position)

    return ExerciseCollection.objects.create(
        owner=owner,
        parent=parent,
        title=title,
        description=description,
        position=position,
    )


@transaction.atomic
def update_collection(collection: ExerciseCollection, **fields) -> ExerciseCollection:
    """Edit metadata, and optionally re-file the collection under a new parent.

    ``parent`` is accepted here rather than through a dedicated move endpoint
    because it is one field of a folder's identity, like its title. The
    structural rules are the same ones ``create_collection`` applies.
    """
    moved = False
    if "parent" in fields:
        parent = fields.pop("parent")
        if parent != collection.parent:
            if parent is not None:
                assert_can_be_parent(
                    parent, owner_id=collection.owner_id, child_id=collection.pk
                )
                # Its own children would land on a third level.
                if collection.children.exists():
                    raise CollectionDepthExceeded()
            collection.parent = parent
            moved = True

    position = fields.pop("position", None)
    if moved and position is None:
        # Append to the end of the new sibling group rather than keeping a
        # position that means nothing there.
        position = _next_position(ExerciseCollection.objects.filter(parent=collection.parent))
    if position is not None and (moved or position != collection.position):
        _shift_siblings_from(collection.parent_id, position, exclude_pk=collection.pk)
        collection.position = position

    for field, value in fields.items():
        setattr(collection, field, value)

    collection.save()
    return collection


@transaction.atomic
def publish_collection(collection: ExerciseCollection) -> ExerciseCollection:
    """Make the folder visible to learners.

    Has no preconditions, unlike publishing an exercise: a collection carries
    no media to validate. It also does not cascade - the exercises inside keep
    whatever publication state they already had, so unpublishing a book hides
    the shelf without withdrawing the material.
    """
    if collection.is_published:
        raise CollectionAlreadyPublished()

    collection.is_published = True
    collection.published_at = timezone.now()
    collection.save(update_fields=["is_published", "published_at", "updated_at"])
    return collection


@transaction.atomic
def unpublish_collection(collection: ExerciseCollection) -> ExerciseCollection:
    """Withdraw the folder. Idempotent, matching ``unpublish_exercise``."""
    if not collection.is_published:
        return collection

    collection.is_published = False
    collection.published_at = None
    collection.save(update_fields=["is_published", "published_at", "updated_at"])
    return collection


# --------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------


@transaction.atomic
def replace_members(
    collection: ExerciseCollection, exercise_ids: list[int]
) -> list[CollectionMembership]:
    """Rewrite the whole member list in order. Position is index + 1.

    Wholesale replacement rather than add/move/remove verbs: the creator UI
    edits an ordered list, and one request that states the final order cannot
    leave gaps or duplicates behind the way a sequence of partial edits can.
    """
    assert_can_have_members(collection)

    exercises = {
        exercise.pk: exercise
        for exercise in ListeningExercise.objects.filter(pk__in=exercise_ids).select_related(
            "membership"
        )
    }

    for exercise_id in exercise_ids:
        exercise = exercises.get(exercise_id)
        if exercise is None:
            raise errors.DomainError(
                f"No exercise with id {exercise_id}.", code=errors.NOT_FOUND, status_code=404
            )
        if exercise.owner_id != collection.owner_id:
            raise CollectionOwnerMismatch()
        membership = getattr(exercise, "membership", None)
        if membership is not None and membership.collection_id != collection.pk:
            raise ExerciseAlreadyInCollection(extra={"exercise_id": exercise_id})

    collection.memberships.all().delete()
    return CollectionMembership.objects.bulk_create(
        CollectionMembership(collection=collection, exercise=exercises[exercise_id], position=index)
        for index, exercise_id in enumerate(exercise_ids, start=1)
    )


@transaction.atomic
def remove_member(collection: ExerciseCollection, exercise_id: int) -> None:
    """Detach one exercise, closing the gap it leaves in the ordering."""
    membership = collection.memberships.filter(exercise_id=exercise_id).first()
    if membership is None:
        raise errors.DomainError(
            "That exercise is not in this collection.",
            code=errors.NOT_FOUND,
            status_code=404,
        )

    membership.delete()

    for position, remaining in enumerate(collection.memberships.order_by("position"), start=1):
        if remaining.position != position:
            remaining.position = position
            remaining.save(update_fields=["position", "updated_at"])
