from django.urls import path
from rest_framework.routers import DefaultRouter

from .segment_views import ExerciseSegmentViewSet, SegmentViewSet
from .views import ListeningExerciseViewSet

router = DefaultRouter()
router.register("exercises", ListeningExerciseViewSet, basename="exercise")
router.register("segments", SegmentViewSet, basename="segment")

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
    *router.urls,
]
