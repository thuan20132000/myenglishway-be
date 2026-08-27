"""The learner's side of writing practice.

Every endpoint here operates on the caller's own notebook and no one else's.
There is no id in the path for it: the workbook plus the authenticated user
identify exactly one notebook, and opening a workbook creates it. That is also
why nothing here is admin-visible - see ``permissions.IsNotebookOwner``.

``PUT`` on a page is the autosave endpoint. It upserts, so the client can
debounce and fire it repeatedly without accumulating rows.
"""

from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.openapi import error_responses
from common.serializers import ErrorSerializer

from .models import WritingExercise, WritingNotebook, WritingPage
from .serializers import (
    WritingNotebookListSerializer,
    WritingNotebookSerializer,
    WritingPageSerializer,
    WritingPageWriteSerializer,
)
from .services import (
    assert_page_in_range,
    clear_complete,
    get_or_create_notebook,
    mark_complete,
    upsert_page,
)


class NotebookMixin:
    """Resolve the workbook, then the caller's notebook against it.

    The workbook is scoped exactly as ``WritingExerciseViewSet.get_queryset``
    scopes it, so someone else's draft 404s here too rather than 403-ing and
    confirming it exists.
    """

    permission_classes = [IsAuthenticated]

    def get_exercise(self) -> WritingExercise:
        user = self.request.user
        queryset = WritingExercise.objects.all()
        if not user.is_admin:
            queryset = queryset.filter(Q(is_published=True) | Q(owner_id=user.id))
        return get_object_or_404(queryset, pk=self.kwargs["exercise_id"])

    def get_notebook(self) -> WritingNotebook:
        return get_or_create_notebook(user=self.request.user, exercise=self.get_exercise())

    def notebook_response(self, notebook, status_code=status.HTTP_200_OK):
        notebook = (
            WritingNotebook.objects.select_related("exercise")
            .prefetch_related(
                Prefetch("pages", queryset=WritingPage.objects.order_by("page_number"))
            )
            .get(pk=notebook.pk)
        )
        serializer = WritingNotebookSerializer(notebook, context={"request": self.request})
        return Response(serializer.data, status=status_code)


@extend_schema(
    tags=["writing"],
    summary="Open the caller's notebook for a workbook",
    description=(
        "Get-or-create: the first call starts the notebook. Returns every page "
        "the learner has saved, where to resume (`last_page`), and the workbook's "
        "`pdf_url`.\n\n"
        "Pages that have never been written to are absent rather than empty - "
        "`pages_started` counts the ones with text in them."
    ),
    responses={200: WritingNotebookSerializer, **error_responses(401, 404)},
)
class NotebookView(NotebookMixin, APIView):
    def get(self, request, exercise_id):
        return self.notebook_response(self.get_notebook())


@extend_schema(
    tags=["writing"],
    summary="Mark the workbook finished, or un-finish it",
    description=(
        "`POST` stamps `completed_at`, `DELETE` clears it. Both are idempotent, "
        "and re-completing keeps the original timestamp.\n\n"
        "Completion is the learner's own claim: nothing infers it from how many "
        "pages have text, because a workbook is not scored."
    ),
    request=None,
    responses={200: WritingNotebookSerializer, **error_responses(401, 404)},
)
class NotebookCompleteView(NotebookMixin, APIView):
    def post(self, request, exercise_id):
        return self.notebook_response(mark_complete(self.get_notebook()))

    def delete(self, request, exercise_id):
        return self.notebook_response(clear_complete(self.get_notebook()))


@extend_schema(
    tags=["writing"],
    summary="Read or save one page of the notebook",
    description=(
        "`PUT` is the autosave endpoint. It upserts by page number, so a client "
        "debouncing every ~500ms produces one row, not a history. Sending an "
        "empty `body` clears the page - that is a legitimate save, not a "
        "validation error.\n\n"
        "`GET` on a page never written to returns an empty body rather than 404. "
        "A page number outside the workbook returns 400 `PAGE_OUT_OF_RANGE`."
    ),
    request=WritingPageWriteSerializer,
    responses={200: WritingPageSerializer, **error_responses(400, 401, 404)},
    examples=[
        OpenApiExample(
            "Autosaved page",
            response_only=True,
            value={
                "page_number": 7,
                "body": "1. however\n2. nevertheless",
                "word_count": 4,
                "updated_at": "2026-08-26T10:04:12Z",
            },
        )
    ],
)
class NotebookPageView(NotebookMixin, APIView):
    def get(self, request, exercise_id, page_number):
        exercise = self.get_exercise()
        assert_page_in_range(exercise, page_number)
        notebook = get_or_create_notebook(user=request.user, exercise=exercise)
        page = notebook.pages.filter(page_number=page_number).first()
        if page is None:
            # Unsaved stand-in rather than a 404: "not started" is a normal
            # state, and the editor wants a shape to bind to either way.
            page = WritingPage(notebook=notebook, page_number=page_number, body="")
        return Response(WritingPageSerializer(page).data)

    def put(self, request, exercise_id, page_number):
        notebook = self.get_notebook()
        serializer = WritingPageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        page = upsert_page(
            notebook=notebook,
            page_number=page_number,
            body=serializer.validated_data["body"],
        )
        return Response(WritingPageSerializer(page).data)


@extend_schema(
    tags=["writing"],
    summary="List the caller's notebooks",
    description=(
        "The \"continue writing\" list, most recently touched first. Page "
        "bodies are omitted; open a notebook to read them."
    ),
    responses={200: WritingNotebookListSerializer(many=True), 401: ErrorSerializer},
)
class NotebookListView(generics.ListAPIView):
    serializer_class = WritingNotebookListSerializer
    permission_classes = [IsAuthenticated]
    queryset = WritingNotebook.objects.none()
    ordering = ["-updated_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WritingNotebook.objects.none()
        return (
            WritingNotebook.objects.filter(user=self.request.user)
            .select_related("exercise")
            .annotate(pages_started=Count("pages", filter=~Q(pages__body=""), distinct=True))
            .order_by("-updated_at")
        )
