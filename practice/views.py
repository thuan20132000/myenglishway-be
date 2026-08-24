from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.openapi import error_responses
from common.serializers import ErrorSerializer
from listening.models import ExerciseStatus, ListeningExercise, TranscriptSegment

from .permissions import CanPracticeExercise
from .filters import PracticeAttemptFilterSet
from .models import PracticeAttempt
from .serializers import (
    HistoryItemSerializer,
    HistoryQuerySerializer,
    PracticeAttemptSerializer,
    PracticeResultSerializer,
    PracticeSegmentProgressSerializer,
    PracticeSegmentSerializer,
    PracticeStartSerializer,
    PracticeSubmissionSerializer,
    PracticeTranscriptSegmentSerializer,
    ProgressSerializer,
    RevealSerializer,
    WorksheetAnswerSerializer,
    WorksheetSerializer,
)
from .services import (
    TranscriptNotAvailable,
    attempted_segment_count,
    exercise_progress,
    history_details,
    next_segment,
    practice_history,
    practice_segments,
    reveal_segment,
    submit_answer,
)


class PracticeSegmentMixin:
    """Resolve a segment and confirm its exercise is open for practice."""

    permission_classes = [IsAuthenticated, CanPracticeExercise]

    def get_segment(self) -> TranscriptSegment:
        lookup = {"pk": self.kwargs["segment_id"]}
        # When the URL names an exercise too, the segment must belong to it:
        # otherwise a segment id from any exercise would resolve here.
        if "exercise_id" in self.kwargs:
            lookup["exercise_id"] = self.kwargs["exercise_id"]

        segment = get_object_or_404(
            TranscriptSegment.objects.select_related("exercise"), **lookup
        )
        self.check_object_permissions(self.request, segment)
        return segment


class PracticeExerciseMixin:
    """Resolve an exercise and confirm it is open for practice."""

    permission_classes = [IsAuthenticated, CanPracticeExercise]

    def get_exercise(self) -> ListeningExercise:
        exercise = get_object_or_404(
            ListeningExercise.objects.select_related("owner"),
            pk=self.kwargs["exercise_id"],
        )
        self.check_object_permissions(self.request, exercise)
        return exercise


@extend_schema(
    tags=["practice"],
    summary="Submit a dictation answer",
    description=(
        "Scores the answer against the segment transcript and records the "
        "attempt. The transcript is returned in the response - after submission "
        "is the first point a learner is allowed to see it. Repeat submissions "
        "are allowed and each creates a new attempt."
    ),
    request=PracticeSubmissionSerializer,
    responses={
        201: PracticeResultSerializer,
        **error_responses(400, 404, 409),
    },
    examples=[
        OpenApiExample(
            "Answer missing one word",
            request_only=True,
            value={"answer": "I would like accommodation near university"},
        ),
        OpenApiExample(
            "Scored result",
            response_only=True,
            value={
                "attempt_id": 991,
                "segment_id": 501,
                "score": 85.7,
                "user_answer": "I would like accommodation near university",
                "correct_answer": "I would like accommodation near the university.",
                "result": {
                    "correct": ["i", "would", "like", "accommodation", "near", "university"],
                    "missing": [{"expected": "the", "position": 5}],
                    "extra": [],
                    "incorrect": [],
                    "counts": {
                        "correct": 6,
                        "missing": 1,
                        "extra": 0,
                        "incorrect": 0,
                        "expected_total": 7,
                    },
                },
                "created_at": "2026-08-19T10:05:00Z",
            },
        ),
    ],
)
class SubmitAnswerView(PracticeSegmentMixin, APIView):
    serializer_class = PracticeSubmissionSerializer

    def post(self, request, segment_id: int):
        segment = self.get_segment()

        serializer = PracticeSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        attempt = submit_answer(
            user=request.user,
            segment=segment,
            raw_answer=serializer.validated_data["answer"],
        )
        return Response(
            PracticeResultSerializer(attempt).data, status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=["practice"],
    summary="Start or resume practising an exercise",
    description=(
        "Returns the next segment the learner has not yet answered, or null "
        "once every segment has been attempted. Stateless - no session is "
        "created, because where a learner left off is derivable from their "
        "attempts."
    ),
    request=None,
    responses={200: PracticeStartSerializer, **error_responses(404, 409)},
    examples=[
        OpenApiExample(
            "Resuming part-way through",
            response_only=True,
            value={
                "exercise_id": 125,
                "title": "Accommodation Practice",
                "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
                "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
                "total_segments": 24,
                "attempted_segments": 8,
                "current_segment": {
                    "id": 501,
                    "sequence": 9,
                    "start_time": 32.5,
                    "end_time": 38.9,
                    "word_count": 11,
                },
            },
        ),
        OpenApiExample(
            "Every segment attempted",
            response_only=True,
            value={
                "exercise_id": 125,
                "title": "Accommodation Practice",
                "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
                "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
                "total_segments": 24,
                "attempted_segments": 24,
                "current_segment": None,
            },
        ),
    ],
)
class StartPracticeView(PracticeExerciseMixin, APIView):
    serializer_class = PracticeStartSerializer

    def post(self, request, exercise_id: int):
        exercise = self.get_exercise()
        current = next_segment(request.user, exercise)

        audio_url = None
        if exercise.audio_file:
            audio_url = request.build_absolute_uri(exercise.audio_file.url)

        # The handout the learner reads while listening. Null when the creator
        # did not attach one; the client renders audio-only in that case.
        pdf_url = None
        if exercise.pdf_file:
            pdf_url = request.build_absolute_uri(exercise.pdf_file.url)

        payload = {
            "exercise_id": exercise.id,
            "title": exercise.title,
            "audio_url": audio_url,
            "pdf_url": pdf_url,
            "total_segments": exercise.segments.count(),
            "attempted_segments": attempted_segment_count(request.user, exercise),
            "current_segment": (
                PracticeSegmentSerializer(current).data if current else None
            ),
        }
        return Response(payload)


