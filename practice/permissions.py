from rest_framework.permissions import BasePermission

from common import errors
from listening.models import ExerciseStatus


class ExerciseNotPublished(errors.ConflictError):
    default_code = errors.EXERCISE_NOT_PUBLISHED
    default_detail = "This exercise is not published."


class ExerciseNotReady(errors.ConflictError):
    default_code = errors.EXERCISE_NOT_READY
    default_detail = "This exercise is not ready for practice."


class CanPracticeExercise(BasePermission):
    """Gate practice on an exercise being available to learners.

    Raises rather than returning False. A permission class that denies produces
    403, but "you asked for a real exercise that is not open yet" is a state
    conflict, not an authorisation failure - the documented contract is 409 with
    EXERCISE_NOT_PUBLISHED, which a boolean return cannot express.

    Creators and admins may practise their own unpublished work so they can
    rehearse before releasing it; everyone else needs it published and ready.
    """

    def has_object_permission(self, request, view, obj):
        exercise = getattr(obj, "exercise", obj)
        user = request.user
        privileged = user.is_admin or exercise.owner_id == user.id

        if not privileged and not exercise.is_published:
            raise ExerciseNotPublished()
        if exercise.status != ExerciseStatus.READY:
            raise ExerciseNotReady(
                extra={"status": exercise.status},
            )
        return True
