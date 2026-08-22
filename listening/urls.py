from django.urls import path
from rest_framework.routers import DefaultRouter

from .answer_views import ExerciseAnswerKeyView
from .collection_views import ExerciseCollectionViewSet
from .segment_views import ExerciseSegmentViewSet, SegmentViewSet
from .views import ListeningExerciseViewSet

router = DefaultRouter()
router.register("exercises", ListeningExerciseViewSet, basename="exercise")
router.register("segments", SegmentViewSet, basename="segment")
router.register("collections", ExerciseCollectionViewSet, basename="collection")

# Routed by hand rather than as @action: removing one member needs the
# exercise id in the path, which the router will not generate.
collection_members = ExerciseCollectionViewSet.as_view({"put": "replace_members"})
collection_member = ExerciseCollectionViewSet.as_view({"delete": "remove_member"})

exercise_segments = ExerciseSegmentViewSet.as_view({"get": "list", "post": "create"})
exercise_segments_reorder = ExerciseSegmentViewSet.as_view({"post": "reorder"})

urlpatterns = [
    path(
        "exercises/<int:exercise_id>/segments/",
        exercise_segments,
        name="exercise-segments",
    ),
    path(
        "exercises/<int:exercise_id>/segments/reorder/",
        exercise_segments_reorder,
        name="exercise-segments-reorder",
    ),
    path(
        "exercises/<int:exercise_id>/answers/",
        ExerciseAnswerKeyView.as_view(),
        name="exercise-answer-key",
    ),
    path(
        "collections/<int:pk>/members/",
        collection_members,
        name="collection-members",
    ),
    path(
        "collections/<int:pk>/members/<int:exercise_id>/",
        collection_member,
        name="collection-member",
    ),
    *router.urls,
]