@extend_schema(
    tags=["practice"],
    summary="List an exercise's segments for practice",
    description=(
        "Playback windows only - the transcript is never included. Each row "
        "carries this learner's progress on that segment."
    ),
    responses={
        200: PracticeSegmentProgressSerializer(many=True),
        **error_responses(404, 409),
    },
    examples=[
        OpenApiExample(
            "Two segments, one attempted",
            response_only=True,
            value=[
                {
                    "id": 501,
                    "sequence": 1,
                    "start_time": 0.5,
                    "end_time": 5.8,
                    "word_count": 7,
                    "attempted": True,
                    "best_score": 91.0,
                },
                {
                    "id": 502,
                    "sequence": 2,
                    "start_time": 6.0,
                    "end_time": 12.4,
                    "word_count": 9,
                    "attempted": False,
                    "best_score": None,
                },
            ],
        )
    ],
)
class PracticeSegmentListView(PracticeExerciseMixin, generics.ListAPIView):
    serializer_class = PracticeSegmentProgressSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TranscriptSegment.objects.none()
        return practice_segments(self.request.user, self.get_exercise())


@extend_schema(
    tags=["practice"],
    summary="Retrieve one segment for practice",
    description=(
        "Timings only. The `text` field does not exist on this representation - "
        "the transcript is released by submitting an answer or by revealing it."
    ),
    responses={200: PracticeSegmentSerializer, **error_responses(404, 409)},
    examples=[
        OpenApiExample(
            "Playback window only",
            response_only=True,
            value={
                "id": 501,
                "sequence": 8,
                "start_time": 32.5,
                "end_time": 38.9,
                "word_count": 11,
            },
        )
    ],
)
class PracticeSegmentDetailView(PracticeSegmentMixin, generics.RetrieveAPIView):
    serializer_class = PracticeSegmentSerializer

    def get_object(self):
        return self.get_segment()


@extend_schema(
    tags=["practice"],
    summary="Reveal a segment's transcript",
    description=(
        "For a learner who wants to give up on a segment. Recorded as an "
        "attempt with `kind=reveal` and no score: it appears in history, is "
        "excluded from average score, and does not mark the segment attempted "
        "or completed."
    ),
    request=None,
    responses={200: RevealSerializer, **error_responses(404, 409)},
    examples=[
        OpenApiExample(
            "Transcript released",
            response_only=True,
            value={
                "attempt_id": 992,
                "segment_id": 501,
                "text": "I would like accommodation near the university.",
                "revealed": True,
                "created_at": "2026-08-19T10:06:00Z",
            },
        )
    ],
)
class RevealSegmentView(PracticeSegmentMixin, APIView):
    serializer_class = RevealSerializer

    def post(self, request, segment_id: int):
        segment = self.get_segment()
        attempt = reveal_segment(user=request.user, segment=segment)

        return Response(
            {
                "attempt_id": attempt.id,
                "segment_id": segment.id,
                "text": attempt.expected_snapshot,
                "revealed": True,
                "created_at": attempt.created_at,
            }
        )


