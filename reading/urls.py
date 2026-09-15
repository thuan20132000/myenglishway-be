from django.urls import path
from rest_framework.routers import DefaultRouter

from .public_views import PublicReadingExerciseViewSet
from .session_views import ExerciseSessionListCreateView, SessionFinishView
from .views import ReadingExerciseViewSet

router = DefaultRouter()
router.register("exercises", ReadingExerciseViewSet, basename="reading-exercise")
router.register(
    "public/exercises", PublicReadingExerciseViewSet, basename="public-reading-exercise"
)

urlpatterns = [
    path(
        "exercises/<int:exercise_id>/sessions/",
        ExerciseSessionListCreateView.as_view(),
        name="reading-session-list",
    ),
    path(
        "sessions/<int:pk>/",
        SessionFinishView.as_view(),
        name="reading-session-detail",
    ),
    *router.urls,
]
