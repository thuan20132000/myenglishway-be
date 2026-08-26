from django.urls import path

from .views import (
    ExerciseProgressView,
    PracticeAttemptListView,
    PracticeHistoryView,
    PracticeSegmentDetailView,
    PracticeSegmentListView,
    PublicPracticeSegmentListView,
    PublicWorksheetTranscriptView,
    RevealSegmentView,
    StartPracticeView,
    SubmitAnswerView,
    WorksheetAnswersView,
    WorksheetTranscriptView,
    WorksheetView,
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
    path(
        "exercises/<int:exercise_id>/worksheet/",
        WorksheetView.as_view(),
        name="practice-worksheet",
    ),
    path(
        "exercises/<int:exercise_id>/answers/",
        WorksheetAnswersView.as_view(),
        name="practice-answers",
    ),
    path(
        "exercises/<int:exercise_id>/transcript/",
        WorksheetTranscriptView.as_view(),
        name="practice-transcript",
    ),
    path("history/", PracticeHistoryView.as_view(), name="practice-history"),
    path("attempts/", PracticeAttemptListView.as_view(), name="practice-attempts"),
    path(
        "public/exercises/<int:exercise_id>/segments/",
        PublicPracticeSegmentListView.as_view(),
        name="public-practice-segments",
    ),
    path(
        "public/exercises/<int:exercise_id>/transcript/",
        PublicWorksheetTranscriptView.as_view(),
        name="public-practice-transcript",
    ),
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
