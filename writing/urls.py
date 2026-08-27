from django.urls import path
from rest_framework.routers import DefaultRouter

from .notebook_views import (
    NotebookCompleteView,
    NotebookListView,
    NotebookPageView,
    NotebookView,
)
from .public_views import PublicWritingExerciseViewSet
from .views import WritingExerciseViewSet

router = DefaultRouter()
router.register("exercises", WritingExerciseViewSet, basename="writing-exercise")
# Anonymous catalogue. Registered on the same router so it inherits the
# trailing-slash and format-suffix conventions of the rest of the surface.
router.register(
    "public/exercises", PublicWritingExerciseViewSet, basename="public-writing-exercise"
)

urlpatterns = [
    # Routed by hand rather than as @action: the notebook is addressed through
    # its workbook and has no id of its own in the path.
    path(
        "exercises/<int:exercise_id>/notebook/",
        NotebookView.as_view(),
        name="writing-notebook",
    ),
    path(
        "exercises/<int:exercise_id>/notebook/complete/",
        NotebookCompleteView.as_view(),
        name="writing-notebook-complete",
    ),
    path(
        "exercises/<int:exercise_id>/pages/<int:page_number>/",
        NotebookPageView.as_view(),
        name="writing-notebook-page",
    ),
    path("notebooks/", NotebookListView.as_view(), name="writing-notebook-list"),
    *router.urls,
]
