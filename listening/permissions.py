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


class IsSegmentOwnerOrAdmin(IsExerciseOwnerOrAdmin):
    """Segment access follows its exercise's owner, for reads as well.

    Unlike exercises, segment payloads contain the transcript, so even safe
    methods are owner-only.
    """

    message = "You do not have permission to view or modify this transcript."

    def has_object_permission(self, request, view, obj):
        user = request.user
        return user.is_admin or self._owner_id(obj) == user.id
