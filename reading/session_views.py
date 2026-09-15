"""The learner's side of paced reading.

Every endpoint here operates on the caller's own sessions and no one else's.
Sessions are append-only runs, not a living notebook: starting creates a row,
finishing writes ``actual_wpm`` on that row. Admins are not exempt.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .models import ReadingExercise, ReadingSession
from .serializers import (
    ReadingSessionFinishSerializer,
    ReadingSessionSerializer,
    ReadingSessionStartSerializer,
)
from .services import finish_session, start_session


class ExerciseVisibilityMixin:
    """Resolve a passage the caller may see, 404 otherwise."""

    permission_classes = [IsAuthenticated]

    def get_exercise(self) -> ReadingExercise:
        user = self.request.user
        queryset = ReadingExercise.objects.all()
        if not user.is_admin:
            queryset = queryset.filter(Q(is_published=True) | Q(owner_id=user.id))
        return get_object_or_404(queryset, pk=self.kwargs["exercise_id"])


class ExerciseSessionListCreateView(ExerciseVisibilityMixin, generics.GenericAPIView):
    queryset = ReadingSession.objects.none()
    serializer_class = ReadingSessionSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReadingSession.objects.none()
        return ReadingSession.objects.filter(
            user=self.request.user, exercise=self.get_exercise()
        ).order_by("-created_at")

    @extend_schema(
        tags=["reading"],
        summary="List the caller's sessions for a passage",
        description="Newest first. Never includes another learner's runs.",
        responses={200: ReadingSessionSerializer(many=True), **error_responses(401, 404)},
    )
    def get(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = ReadingSessionSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = ReadingSessionSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["reading"],
        summary="Start a reading session",
        description=(
            "Creates an in-progress run. The server sets `started_at` and "
            "snapshots `word_count` from the passage. Finish it with "
            "`PATCH /reading/sessions/{id}/`."
        ),
        request=ReadingSessionStartSerializer,
        responses={
            201: ReadingSessionSerializer,
            **error_responses(400, 401, 404),
        },
    )
    def post(self, request, *args, **kwargs):
        exercise = self.get_exercise()
        serializer = ReadingSessionStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = start_session(user=request.user, exercise=exercise, **serializer.validated_data)
        return Response(ReadingSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class SessionFinishView(generics.GenericAPIView):
    queryset = ReadingSession.objects.none()
    permission_classes = [IsAuthenticated]
    serializer_class = ReadingSessionFinishSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ReadingSession.objects.none()
        # Caller's sessions only: someone else's id is 404, not 403.
        return ReadingSession.objects.filter(user=self.request.user)

    @extend_schema(
        tags=["reading"],
        summary="Finish a reading session",
        description=(
            "Writes `finished_at` and a server-computed `actual_wpm` from the "
            "snapshotted `word_count` and `reading_ms`. `reading_ms` must be "
            "at least 1. 409 if the session is already finished."
        ),
        request=ReadingSessionFinishSerializer,
        responses={
            200: ReadingSessionSerializer,
            404: ErrorSerializer,
            **error_responses(400, 401, 409),
        },
    )
    def patch(self, request, *args, **kwargs):
        session = self.get_object()
        serializer = ReadingSessionFinishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = finish_session(session, **serializer.validated_data)
        return Response(ReadingSessionSerializer(session).data)
