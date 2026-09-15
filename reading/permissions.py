from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsPassageOwnerOrAdmin(BasePermission):
    """Write access limited to the passage owner (or an admin).

    Read access is left to queryset scoping: a passage the caller may not see
    is absent from the queryset and yields 404, so a 403 here never confirms
    the existence of someone else's draft.
    """

    message = "You do not have permission to modify this passage."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in SAFE_METHODS:
            return True
        return user.is_admin or obj.owner_id == user.id


class IsSessionOwner(BasePermission):
    """Owner-only, reads included - and admins are not exempt.

    A session is personal performance data. Being able to inspect it is not
    part of running the service, matching writing notebooks.
    """

    message = "A reading session is private to the learner who ran it."

    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id
