"""Workbook CRUD and publication.

Two deliberate departures from ``listening.views``:

* **Creating is not creator-gated.** The story that drives this domain is "I
  upload my own book and work through it", so any authenticated user may add a
  workbook. It starts private; only *publishing* it to the catalogue needs the
  creator role.
* **There is no status.** A workbook is usable the moment it has a PDF, and
  ``pdf_file`` is required, so publish has nothing to wait for.
"""

from django.db.models import (
    BooleanField,
    Case,
    Count,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
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

from .filters import WritingExerciseFilterSet
from .models import WritingExercise, WritingNotebook, WritingPage
from .permissions import IsWorkbookOwnerOrAdmin
from .serializers import (
    WritingExerciseCreateSerializer,
    WritingExerciseDetailSerializer,
    WritingExerciseListSerializer,
    WritingExercisePublicationSerializer,
    WritingExerciseUpdateSerializer,
)
from .services import create_exercise, publish_exercise, unpublish_exercise, update_exercise


def annotate_for_user(queryset, user):
    """Attach the caller's own standing to each workbook row.

    Three subqueries rather than joins: a join to notebooks would multiply rows
    and, worse, would have to be filtered to the caller on every use. Anonymous
    callers get the constant "no notebook" shape so the public catalogue can
    share these serializers.
    """
    if user is None or not user.is_authenticated:
        return queryset.annotate(
            has_notebook=Value(False, output_field=BooleanField()),
            pages_started=Value(0, output_field=IntegerField()),
            last_page=Value(None, output_field=IntegerField()),
        )

    notebooks = WritingNotebook.objects.filter(exercise=OuterRef("pk"), user=user)
    started = (
        WritingPage.objects.filter(notebook__exercise=OuterRef("pk"), notebook__user=user)
        .exclude(body="")
        .order_by()
        .values("notebook__exercise")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    return queryset.annotate(
        last_page=Subquery(notebooks.values("last_page")[:1]),
        pages_started=Subquery(started),
    ).annotate(
        has_notebook=Case(
            When(last_page__isnull=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    )


@extend_schema_view(
    list=extend_schema(
        tags=["writing"],
        summary="List workbooks visible to the caller",
        description=(
            "Anyone sees published workbooks plus their own, in any state. "
            "Admins see everything. Rows carry the caller's own progress "
            "(`has_notebook`, `pages_started`, `last_page`) and never a "
            "`pdf_url` - that is on the detail view."
        ),
        parameters=[
            OpenApiParameter("search", str, description="Matches title, description and source."),
            OpenApiParameter("mine", bool, description="Restrict to the caller's own uploads."),
            OpenApiParameter(
                "ordering",
                str,
                description="One of created_at, -created_at, title, -title, page_count.",
            ),
        ],
        responses={200: WritingExerciseListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["writing"],
        summary="Retrieve one workbook",
        description=(
            "Includes `pdf_url`, which the client renders with pdf.js. It is a "
            "plain media URL: fetch it *without* an `Authorization` header."
        ),
        responses={200: WritingExerciseDetailSerializer, 404: ErrorSerializer},
    ),
    create=extend_schema(
        tags=["writing"],
        summary="Upload a workbook",
        description=(
            "Any authenticated user, not just creators - uploading your own "
            "book to work through is the point of this domain. The workbook "
            "starts private. `page_count` is read from the PDF and ignored if "
            "sent."
        ),
        request={"multipart/form-data": WritingExerciseCreateSerializer},
        responses={201: WritingExerciseDetailSerializer, **error_responses(400, 403)},
    ),
    partial_update=extend_schema(
        tags=["writing"],
        summary="Update workbook metadata or replace its PDF",
        description=(
            "Owner or admin only. Replacing `pdf_file` re-reads the page count "
            "and deletes the old file.\n\n"
            "The replacement is refused with 409 once any learner has started a "
            "notebook, or while the workbook is published: page numbers are the "
            "only link between a notebook and the PDF, so a re-paginated "
            "replacement would silently misfile everything already written."
        ),
        request={"multipart/form-data": WritingExerciseUpdateSerializer},
        responses={
            200: WritingExerciseDetailSerializer,
            **error_responses(400, 403, 404, 409),
        },
    ),
    destroy=extend_schema(
        tags=["writing"],
        summary="Delete a workbook and every notebook against it",
        description=(
            "Owner or admin only. Cascades to each learner's notebook and pages "
            "- this destroys their writing and cannot be undone. Prefer "
            "unpublishing to withdraw a workbook."
        ),
        responses={204: None, **error_responses(403, 404)},
    ),
)
class WritingExerciseViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # Declared so drf-spectacular can derive the model without executing
    # get_queryset(); the real scoping happens there.
    queryset = WritingExercise.objects.none()
    permission_classes = [IsAuthenticated, IsWorkbookOwnerOrAdmin]
    filterset_class = WritingExerciseFilterSet
    search_fields = ["title", "description", "source"]
    ordering_fields = ["created_at", "title", "page_count"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    serializer_classes = {
        "list": WritingExerciseListSerializer,
        "create": WritingExerciseCreateSerializer,
        "update": WritingExerciseUpdateSerializer,
        "partial_update": WritingExerciseUpdateSerializer,
    }

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, WritingExerciseDetailSerializer)

    def get_permissions(self):
        if self.action in ("publish", "unpublish"):
            return [IsAuthenticated(), IsCreator(), IsWorkbookOwnerOrAdmin()]
        return super().get_permissions()

    def get_queryset(self):
        """Scope by visibility, then annotate what the serializers need.

        Workbooks the caller may not see are excluded here rather than denied
        by an object permission, so a request for someone else's draft returns
        404 instead of confirming it exists with a 403.
        """
        if getattr(self, "swagger_fake_view", False):
            return WritingExercise.objects.none()

        user = self.request.user
        queryset = WritingExercise.objects.select_related("owner")
        if not user.is_admin:
            # Owner, not is_creator: anyone may upload a private workbook here.
            queryset = queryset.filter(Q(is_published=True) | Q(owner_id=user.id))
        return annotate_for_user(queryset, user)

    def _detail_response(self, exercise, status_code):
        serializer = WritingExerciseDetailSerializer(
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

    def _owned(self):
        """The workbook, with the publish gate that queryset scoping cannot give.

        ``IsWorkbookOwnerOrAdmin`` waves through safe methods, and a published
        workbook is visible to everyone, so without this an authenticated
        creator could publish somebody else's book.
        """
        exercise = self.get_object()
        user = self.request.user
        if not (user.is_admin or exercise.owner_id == user.id):
            raise PermissionDenied("Only the workbook's owner can publish it.")
        return exercise

    @extend_schema(
        tags=["writing"],
        summary="Publish a workbook to the catalogue",
        description=(
            "Owner **and** creator role. Makes the workbook readable by anyone, "
            "including anonymous visitors. Notebooks stay private regardless: "
            "publishing shares the PDF, never anyone's writing."
        ),
        request=None,
        responses={
            200: WritingExercisePublicationSerializer,
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
        return Response(WritingExercisePublicationSerializer(exercise).data)

    @extend_schema(
        tags=["writing"],
        summary="Withdraw a workbook from the catalogue",
        description=(
            "Owner **and** creator role. Idempotent. Notebooks learners have "
            "already started are retained and stay readable by their owners."
        ),
        request=None,
        responses={200: WritingExercisePublicationSerializer, **error_responses(403, 404)},
    )
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        exercise = unpublish_exercise(self._owned())
        return Response(WritingExercisePublicationSerializer(exercise).data)