@extend_schema(
    tags=["practice"],
    summary="Progress on one exercise",
    description=(
        "Always scoped to the calling user.\n\n"
        "- **attempted**: distinct segments with at least one submission\n"
        "- **completed**: distinct segments whose *best* submission reaches the "
        "completion threshold\n"
        "- **average_score**: mean of each attempted segment's *best* score, so "
        "retrying a segment can only raise it\n"
        "- **revealed**: distinct segments the learner revealed; never counted "
        "as attempted or completed"
    ),
    responses={200: ProgressSerializer, **error_responses(404, 409)},
    examples=[
        OpenApiExample(
            "Part-way through",
            response_only=True,
            value={
                "exercise_id": 125,
                "total_segments": 24,
                "attempted_segments": 10,
                "completed_segments": 8,
                "revealed_segments": 2,
                "progress_percentage": 33.3,
                "average_score": 86.4,
                "last_segment_sequence": 9,
                "last_practiced_at": "2026-08-19T10:06:00Z",
            },
        )
    ],
)
class ExerciseProgressView(PracticeExerciseMixin, APIView):
    serializer_class = ProgressSerializer

    def get(self, request, exercise_id: int):
        exercise = self.get_exercise()
        return Response(exercise_progress(request.user, exercise))


@extend_schema(
    tags=["practice"],
    summary="Practice history, aggregated per exercise",
    description=(
        "The calling user's own history, most recently practised first. "
        "Includes exercises that have since been unpublished, so a learner "
        "never loses sight of work they did."
    ),
    parameters=[
        OpenApiParameter("exercise", int, description="Limit to one exercise."),
        OpenApiParameter("date_from", str, description="Inclusive lower bound, YYYY-MM-DD."),
        OpenApiParameter("date_to", str, description="Inclusive upper bound, YYYY-MM-DD."),
    ],
    responses={200: HistoryItemSerializer(many=True), **error_responses(400)},
    examples=[
        OpenApiExample(
            "One exercise practised",
            response_only=True,
            value={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "exercise": {
                            "id": 125,
                            "title": "Accommodation Practice",
                            "total_segments": 24,
                        },
                        "total_attempts": 34,
                        "last_practiced_at": "2026-08-19T10:06:00Z",
                        "attempted_segments": 20,
                        "completed_segments": 17,
                        "average_score": 87.5,
                    }
                ],
            },
        )
    ],
)
class PracticeHistoryView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = HistoryItemSerializer
    # History rows are grouped aggregates, not model instances; the empty
    # queryset exists only so schema generation can introspect the view.
    queryset = PracticeAttempt.objects.none()

    def get(self, request):
        query = HistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data

        rows = practice_history(
            request.user,
            exercise_id=filters.get("exercise"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
        )

        # Paginate the grouped rows in SQL, then do the per-segment work for
        # this page only.
        page = self.paginate_queryset(rows)
        details = history_details(
            request.user,
            list(page if page is not None else rows),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
        )

        if page is not None:
            return self.get_paginated_response(details)
        return Response(details)


@extend_schema(
    tags=["practice"],
    summary="List the caller's individual attempts",
    description=(
        "Always filtered to the calling user - another learner's attempts are "
        "not addressable through this endpoint."
    ),
    parameters=[
        OpenApiParameter("exercise", int, description="Limit to one exercise."),
        OpenApiParameter("segment", int, description="Limit to one segment."),
        OpenApiParameter("kind", str, enum=["submission", "reveal"]),
        OpenApiParameter("date_from", str, description="Inclusive lower bound, YYYY-MM-DD."),
        OpenApiParameter("date_to", str, description="Inclusive upper bound, YYYY-MM-DD."),
    ],
    responses={200: PracticeAttemptSerializer(many=True), **error_responses(400)},
)
class PracticeAttemptListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PracticeAttemptSerializer
    filterset_class = PracticeAttemptFilterSet
    ordering_fields = ["created_at", "score"]
    ordering = ["-created_at"]
    queryset = PracticeAttempt.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PracticeAttempt.objects.none()
        # Ownership is enforced in the queryset, not by an object permission:
        # a row that is not the caller's is simply not addressable, so a
        # missing check yields an empty result rather than a leak.
        return (
            PracticeAttempt.objects.filter(user=self.request.user)
            .select_related("exercise", "segment")
            .order_by("-created_at")
        )


# --------------------------------------------------------------------------
# Worksheet mode
#
# Read-only, unscored, and recording nothing. Deliberately separate views
# rather than options on the dictation endpoints: the two modes share only the
# exercise they run against.
# --------------------------------------------------------------------------


@extend_schema(
    tags=["practice"],
    summary="Open an exercise as a worksheet",
    description=(
        "Everything the worksheet page needs in one request: the audio, the PDF "
        "question sheet, and which question numbers to render inputs for.\n\n"
        "`question_numbers` lists the numbers as printed rather than a count, "
        "because a sheet may cover questions 11-20. It is empty when the creator "
        "has not written an answer key; the page is still usable as audio plus "
        "question sheet."
    ),
    responses={200: WorksheetSerializer, **error_responses(403, 404, 409)},
    examples=[
        OpenApiExample(
            "A Section 2 sheet",
            response_only=True,
            value={
                "exercise_id": 125,
                "title": "Section 2 - Museum tour",
                "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
                "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
                "duration": 184.2,
                "question_numbers": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                "has_transcript": True,
            },
        )
    ],
)
class WorksheetView(PracticeExerciseMixin, APIView):
    serializer_class = WorksheetSerializer

    def get(self, request, exercise_id: int):
        exercise = self.get_exercise()

        def absolute(file_field):
            return request.build_absolute_uri(file_field.url) if file_field else None

        return Response(
            {
                "exercise_id": exercise.id,
                "title": exercise.title,
                "audio_url": absolute(exercise.audio_file),
                "pdf_url": absolute(exercise.pdf_file),
                "duration": exercise.duration,
                "question_numbers": list(
                    exercise.answers.order_by("number").values_list("number", flat=True)
                ),
                "has_transcript": exercise.segments.exists(),
            }
        )


@extend_schema(
    tags=["practice"],
    summary="Read the answer key",
    description=(
        "The creator's answers, for the learner to check their sheet against. "
        "Nothing is scored - the answers are shown beside what the learner typed "
        "and the judgement is theirs.\n\n"
        "A separate request from the worksheet so the answers are not sitting in "
        "the page payload while the learner is still working. This is a UI "
        "affordance, not a security boundary: the endpoint is open to anyone who "
        "may practise the exercise, whenever they ask."
    ),
    responses={200: WorksheetAnswerSerializer(many=True), **error_responses(403, 404, 409)},
    examples=[
        OpenApiExample(
            "Three answers",
            response_only=True,
            value=[
                {"number": 11, "text": "library"},
                {"number": 12, "text": "9.30"},
                {"number": 13, "text": "blue"},
            ],
        )
    ],
)
class WorksheetAnswersView(PracticeExerciseMixin, APIView):
    serializer_class = WorksheetAnswerSerializer

    def get(self, request, exercise_id: int):
        exercise = self.get_exercise()
        answers = exercise.answers.order_by("number")
        return Response(WorksheetAnswerSerializer(answers, many=True).data)


@extend_schema(
    tags=["practice"],
    summary="Read the full transcript",
    description=(
        "The whole transcript, in order, for a learner who wants to read along.\n\n"
        "Unlike `reveal/`, this records nothing: worksheet mode has no notion of "
        "giving up, so looking at the transcript is not an event worth logging. "
        "The dictation endpoints keep their own text-free payloads."
    ),
    responses={
        200: PracticeTranscriptSegmentSerializer(many=True),
        **error_responses(403, 404, 409),
    },
)
class WorksheetTranscriptView(PracticeExerciseMixin, APIView):
    serializer_class = PracticeTranscriptSegmentSerializer

    def get(self, request, exercise_id: int):
        exercise = self.get_exercise()
        segments = exercise.segments.order_by("sequence")
        if not segments.exists():
            raise TranscriptNotAvailable()
        return Response(PracticeTranscriptSegmentSerializer(segments, many=True).data)


@extend_schema(
    tags=["public"],
    summary="List a published exercise's segments for practice (no authentication)",
    description=(
        "The anonymous equivalent of the practice segment list: playback "
        "windows only, and without the per-learner `attempted`/`best_score` "
        "columns, which need an account. Scoring an answer, revealing a "
        "transcript and recording history all require signing in.\n\n"
        "The exercise must be published and `ready`; anything else returns 404 "
        "rather than the 409 the authenticated endpoint gives, because an "
        "anonymous caller is not entitled to learn that a draft exists."
    ),
    responses={200: PracticeSegmentSerializer(many=True), 404: ErrorSerializer},
    examples=[
        OpenApiExample(
            "Two playback windows",
            response_only=True,
            value=[
                {"id": 501, "sequence": 1, "start_time": 0.5, "end_time": 5.8, "word_count": 7},
                {"id": 502, "sequence": 2, "start_time": 6.0, "end_time": 12.4, "word_count": 9},
            ],
        )
    ],
)
class PublicPracticeSegmentListView(generics.ListAPIView):
    """Segment timings for anyone, gated on the exercise being open to learners.

    Deliberately does not reuse ``PracticeExerciseMixin``: that path runs
    ``CanPracticeExercise``, which reads ``user.is_admin`` and distinguishes
    "not published" from "not ready" in the response. Both are wrong here - an
    anonymous caller has no role, and telling them why an id is unavailable
    leaks the existence of unpublished work.
    """

    serializer_class = PracticeSegmentSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TranscriptSegment.objects.none()

        exercise = get_object_or_404(
            ListeningExercise,
            pk=self.kwargs["exercise_id"],
            is_published=True,
            status=ExerciseStatus.READY,
        )
        return exercise.segments.order_by("sequence")
