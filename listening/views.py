from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, viewsets
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.openapi import error_responses
from common.permissions import IsCreator
from common.serializers import ErrorSerializer

from .filters import ListeningExerciseFilterSet
from .models import ExerciseStatus, ListeningExercise, TranscriptSegment
from .permissions import IsExerciseOwnerOrAdmin
from .serializers import (
    ExercisePublicationSerializer,
    ExerciseStatusSerializer,
    ListeningExerciseCreateSerializer,
    ListeningExerciseDetailSerializer,
    ListeningExerciseListSerializer,
    ListeningExerciseUpdateSerializer,
)
from .services import (
    ExerciseLocked,
    ExerciseNotReady,
    ExerciseProcessing,
    create_exercise,
    publish_exercise,
    schedule_transcription,
    unpublish_exercise,
    update_exercise,
)


@extend_schema_view(
    list=extend_schema(
        tags=["listening"],
        summary="List exercises visible to the caller",
        description=(
            "Students see published exercises. Creators additionally see their own "
            "exercises in any status. Admins see everything. Segments are never "
            "included in list rows."
        ),
        parameters=[
            OpenApiParameter("search", str, description="Matches title and description."),
            OpenApiParameter(
                "ordering",
                str,
                description="One of created_at, -created_at, title, -title, duration.",
            ),
        ],
        responses={
            200: ListeningExerciseListSerializer(many=True),
            **error_responses(400),
        },
        examples=[
            OpenApiExample(
                "One page of results",
                response_only=True,
                value={
                    "count": 42,
                    "next": "http://localhost:8000/api/v1/listening/exercises/?page=2",
                    "previous": None,
                    "results": [
                        {
                            "id": 125,
                            "title": "Accommodation Practice",
                            "owner": {"id": 3, "full_name": "Ben Ito"},
                            "status": "ready",
                            "is_published": True,
                            "duration": 184.2,
                            "language": "en",
                            "segment_count": 24,
                            "created_at": "2026-08-19T09:00:00Z",
                        }
                    ],
                },
            )
        ],
    ),
    retrieve=extend_schema(
        tags=["listening"],
        summary="Retrieve one exercise",
        description=(
            "`segments` (including transcript text) is present only when the caller "
            "owns the exercise or is an admin."
        ),
        responses={200: ListeningExerciseDetailSerializer, 404: ErrorSerializer},
    ),
    create=extend_schema(
        tags=["listening"],
        summary="Create an exercise",
        description="Creator role required. Audio may be uploaded now or added later.",
        request={"multipart/form-data": ListeningExerciseCreateSerializer},
        responses={
            201: ListeningExerciseDetailSerializer,
            400: ErrorSerializer,
            403: ErrorSerializer,
        },
    ),
    partial_update=extend_schema(
        tags=["listening"],
        summary="Update exercise metadata or replace its audio",
        description=(
            "Owner or admin only. `status` and `is_published` cannot be set here - "
            "use the publish and unpublish actions.\n\n"
            "Replacing `audio_file` resets the exercise to `uploaded` and clears "
            "`duration`, because the existing segments describe the old audio. "
            "Supplying `duration` in the same request keeps the new value. "
            "Replacing audio on a published exercise returns 409; unpublish first."
        ),
        request={"multipart/form-data": ListeningExerciseUpdateSerializer},
        responses={
            200: ListeningExerciseDetailSerializer,
            400: ErrorSerializer,
            403: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    ),
    destroy=extend_schema(
        tags=["listening"],
        summary="Delete an exercise and everything derived from it",
        description=(
            "Owner or admin only. Cascades to the exercise's segments and to "
            "every learner's attempts against them. This cannot be undone - "
            "prefer unpublishing if the intent is only to withdraw it."
        ),
        responses={204: None, **error_responses(403, 404)},
    ),
)
class ListeningExerciseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # IsAuthenticated is listed explicitly: setting permission_classes here
    # replaces the project default rather than adding to it.
    # Declared so drf-spectacular can derive the model without executing
    # get_queryset(); the real scoping happens there.
    queryset = ListeningExercise.objects.none()
    permission_classes = [IsAuthenticated, IsExerciseOwnerOrAdmin]
    filterset_class = ListeningExerciseFilterSet
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "title", "duration"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    serializer_classes = {
        "list": ListeningExerciseListSerializer,
        "retrieve": ListeningExerciseDetailSerializer,
        "create": ListeningExerciseCreateSerializer,
        "update": ListeningExerciseUpdateSerializer,
        "partial_update": ListeningExerciseUpdateSerializer,
    }

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, ListeningExerciseDetailSerializer)

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), IsCreator()]
        return super().get_permissions()

    def get_queryset(self):
        """Scope by visibility, then annotate what the serializers need.

        Exercises the caller may not see are excluded here rather than denied
        by an object permission, so a request for someone else's draft returns
        404 instead of confirming it exists with a 403.
        """
        if getattr(self, "swagger_fake_view", False):
            return ListeningExercise.objects.none()

        user = self.request.user
        queryset = ListeningExercise.objects.select_related("owner").annotate(
            segment_count=Count("segments", distinct=True)
        )

        if not user.is_admin:
            visible = Q(is_published=True)
            if user.is_creator:
                visible |= Q(owner_id=user.id)
            queryset = queryset.filter(visible)

        if self.action == "retrieve":
            # Only the detail view renders segments, and only for owner/admin.
            queryset = queryset.prefetch_related(
                Prefetch(
                    "segments",
                    queryset=TranscriptSegment.objects.order_by("sequence"),
                )
            )
        return queryset

    def _detail_response(self, exercise, status_code):
        exercise.segment_count = exercise.segments.count()
        serializer = ListeningExerciseDetailSerializer(
            exercise, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status_code)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exercise = create_exercise(owner=request.user, **serializer.validated_data)
        return self._detail_response(exercise, drf_status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        exercise = self.get_object()
        serializer = self.get_serializer(exercise, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        exercise = update_exercise(exercise, **serializer.validated_data)
        return self._detail_response(exercise, drf_status.HTTP_200_OK)

    @extend_schema(
        tags=["listening"],
        summary="Publish an exercise",
        description=(
            "Makes the exercise available to learners. Requires audio, a known "
            "duration, at least one segment whose timestamps fit inside that "
            "duration, and the `ready` status. A custom action rather than a "
            "PATCH of `is_published`: this is a state transition with "
            "preconditions, and folding it into partial update would mean "
            "running publish validation on every metadata edit."
        ),
        request=None,
        responses={200: ExercisePublicationSerializer, **error_responses(403, 404, 409)},
        examples=[
            OpenApiExample(
                "Published",
                response_only=True,
                status_codes=["200"],
                value={
                    "id": 125,
                    "status": "ready",
                    "is_published": True,
                    "published_at": "2026-08-19T10:00:00Z",
                },
            ),
            OpenApiExample(
                "Preconditions unmet",
                response_only=True,
                status_codes=["409"],
                value={
                    "code": "EXERCISE_NOT_READY",
                    "detail": "This exercise does not meet the requirements for publishing.",
                    "extra": {"reasons": ["missing_duration", "no_segments"]},
                },
            ),
        ],
    )
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        exercise = publish_exercise(self.get_object())
        return Response(ExercisePublicationSerializer(exercise).data)

    @extend_schema(
        tags=["listening"],
        summary="Unpublish an exercise",
        description=(
            "Withdraws the exercise from learners. Idempotent - unpublishing an "
            "already-unpublished exercise succeeds. Existing practice attempts "
            "are retained."
        ),
        request=None,
        responses={200: ExercisePublicationSerializer, **error_responses(403, 404)},
    )
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        exercise = unpublish_exercise(self.get_object())
        return Response(ExercisePublicationSerializer(exercise).data)

    @extend_schema(
        tags=["listening"],
        summary="Transcription status",
        description=(
            "Owner or admin only. A small payload intended for polling while "
            "`status` is `processing`; when transcription fails, "
            "`processing_error` explains why."
        ),
        responses={200: ExerciseStatusSerializer, **error_responses(403, 404)},
        examples=[
            OpenApiExample(
                "Transcription in flight",
                response_only=True,
                value={
                    "id": 125,
                    "status": "processing",
                    "is_published": False,
                    "duration": None,
                    "segment_count": 0,
                    "processing_started_at": "2026-08-19T10:00:00Z",
                    "processing_error": "",
                    "transcription_provider": "",
                },
            ),
            OpenApiExample(
                "Transcription failed",
                response_only=True,
                value={
                    "id": 125,
                    "status": "failed",
                    "is_published": False,
                    "duration": None,
                    "segment_count": 0,
                    "processing_started_at": "2026-08-19T10:00:00Z",
                    "processing_error": "Audio is 41.2 MB, above the 25 MB limit for automatic transcription.",
                    "transcription_provider": "",
                },
            ),
        ],
    )
    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        exercise = self.get_object()
        # IsExerciseOwnerOrAdmin deliberately permits every safe method, so it
        # cannot gate a GET. Checked explicitly here: this payload carries
        # processing diagnostics that belong to the creator alone.
        if not (request.user.is_admin or exercise.owner_id == request.user.id):
            raise PermissionDenied("You do not have permission to view this exercise's status.")
        exercise.segment_count = exercise.segments.count()
        return Response(ExerciseStatusSerializer(exercise).data)

    @extend_schema(
        tags=["listening"],
        summary="Transcribe the audio again",
        description=(
            "Owner or admin only. Queues automatic transcription, replacing any "
            "existing transcript. Use it to retry after a failure, or to "
            "transcribe an exercise whose segments were written by hand.\n\n"
            "Returns 202 with the exercise moved to `processing`. Poll "
            "`status/` for the outcome."
        ),
        request=None,
        responses={202: ExerciseStatusSerializer, **error_responses(403, 404, 409)},
    )
    @action(detail=True, methods=["post"])
    def transcribe(self, request, pk=None):
        exercise = self.get_object()

        if exercise.is_published:
            raise ExerciseLocked(
                "Unpublish the exercise before transcribing it again.",
                extra={"reasons": ["exercise_is_published"]},
            )
        if not exercise.has_audio:
            raise ExerciseNotReady(
                "This exercise has no audio to transcribe.",
                extra={"reasons": ["missing_audio"]},
            )
        if exercise.status == ExerciseStatus.PROCESSING:
            raise ExerciseProcessing()

        if not schedule_transcription(exercise):
            raise ExerciseNotReady(
                "Automatic transcription is disabled on this server.",
                extra={"reasons": ["transcription_disabled"]},
            )

        exercise.segment_count = exercise.segments.count()
        return Response(
            ExerciseStatusSerializer(exercise).data, status=drf_status.HTTP_202_ACCEPTED
        )
