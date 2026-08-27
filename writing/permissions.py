from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsWorkbookOwnerOrAdmin(BasePermission):
    """Write access limited to the workbook owner (or an admin).

    Read access is left to queryset scoping: a workbook the caller may not see
    is absent from the queryset and yields 404, so a 403 here never confirms
    the existence of someone else's draft.
    """

    message = "You do not have permission to modify this workbook."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if request.method in SAFE_METHODS:
            return True
        return user.is_admin or obj.owner_id == user.id


class IsNotebookOwner(BasePermission):
    """Owner-only, reads included - and admins are not exempt.

    Stricter than anything in ``listening`` on purpose. A transcript is course
    material an admin may reasonably inspect; a notebook is somebody's personal
    writing, and being able to see it is not part of running the service.
    """

    message = "A notebook is private to the learner who wrote it."

    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id
