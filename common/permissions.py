"""Reusable permission classes.

Object-level rules live next to the domain they guard (``listening.permissions``,
``practice.permissions``); only role gates that are genuinely cross-app belong
here.
"""

from rest_framework.permissions import BasePermission


class IsCreator(BasePermission):
    """Allows users who may author exercises (creator or admin roles)."""

    message = "Only creators can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_creator)


class IsAdminRole(BasePermission):
    """Allows only users holding the admin role."""

    message = "Administrator access is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_admin)
