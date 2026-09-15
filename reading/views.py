"""Passage CRUD and publication.

Two deliberate departures from ``listening.views``, matching ``writing``:

* **Creating is not creator-gated.** The story that drives this domain is "I
  paste a Part 4 transcript and practise it", so any authenticated user may add
  a passage. It starts private; only *publishing* it to the catalogue needs the
  creator role.
* **There is no status.** A passage is usable the moment it has a body.
"""

from django.db.models import Count, FloatField, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
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

from .filters import ReadingExerciseFilterSet
from .models import ReadingExercise, ReadingSession
from .permissions import IsPassageOwnerOrAdmin
from .serializers import (
    ReadingExerciseCreateSerializer,
    ReadingExerciseDetailSerializer,
    ReadingExerciseListSerializer,
    ReadingExercisePublicationSerializer,
    ReadingExerciseUpdateSerializer,
)
from .services import (
    clear_audio,
    create_exercise,
    publish_exercise,
    unpublish_exercise,
    update_exercise,
)


def annotate_for_user(queryset, user):
    """Attach the caller's own standing to each passage row.

    Subqueries rather than joins: a join to sessions would multiply rows and
    would have to be filtered to the caller on every use. Anonymous callers
    get the constant "no sessions" shape so the public catalogue can share
    these serializers.
    """
    if user is None or not user.is_authenticated:
        return queryset.annotate(
            session_count=Value(0, output_field=IntegerField()),
            last_actual_wpm=Value(None, output_field=FloatField()),
            last_target_wpm=Value(None, output_field=IntegerField()),
        )

    own = ReadingSession.objects.filter(exercise=OuterRef("pk"), user=user)
    finished = own.filter(finished_at__isnull=False).order_by("-finished_at")
    count = own.order_by().values("exercise").annotate(total=Count("pk")).values("total")[:1]
    return queryset.annotate(
        session_count=Coalesce(Subquery(count, output_field=IntegerField()), Value(0)),
        last_actual_wpm=Subquery(finished.values("actual_wpm")[:1]),
        last_target_wpm=Subquery(finished.values("target_wpm")[:1]),
    )


