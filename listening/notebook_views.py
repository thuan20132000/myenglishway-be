"""The learner's notes on a listening exercise.

Every endpoint here operates on the caller's own notebook and no one else's.
There is no id in the path for it: the exercise plus the authenticated user
identify exactly one notebook, and opening an exercise creates it.

``PUT`` is the autosave endpoint. It upserts the single ``body``, so the
client can debounce and fire it repeatedly without accumulating rows.
"""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .models import ListeningExercise, ListeningNotebook
from .notebook_services import (
    clear_complete,
    get_or_create_notebook,
    mark_complete,
    upsert_body,
)
from .serializers import (
    ListeningNotebookListSerializer,
    ListeningNotebookSerializer,
    ListeningNotebookWriteSerializer,
)


class NotebookMixin:
    """Resolve the exercise, then the caller's notebook against it.

    Visibility is published-or-owned (admin sees every exercise), so someone
    else's draft 404s rather than 409-ing. Notes do not require the exercise
    to be ``ready``.
    """

    permission_classes = [IsAuthenticated]

    def get_exercise(self) -> ListeningExercise:
        user = self.request.user
        queryset = ListeningExercise.objects.all()
        if not user.is_admin:
            queryset = queryset.filter(Q(is_published=True) | Q(owner_id=user.id))
        return get_object_or_404(queryset, pk=self.kwargs["exercise_id"])

    def get_notebook(self) -> ListeningNotebook:
        return get_or_create_notebook(user=self.request.user, exercise=self.get_exercise())

    def notebook_response(self, notebook, status_code=status.HTTP_200_OK):
        notebook = ListeningNotebook.objects.select_related("exercise").get(pk=notebook.pk)
        serializer = ListeningNotebookSerializer(
            notebook, context={"request": self.request}
        )
        return Response(serializer.data, status=status_code)


class ListeningNotebookView(NotebookMixin, APIView):
    @extend_schema(
        tags=["listening"],
        summary="Open the caller's notebook for an exercise",
        description=(
            "Get-or-create: the first call starts the notebook. Returns the "
            "note plus `audio_url` and `pdf_url` so the notes screen is one "
            "request.\n\n"
            "Do not prefetch this on hover — opening is what starts the notebook."
        ),
        responses={200: ListeningNotebookSerializer, **error_responses(401, 404)},
        examples=[
            OpenApiExample(
                "Empty notebook",
                response_only=True,
                value={
                    "id": 1,
                    "exercise_id": 125,
                    "title": "Accommodation Practice",
                    "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
                    "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
                    "body": "",
                    "word_count": 0,
                    "completed_at": None,
                    "created_at": "2026-09-11T10:00:00Z",
                    "updated_at": "2026-09-11T10:00:00Z",
                },
            )
        ],
    )
    def get(self, request, exercise_id):
        return self.notebook_response(self.get_notebook())

    @extend_schema(
        tags=["listening"],
        summary="Save the caller's notes",
        description=(
            "Autosave. Overwrites the single `body`, so a client debouncing "
            "every ~500ms produces one row, not a history. An empty `body` "
            "clears the note — that is a legitimate save, not a validation "
            "error."
        ),
        request=ListeningNotebookWriteSerializer,
        responses={
            200: ListeningNotebookSerializer,
            **error_responses(400, 401, 404),
        },
    )
    def put(self, request, exercise_id):
        notebook = self.get_notebook()
        serializer = ListeningNotebookWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notebook = upsert_body(notebook=notebook, body=serializer.validated_data["body"])
        return self.notebook_response(notebook)


class ListeningNotebookCompleteView(NotebookMixin, APIView):
    @extend_schema(
        tags=["listening"],
        summary="Mark the notes finished, or un-finish them",
        description=(
            "`POST` stamps `completed_at`, `DELETE` clears it. Both are "
            "idempotent, and re-completing keeps the original timestamp.\n\n"
            "Completion is the learner's own claim: nothing infers it from "
            "the text, because a notebook is not scored."
        ),
        request=None,
        responses={200: ListeningNotebookSerializer, **error_responses(401, 404)},
    )
    def post(self, request, exercise_id):
        return self.notebook_response(mark_complete(self.get_notebook()))

    @extend_schema(
        tags=["listening"],
        summary="Clear the finished mark on the notes",
        description="Idempotent: already-unfinished notes stay unfinished.",
        request=None,
        responses={200: ListeningNotebookSerializer, **error_responses(401, 404)},
    )
    def delete(self, request, exercise_id):
        return self.notebook_response(clear_complete(self.get_notebook()))


@extend_schema(
    tags=["listening"],
    summary="List the caller's listening notebooks",
    description=(
        "The \"continue notes\" list, most recently touched first. Bodies "
        "are omitted; open a notebook to read them."
    ),
    responses={200: ListeningNotebookListSerializer(many=True), 401: ErrorSerializer},
)
class ListeningNotebookListView(generics.ListAPIView):
    serializer_class = ListeningNotebookListSerializer
    permission_classes = [IsAuthenticated]
    queryset = ListeningNotebook.objects.none()
    ordering = ["-updated_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ListeningNotebook.objects.none()
        return (
            ListeningNotebook.objects.filter(user=self.request.user)
            .select_related("exercise")
            .order_by("-updated_at")
        )
