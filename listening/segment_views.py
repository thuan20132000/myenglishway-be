"""Creator-facing transcript segment endpoints.

Two viewsets rather than one nested router: the collection is always addressed
through its exercise (``/exercises/{id}/segments/``) while a single segment has
a stable flat URL (``/segments/{id}/``). Wiring both by hand keeps the URL shape
explicit and avoids a nested-router dependency for two routes.

Every payload here contains transcript text, so access is owner-or-admin for
reads as well as writes - the practice app has its own, text-free serializer.
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .models import ListeningExercise, TranscriptSegment
from .permissions import IsSegmentOwnerOrAdmin
from .serializers import (
    SegmentReorderSerializer,
    TranscriptSegmentCreatorSerializer,
)
from .services import create_segment, delete_segment, reorder_segments, update_segment


class ExerciseSegmentViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Segment collection for one exercise, plus the reorder action."""

    queryset = TranscriptSegment.objects.none()
    serializer_class = TranscriptSegmentCreatorSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # a transcript is read as a whole, never paged

    def get_exercise(self) -> ListeningExercise:
        if not hasattr(self, "_exercise"):
            exercise = get_object_or_404(
                ListeningExercise.objects.select_related("owner"),
                pk=self.kwargs["exercise_id"],
            )
            # Enforced here rather than via queryset scoping so that a
            # non-owner asking for someone else's transcript is refused
            # whether or not the exercise is published.
            self.check_object_permissions(self.request, exercise)
            self._exercise = exercise
        return self._exercise

    def get_permissions(self):
        return [IsAuthenticated(), IsSegmentOwnerOrAdmin()]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TranscriptSegment.objects.none()
        return self.get_exercise().segments.all()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not getattr(self, "swagger_fake_view", False):
            context["exercise"] = self.get_exercise()
        return context

    @extend_schema(
        tags=["listening"],
        summary="List an exercise's transcript segments",
        description="Includes transcript text. Owner or admin only. Not paginated.",
        responses={
            200: TranscriptSegmentCreatorSerializer(many=True),
            403: ErrorSerializer,
            404: ErrorSerializer,
        },
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        tags=["listening"],
        summary="Create a transcript segment",
        description=(
            "`sequence` may be omitted to append after the current highest. "
            "Creating the first segment on an exercise that has audio moves it "
            "to the `ready` status."
        ),
        responses={
            201: TranscriptSegmentCreatorSerializer,
            **error_responses(400, 403, 404, 409),
        },
        examples=[
            OpenApiExample(
                "Append a segment",
                request_only=True,
                value={
                    "start_time": 12.4,
                    "end_time": 18.7,
                    "text": "I'd like to book a room for next weekend.",
                },
            ),
            OpenApiExample(
                "Sequence already taken",
                response_only=True,
                status_codes=["400"],
                value={
                    "code": "DUPLICATE_SEGMENT_SEQUENCE",
                    "detail": "Another segment in this exercise already uses that sequence.",
                    "extra": {"sequence": 3},
                },
            ),
        ],
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        segment = create_segment(self.get_exercise(), **serializer.validated_data)
        return Response(
            self.get_serializer(segment).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["listening"],
        summary="Reorder every segment of an exercise",
        description=(
            "Atomically reassigns sequences. The payload must list every segment "
            "of the exercise exactly once."
        ),
        request=SegmentReorderSerializer,
        examples=[
            OpenApiExample(
                "Swap the first two segments",
                request_only=True,
                value={"ordering": [{"id": 502, "sequence": 1}, {"id": 501, "sequence": 2}]},
            )
        ],
        responses={
            200: TranscriptSegmentCreatorSerializer(many=True),
            400: ErrorSerializer,
            403: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def reorder(self, request, *args, **kwargs):
        exercise = self.get_exercise()
        serializer = SegmentReorderSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        reorder_segments(exercise, serializer.validated_data["ordering"])

        segments = exercise.segments.all()
        return Response(TranscriptSegmentCreatorSerializer(segments, many=True).data)


@extend_schema_view(
    retrieve=extend_schema(
        tags=["listening"],
        summary="Retrieve one transcript segment",
        responses={200: TranscriptSegmentCreatorSerializer, 403: ErrorSerializer, 404: ErrorSerializer},
    ),
    partial_update=extend_schema(
        tags=["listening"],
        summary="Correct transcript text or adjust timestamps",
        description=(
            "Owner or admin only. `word_count` is recomputed. Past attempts keep "
            "their own snapshot of the transcript, so correcting a typo never "
            "changes a learner's historical score."
        ),
        responses={
            200: TranscriptSegmentCreatorSerializer,
            400: ErrorSerializer,
            403: ErrorSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    ),
    destroy=extend_schema(
        tags=["listening"],
        summary="Delete a transcript segment",
        description=(
            "Removing the last segment returns the exercise to `uploaded` and "
            "unpublishes it, since a published exercise must be `ready`."
        ),
        responses={204: None, 403: ErrorSerializer, 404: ErrorSerializer, 409: ErrorSerializer},
    ),
)
class SegmentViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Flat detail routes for a single segment."""

    queryset = TranscriptSegment.objects.select_related("exercise__owner")
    serializer_class = TranscriptSegmentCreatorSerializer
    permission_classes = [IsAuthenticated, IsSegmentOwnerOrAdmin]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.detail and not getattr(self, "swagger_fake_view", False):
            context["exercise"] = self.get_object().exercise
        return context

    def update(self, request, *args, **kwargs):
        segment = self.get_object()
        serializer = self.get_serializer(segment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        segment = update_segment(segment, **serializer.validated_data)
        return Response(self.get_serializer(segment).data)

    def destroy(self, request, *args, **kwargs):
        delete_segment(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