@extend_schema_view(
    list=extend_schema(
        tags=["reading"],
        summary="List passages visible to the caller",
        description=(
            "Anyone sees published passages plus their own, in any state. "
            "Admins see everything. Rows carry the caller's own progress "
            "(`session_count`, `last_actual_wpm`, `last_target_wpm`) and never "
            "a `body` or `audio_url` - those are on the detail view."
        ),
        parameters=[
            OpenApiParameter("search", str, description="Matches title, description and source."),
            OpenApiParameter("mine", bool, description="Restrict to the caller's own uploads."),
            OpenApiParameter(
                "ordering",
                str,
                description="One of created_at, -created_at, title, -title, word_count.",
            ),
        ],
        responses={200: ReadingExerciseListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["reading"],
        summary="Retrieve one passage",
        description=(
            "Includes `body` and `audio_url`. `audio_url` is a plain media URL: "
            "fetch it *without* an `Authorization` header."
        ),
        responses={200: ReadingExerciseDetailSerializer, 404: ErrorSerializer},
    ),
    create=extend_schema(
        tags=["reading"],
        summary="Create a passage",
        description=(
            "Any authenticated user, not just creators - pasting your own "
            "Section 4 transcript to work through is the point of this domain. "
            "The passage starts private. `word_count` is derived from the body "
            "and ignored if sent."
        ),
        request=ReadingExerciseCreateSerializer,
        responses={201: ReadingExerciseDetailSerializer, **error_responses(400, 403)},
    ),
    partial_update=extend_schema(
        tags=["reading"],
        summary="Update passage metadata, body, or audio",
        description=(
            "Owner or admin only. Replacing `audio_file` deletes the old file. "
            "Editing `body` recomputes `word_count`; existing sessions keep the "
            "snapshot they took at start."
        ),
        request=ReadingExerciseUpdateSerializer,
        responses={
            200: ReadingExerciseDetailSerializer,
            **error_responses(400, 403, 404),
        },
    ),
    destroy=extend_schema(
        tags=["reading"],
        summary="Delete a passage and every session against it",
        description=(
            "Owner or admin only. Cascades to each learner's sessions. Prefer "
            "unpublishing to withdraw a passage."
        ),
        responses={204: None, **error_responses(403, 404)},
    ),
)
class ReadingExerciseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # Declared so drf-spectacular can derive the model without executing
    # get_queryset(); the real scoping happens there.
    queryset = ReadingExercise.objects.none()
    permission_classes = [IsAuthenticated, IsPassageOwnerOrAdmin]
    filterset_class = ReadingExerciseFilterSet
    search_fields = ["title", "description", "source"]
    ordering_fields = ["created_at", "title", "word_count"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    serializer_classes = {
        "list": ReadingExerciseListSerializer,
        "create": ReadingExerciseCreateSerializer,
        "update": ReadingExerciseUpdateSerializer,
        "partial_update": ReadingExerciseUpdateSerializer,
    }

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, ReadingExerciseDetailSerializer)

    def get_permissions(self):
        if self.action in ("publish", "unpublish"):
            return [IsAuthenticated(), IsCreator(), IsPassageOwnerOrAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        """Scope by visibility, then annotate what the serializers need.

        Passages the caller may not see are excluded here rather than denied
        by an object permission, so a request for someone else's draft returns
        404 instead of confirming it exists with a 403.
        """
        if getattr(self, "swagger_fake_view", False):
            return ReadingExercise.objects.none()

        user = self.request.user
        queryset = ReadingExercise.objects.select_related("owner")
        if not user.is_admin:
            queryset = queryset.filter(Q(is_published=True) | Q(owner_id=user.id))
        return annotate_for_user(queryset, user)

    def _detail_response(self, exercise, status_code):
        serializer = ReadingExerciseDetailSerializer(
            exercise, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status_code)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exercise = create_exercise(owner=request.user, **serializer.validated_data)
        exercise = annotate_for_user(
            ReadingExercise.objects.select_related("owner").filter(pk=exercise.pk),
            request.user,
        ).get()
        return self._detail_response(exercise, drf_status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        exercise = self.get_object()
        serializer = self.get_serializer(exercise, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        exercise = update_exercise(exercise, **serializer.validated_data)
        exercise = annotate_for_user(
            ReadingExercise.objects.select_related("owner").filter(pk=exercise.pk),
            request.user,
        ).get()
        return self._detail_response(exercise, drf_status.HTTP_200_OK)

    def _owned(self):
        """The passage, with the publish gate that queryset scoping cannot give.

        ``IsPassageOwnerOrAdmin`` waves through safe methods, and a published
        passage is visible to everyone, so without this an authenticated
        creator could publish somebody else's book.
        """
        exercise = self.get_object()
        user = self.request.user
        if not (user.is_admin or exercise.owner_id == user.id):
            raise PermissionDenied("Only the passage's owner can publish it.")
        return exercise

    @extend_schema(
        tags=["reading"],
        summary="Publish a passage to the catalogue",
        description=(
            "Owner **and** creator role. Makes the passage readable by anyone, "
            "including anonymous visitors. Sessions stay private regardless: "
            "publishing shares the body, never anyone's WPM history."
        ),
        request=None,
        responses={
            200: ReadingExercisePublicationSerializer,
            **error_responses(403, 404, 409),
        },
        examples=[
            OpenApiExample(
                "Published",
                response_only=True,
                status_codes=["200"],
                value={"id": 7, "is_published": True, "published_at": "2026-08-26T10:00:00Z"},
            )
        ],
    )
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        exercise = publish_exercise(self._owned())
        return Response(ReadingExercisePublicationSerializer(exercise).data)

    @extend_schema(
        tags=["reading"],
        summary="Withdraw a passage from the catalogue",
        description=(
            "Owner **and** creator role. Idempotent. Sessions learners have "
            "already started are retained and stay readable by their owners."
        ),
        request=None,
        responses={200: ReadingExercisePublicationSerializer, **error_responses(403, 404)},
    )
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        exercise = unpublish_exercise(self._owned())
        return Response(ReadingExercisePublicationSerializer(exercise).data)

    @extend_schema(
        tags=["reading"],
        summary="Remove the optional audio from a passage",
        description="Owner or admin only. Idempotent: deleting when there is no file is 204.",
        request=None,
        responses={204: None, **error_responses(403, 404)},
    )
    @action(detail=True, methods=["delete"], url_path="audio")
    def audio(self, request, pk=None):
        clear_audio(self.get_object())
        return Response(status=drf_status.HTTP_204_NO_CONTENT)
