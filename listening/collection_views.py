"""The collections endpoint.

A separate module from ``views.py``, which is already long and entirely about
the exercise lifecycle. The structure here deliberately mirrors
``ListeningExerciseViewSet``: visibility in ``get_queryset`` so invisible rows
404 rather than 403, a serializer per action, and state transitions delegated
to the service layer.
"""

from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import mixins, viewsets
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.openapi import error_responses
from common.permissions import IsCreator
from common.serializers import ErrorSerializer

from .collection_services import (
    create_collection,
    publish_collection,
    remove_member,
    replace_members,
    unpublish_collection,
    update_collection,
)
from .models import CollectionMembership, ExerciseCollection, ListeningExercise
from .permissions import IsCollectionOwnerOrAdmin
from .serializers import (
    CollectionCreateSerializer,
    CollectionDetailSerializer,
    CollectionListSerializer,
    CollectionMembersSerializer,
    CollectionPublicationSerializer,
    CollectionUpdateSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=["listening"],
        summary="List root collections visible to the caller",
        description=(
            "Only roots are listed: a child collection is reached by opening its "
            "parent. Students see published collections, creators additionally see "
            "their own in any state, admins see everything."
        ),
        responses={200: CollectionListSerializer(many=True), **error_responses(400)},
        examples=[
            OpenApiExample(
                "One page of results",
                response_only=True,
                value={
                    "count": 2,
                    "next": None,
                    "previous": None,
                    "results": [
                        {
                            "id": 3,
                            "title": "Cambridge IELTS 18",
                            "owner": {"id": 3, "full_name": "Ben Ito"},
                            "is_published": True,
                            "position": 1,
                            "child_count": 4,
                            "member_count": 0,
                            "created_at": "2026-08-19T09:00:00Z",
                        }
                    ],
                },
            )
        ],
    ),
    retrieve=extend_schema(
        tags=["listening"],
        summary="Retrieve one collection with its contents",
        description=(
            "`children` holds nested collections, `members` the exercises filed "
            "directly here. A collection never has both. Each list is filtered to "
            "what the caller may see."
        ),
        responses={200: CollectionDetailSerializer, 404: ErrorSerializer},
    ),
    create=extend_schema(
        tags=["listening"],
        summary="Create a collection",
        description=(
            "Creator role required. Omit `parent` for a root (a book, or a flat "
            "category); supply it to add a test to a book. Nesting stops there."
        ),
        responses={
            201: CollectionDetailSerializer,
            **error_responses(400, 403, 409),
        },
    ),
    partial_update=extend_schema(
        tags=["listening"],
        summary="Edit a collection or move it under a new parent",
        description=(
            "Owner or admin only. `is_published` cannot be set here - use the "
            "publish and unpublish actions. Setting `parent` re-files the "
            "collection; a collection that has children of its own cannot be "
            "moved under a parent, because its children would land three levels "
            "deep."
        ),
        responses={
            200: CollectionDetailSerializer,
            **error_responses(400, 403, 404, 409),
        },
    ),
    destroy=extend_schema(
        tags=["listening"],
        summary="Delete a collection and its child collections",
        description=(
            "Owner or admin only. Child collections and memberships go with it; "
            "the exercises themselves are left alone and simply become ungrouped."
        ),
        responses={204: None, **error_responses(403, 404)},
    ),
)
class ExerciseCollectionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # Declared so drf-spectacular can derive the model without executing
    # get_queryset(); the real scoping happens there.
    queryset = ExerciseCollection.objects.none()
    permission_classes = [IsAuthenticated, IsCollectionOwnerOrAdmin]
    filterset_fields = ["is_published", "owner"]
    search_fields = ["title", "description"]
    ordering_fields = ["position", "created_at", "title"]
    ordering = ["position", "id"]
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]

    serializer_classes = {
        "list": CollectionListSerializer,
        "retrieve": CollectionDetailSerializer,
        "create": CollectionCreateSerializer,
        "partial_update": CollectionUpdateSerializer,
        "replace_members": CollectionMembersSerializer,
    }

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, CollectionDetailSerializer)

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), IsCreator()]
        return super().get_permissions()

    def get_queryset(self):
        """Scope by visibility, then annotate and prefetch what serializers need."""
        if getattr(self, "swagger_fake_view", False):
            return ExerciseCollection.objects.none()

        user = self.request.user
        queryset = ExerciseCollection.objects.select_related("owner", "parent").annotate(
            child_count=Count("children", distinct=True),
            member_count=Count("memberships", distinct=True),
        )

        if not user.is_admin:
            visible = Q(is_published=True)
            if user.is_creator:
                visible |= Q(owner_id=user.id)
            queryset = queryset.filter(visible)

        if self.action == "list":
            # Children are reached by opening their parent, never as top-level rows.
            queryset = queryset.filter(parent__isnull=True)

        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "children",
                    queryset=ExerciseCollection.objects.select_related("owner").annotate(
                        child_count=Count("children", distinct=True),
                        member_count=Count("memberships", distinct=True),
                    ),
                ),
                Prefetch(
                    "memberships",
                    queryset=CollectionMembership.objects.order_by("position"),
                ),
                # Prefetched separately rather than select_related on the line
                # above so the exercises arrive with segment_count annotated,
                # which ListeningExerciseListSerializer requires.
                Prefetch(
                    "memberships__exercise",
                    queryset=ListeningExercise.objects.select_related(
                        "owner", "membership__collection__parent"
                    ).annotate(segment_count=Count("segments", distinct=True)),
                ),
            )
        return queryset

    def _detail_response(self, collection, status_code):
        collection = self.get_queryset().get(pk=collection.pk)
        serializer = CollectionDetailSerializer(
            collection, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status_code)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collection = create_collection(owner=request.user, **serializer.validated_data)
        return self._detail_response(collection, drf_status.HTTP_201_CREATED)

    # Defined directly rather than via UpdateModelMixin: "put" has to stay in
    # http_method_names for the members route, and inheriting the mixin would
    # let the router expose a whole-object PUT on the detail route too.
    def partial_update(self, request, *args, **kwargs):
        collection = self.get_object()
        serializer = self.get_serializer(collection, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        collection = update_collection(collection, **serializer.validated_data)
        return self._detail_response(collection, drf_status.HTTP_200_OK)

    @extend_schema(
        tags=["listening"],
        summary="Publish a collection",
        description=(
            "Lists the folder for learners. Unlike an exercise there are no "
            "preconditions - a collection holds no media to validate - and this "
            "does not cascade: the exercises inside keep whatever publication "
            "state they already had."
        ),
        request=None,
        responses={200: CollectionPublicationSerializer, **error_responses(403, 404, 409)},
    )
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        collection = publish_collection(self.get_object())
        return Response(CollectionPublicationSerializer(collection).data)

    @extend_schema(
        tags=["listening"],
        summary="Unpublish a collection",
        description=(
            "Hides the folder. Idempotent. The member exercises stay published "
            "and individually practisable - this withdraws the shelf, not the "
            "material on it."
        ),
        request=None,
        responses={200: CollectionPublicationSerializer, **error_responses(403, 404)},
    )
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        collection = unpublish_collection(self.get_object())
        return Response(CollectionPublicationSerializer(collection).data)

    @extend_schema(
        tags=["listening"],
        summary="Replace the collection's member exercises",
        description=(
            "Owner or admin only. Send every exercise that should be in the "
            "collection, in display order; position is the array index plus one. "
            "Exercises must be owned by the collection's owner and must not "
            "already belong to a different collection. A collection that holds "
            "child collections cannot hold exercises."
        ),
        request=CollectionMembersSerializer,
        responses={200: CollectionDetailSerializer, **error_responses(400, 403, 404, 409)},
        examples=[
            OpenApiExample(
                "Four parts in order",
                request_only=True,
                value={"exercise_ids": [125, 126, 127, 128]},
            )
        ],
    )
    def replace_members(self, request, pk=None):
        collection = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        replace_members(collection, serializer.validated_data["exercise_ids"])
        return self._detail_response(collection, drf_status.HTTP_200_OK)

    @extend_schema(
        tags=["listening"],
        summary="Remove one exercise from the collection",
        description=(
            "Owner or admin only. The exercise itself is untouched and becomes "
            "ungrouped; the remaining members close the gap in the ordering."
        ),
        responses={
            204: OpenApiResponse(description="Removed."),
            **error_responses(403, 404),
        },
    )
    def remove_member(self, request, pk=None, exercise_id=None):
        collection = self.get_object()
        remove_member(collection, exercise_id)
        return Response(status=drf_status.HTTP_204_NO_CONTENT)
