from django.urls import path

from .views import (
    ExerciseProgressView,
    PracticeAttemptListView,
    PracticeHistoryView,
    PracticeSegmentDetailView,
    PracticeSegmentListView,
    RevealSegmentView,
    StartPracticeView,
    SubmitAnswerView,
)

urlpatterns = [
    path(
        "exercises/<int:exercise_id>/start/",
        StartPracticeView.as_view(),
        name="practice-start",
    ),
    path(
        "exercises/<int:exercise_id>/segments/",
        PracticeSegmentListView.as_view(),
        name="practice-segments",
    ),
    path(
        "exercises/<int:exercise_id>/segments/<int:segment_id>/",
        PracticeSegmentDetailView.as_view(),
        name="practice-segment-detail",
    ),
    path(
        "exercises/<int:exercise_id>/progress/",
        ExerciseProgressView.as_view(),
        name="practice-progress",
    ),
    path("history/", PracticeHistoryView.as_view(), name="practice-history"),
    path("attempts/", PracticeAttemptListView.as_view(), name="practice-attempts"),
    path(
        "segments/<int:segment_id>/submit/",
        SubmitAnswerView.as_view(),
        name="segment-submit",
    ),
    path(
        "segments/<int:segment_id>/reveal/",
        RevealSegmentView.as_view(),
        name="segment-reveal",
    ),
]
