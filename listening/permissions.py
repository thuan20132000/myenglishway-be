from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsExerciseOwnerOrAdmin(BasePermission):
    """Write access limited to the exercise owner (or an admin).

    Read access is left to queryset scoping: an exercise the caller may not see
    is absent from the queryset and yields 404, so a 403 here never confirms
    the existence of someone else's draft.
    """

    message = "You do not have permission to modify this exercise."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in SAFE_METHODS:
            return True
        if user.is_admin:
            return True
        return self._owner_id(obj) == user.id

    @staticmethod
    def _owner_id(obj) -> int:
        # Works for both ListeningExercise and TranscriptSegment.
        exercise = getattr(obj, "exercise", obj)
        return exercise.owner_id


class IsExerciseSolutionOwnerOrAdmin(IsExerciseOwnerOrAdmin):
    """Owner-only for reads as well as writes.

    The base class lets any caller read, because an exercise payload is safe to
    show. Payloads that give away the solution - the transcript, the answer key
    - are not, so reading them is restricted to the owner too.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user
        return user.is_admin or self._owner_id(obj) == user.id


class IsSegmentOwnerOrAdmin(IsExerciseSolutionOwnerOrAdmin):
    """Segment access follows its exercise's owner: payloads contain transcript."""

    message = "You do not have permission to view or modify this transcript."


class IsAnswerKeyOwnerOrAdmin(IsExerciseSolutionOwnerOrAdmin):
    """The answer key is the solution to the question sheet - owner-only."""

    message = "You do not have permission to view or modify this answer key."


class IsCollectionOwnerOrAdmin(BasePermission):
    """Write access limited to the collection owner (or an admin).

    Same reasoning as ``IsExerciseOwnerOrAdmin``: reads are governed by
    queryset scoping, so an invisible collection 404s rather than 403s and a
    denial never confirms that someone else's draft folder exists.
    """

    message = "You do not have permission to modify this collection."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in SAFE_METHODS:
            return True
        return user.is_admin or obj.owner_id == user.id
