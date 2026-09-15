"""Anonymous, read-only passage catalogue.

Its own module for the reason ``listening.public_views`` gives: the
authenticated viewset widens what it shows by reading ``user.is_admin``, which
``AnonymousUser`` does not have. One fixed rule here instead - published rows
only, reads only.

There is deliberately **no** public session route. The body is what publishing
shares; recording a WPM run requires an account.
"""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.permissions import AllowAny

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .filters import ReadingExerciseFilterSet
from .models import ReadingExercise
from .serializers import ReadingExerciseDetailSerializer, ReadingExerciseListSerializer
from .views import annotate_for_user

PUBLIC = {"is_published": True}


@extend_schema_view(
    list=extend_schema(
        tags=["public"],
        summary="List published passages (no authentication)",
        responses={200: ReadingExerciseListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["public"],
        summary="Retrieve one published passage",
        description="An unpublished passage returns 404. Includes `body`.",
        responses={200: ReadingExerciseDetailSerializer, 404: ErrorSerializer},
    ),
)
class PublicReadingExerciseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = ReadingExercise.objects.none()
    permission_classes = [AllowAny]
    authentication_classes = []
    filterset_class = ReadingExerciseFilterSet
    search_fields = ["title", "description", "source"]
    ordering_fields = ["created_at", "title", "word_count"]
    ordering = ["-created_at"]
    http_method_names = ["get", "head", "options"]

    serializer_classes = {"list": ReadingExerciseListSerializer}

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, ReadingExerciseDetailSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReadingExercise.objects.none()
        queryset = ReadingExercise.objects.filter(**PUBLIC).select_related("owner")
        return annotate_for_user(queryset, getattr(self.request, "user", None))
