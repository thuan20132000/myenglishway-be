"""Anonymous, read-only workbook catalogue.

Its own module for the reason ``listening.public_views`` gives: the
authenticated viewset widens what it shows by reading ``user.is_admin``, which
``AnonymousUser`` does not have. One fixed rule here instead - published rows
only, reads only.

There is deliberately **no** public notebook route. The PDF is what publishing
shares; writing something down requires an account.
"""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.permissions import AllowAny

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .filters import WritingExerciseFilterSet
from .models import WritingExercise
from .serializers import WritingExerciseDetailSerializer, WritingExerciseListSerializer
from .views import annotate_for_user

PUBLIC = {"is_published": True}


@extend_schema_view(
    list=extend_schema(
        tags=["public"],
        summary="List published workbooks (no authentication)",
        responses={200: WritingExerciseListSerializer(many=True), **error_responses(400)},
    ),
    retrieve=extend_schema(
        tags=["public"],
        summary="Retrieve one published workbook",
        description="An unpublished workbook returns 404.",
        responses={200: WritingExerciseDetailSerializer, 404: ErrorSerializer},
    ),
)
class PublicWritingExerciseViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = WritingExercise.objects.none()
    permission_classes = [AllowAny]
    authentication_classes = []
    filterset_class = WritingExerciseFilterSet
    search_fields = ["title", "description", "source"]
    ordering_fields = ["created_at", "title", "page_count"]
    ordering = ["-created_at"]
    http_method_names = ["get", "head", "options"]

    serializer_classes = {"list": WritingExerciseListSerializer}

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, WritingExerciseDetailSerializer)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WritingExercise.objects.none()
        queryset = WritingExercise.objects.filter(**PUBLIC).select_related("owner")
        # The caller is always anonymous here, so this fills in the constant
        # "no notebook" progress the shared serializers expect.
        return annotate_for_user(queryset, getattr(self.request, "user", None))
