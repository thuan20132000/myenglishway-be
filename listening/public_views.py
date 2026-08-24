"""Anonymous, read-only catalogue endpoints.

The authenticated viewsets in ``views.py`` and ``collection_views.py`` widen
what they show according to who is asking - a creator sees their own drafts, an
admin sees everything - and every branch of that logic reads ``user.is_admin``,
which an ``AnonymousUser`` does not have. Rather than thread a "no user" case
through all of it, the public surface is its own module with one fixed rule:
published rows only, reads only.

The serializers are shared with the authenticated surface: they already collapse
to the learner view when ``request.user`` is anonymous (no transcript, no
processing diagnostics, no breadcrumb rung the caller may not see), so a public
payload is exactly what a signed-in student would receive.
"""

from django.db.models import Count, F, Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.permissions import AllowAny

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .filters import ListeningExerciseFilterSet
from .models import CollectionMembership, ExerciseCollection, ListeningExercise
from .serializers import (
    CollectionDetailSerializer,
    CollectionListSerializer,
    ListeningExerciseDetailSerializer,
    ListeningExerciseListSerializer,
)

#: No status term is needed alongside it: the ``exercise_published_requires_ready``
#: database constraint makes "published but still processing" unrepresentable.
PUBLIC = Q(is_published=True)


@extend_schema_view(
    list=extend_schema(
        tags=["public"],
        summary="List published root collections (no authentication)",
        description=(
            "The anonymous catalogue. Only published root collections are "
            "listed; a child collection is reached by opening its parent."
        ),
        responses={200: CollectionListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["public"],
        summary="Retrieve one published collection with its contents",
        description=(
            "`children` holds nested collections, `members` the exercises filed "
            "directly here, both filtered to published rows. An unpublished "
            "collection returns 404."
        ),
        responses={200: CollectionDetailSerializer, 404: ErrorSerializer},
    ),
)
class PublicCollectionViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = ExerciseCollection.objects.none()
    permission_classes = [AllowAny]
    authentication_classes = []
    search_fields = ["title", "description"]
    ordering_fields = ["position", "created_at", "title"]
    ordering = ["position", "id"]
    http_method_names = ["get", "head", "options"]

    serializer_classes = {"list": CollectionListSerializer}

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, CollectionDetailSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ExerciseCollection.objects.none()

        queryset = (
            ExerciseCollection.objects.filter(PUBLIC)
            .select_related("owner", "parent")
            .annotate(
                child_count=Count("children", distinct=True),
                member_count=Count("memberships", distinct=True),
            )
        )

        if self.action == "list":
            queryset = queryset.filter(parent__isnull=True)

        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "children",
                    queryset=ExerciseCollection.objects.filter(PUBLIC)
                    .select_related("owner")
                    .annotate(
                        child_count=Count("children", distinct=True),
                        member_count=Count("memberships", distinct=True),
                    ),
                ),
                Prefetch(
                    "memberships",
                    queryset=CollectionMembership.objects.filter(
                        exercise__is_published=True
                    ).order_by("position"),
                ),
                # Separate from the select_related above so the exercises
                # arrive with segment_count annotated, which the list
                # serializer requires.
                Prefetch(
                    "memberships__exercise",
                    queryset=ListeningExercise.objects.select_related(
                        "owner", "membership__collection__parent"
                    ).annotate(segment_count=Count("segments", distinct=True)),
                ),
            )
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=["public"],
        summary="List published exercises (no authentication)",
        description=(
            "Every published exercise, whether or not it is filed under a "
            "collection. Use `?collection=<id>` for one collection's exercises "
            "in membership order (`?ordering=position`), or `?ungrouped=true` "
            "for the ones filed nowhere."
        ),
        parameters=[
            OpenApiParameter("search", str, description="Matches title and description."),
            OpenApiParameter(
                "ordering",
                str,
                description="One of created_at, -created_at, title, -title, duration, position.",
            ),
        ],
        responses={200: ListeningExerciseListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["public"],
        summary="Retrieve one published exercise",
        description=(
            "Metadata plus `audio_url` and `pdf_url`. The transcript is never "
            "included: fetch the segment timings from the public practice "
            "endpoint and score answers by signing in. An unpublished exercise "
            "returns 404."
        ),
        responses={200: ListeningExerciseDetailSerializer, 404: ErrorSerializer},
    ),
)
class PublicExerciseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = ListeningExercise.objects.none()
    permission_classes = [AllowAny]
    authentication_classes = []
    filterset_class = ListeningExerciseFilterSet
    search_fields = ["title", "description"]
    ordering_fields = ["created_at", "title", "duration", "position"]
    ordering = ["-created_at"]
    http_method_names = ["get", "head", "options"]

    serializer_classes = {"list": ListeningExerciseListSerializer}

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, ListeningExerciseDetailSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ListeningExercise.objects.none()

        return (
            ListeningExercise.objects.filter(PUBLIC)
            .select_related("owner", "membership__collection__parent")
            .annotate(
                segment_count=Count("segments", distinct=True),
                answer_count=Count("answers", distinct=True),
                position=F("membership__position"),
            )
        )
