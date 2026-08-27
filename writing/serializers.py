"""Serializers for the writing domain.

Read and write shapes are separate classes, as in ``listening``: reads carry
derived fields (``pdf_url``, ``pages_started``) that are meaningless as input,
and writes must never accept ``owner``, ``page_count``, ``is_published`` or
``published_at``.
"""

from django.conf import settings
from rest_framework import serializers

from common.serializers import OwnerRefSerializer, absolute_media_url

from .models import WritingExercise, WritingNotebook, WritingPage
from .validators import validate_writing_pdf

PDF_HELP_TEXT = (
    "The workbook itself. PDF only, up to "
    f"{settings.MAX_WRITING_PDF_FILE_SIZE_MB} MB. Its page count is read from "
    "the file; it is never taken from the request."
)


class WritingPdfUrlMixin(serializers.Serializer):
    """The workbook's URL - a plain media URL carrying no authorisation.

    The client renders it with pdf.js and must fetch it *without* an
    ``Authorization`` header, exactly like listening's audio.
    """

    pdf_url = serializers.SerializerMethodField()

    def get_pdf_url(self, obj) -> str | None:
        return absolute_media_url(obj.pdf_file, self.context.get("request"))


class WritingProgressMixin(serializers.Serializer):
    """The caller's own standing against this workbook.

    Backed by the annotations in ``writing.views.annotate_for_user``; absent
    for an anonymous caller on the public catalogue, hence the defaults.
    """

    has_notebook = serializers.SerializerMethodField()
    pages_started = serializers.SerializerMethodField()
    last_page = serializers.SerializerMethodField()

    def get_has_notebook(self, obj) -> bool:
        return bool(getattr(obj, "has_notebook", False))

    def get_pages_started(self, obj) -> int:
        return getattr(obj, "pages_started", None) or 0

    def get_last_page(self, obj) -> int | None:
        return getattr(obj, "last_page", None)


class WritingExerciseListSerializer(WritingProgressMixin, serializers.ModelSerializer):
    """List rows. No ``pdf_url``: media URLs live on the detail view."""

    owner = OwnerRefSerializer(read_only=True)

    class Meta:
        model = WritingExercise
        fields = [
            "id",
            "title",
            "source",
            "owner",
            "page_count",
            "language",
            "is_published",
            "has_notebook",
            "pages_started",
            "last_page",
            "created_at",
        ]
        read_only_fields = fields


class WritingExerciseDetailSerializer(
    WritingPdfUrlMixin, WritingProgressMixin, serializers.ModelSerializer
):
    owner = OwnerRefSerializer(read_only=True)

    class Meta:
        model = WritingExercise
        fields = [
            "id",
            "title",
            "description",
            "source",
            "owner",
            "pdf_url",
            "page_count",
            "language",
            "is_published",
            "published_at",
            "has_notebook",
            "pages_started",
            "last_page",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class WritingExerciseCreateSerializer(serializers.ModelSerializer):
    """Any authenticated user may upload a workbook; it starts private."""

    pdf_file = serializers.FileField(
        required=True, validators=[validate_writing_pdf], help_text=PDF_HELP_TEXT
    )

    class Meta:
        model = WritingExercise
        fields = ["title", "description", "source", "language", "pdf_file"]


class WritingExerciseUpdateSerializer(serializers.ModelSerializer):
    pdf_file = serializers.FileField(
        required=False, validators=[validate_writing_pdf], help_text=PDF_HELP_TEXT
    )

    class Meta:
        model = WritingExercise
        fields = ["title", "description", "source", "language", "pdf_file"]
        extra_kwargs = {"title": {"required": False}}


class WritingExercisePublicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = WritingExercise
        fields = ["id", "is_published", "published_at"]
        read_only_fields = fields


class WritingPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = WritingPage
        fields = ["page_number", "body", "word_count", "updated_at"]
        read_only_fields = fields


class WritingPageWriteSerializer(serializers.Serializer):
    """The autosave body. Blank is a real value - the learner cleared the page."""

    body = serializers.CharField(allow_blank=True, trim_whitespace=False)

    def validate_body(self, value: str) -> str:
        # Read at call time rather than as a `max_length` argument: the ceiling
        # is a deployment knob, and binding it at import would freeze whatever
        # the setting happened to be when the module first loaded.
        limit = settings.MAX_WRITING_PAGE_LENGTH
        if len(value) > limit:
            raise serializers.ValidationError(
                f"A page holds at most {limit} characters."
            )
        return value


class WritingNotebookSerializer(WritingPdfUrlMixin, serializers.ModelSerializer):
    """The whole notebook: every saved page, plus where to resume."""

    exercise_id = serializers.IntegerField(source="exercise.id", read_only=True)
    title = serializers.CharField(source="exercise.title", read_only=True)
    page_count = serializers.IntegerField(source="exercise.page_count", read_only=True)
    pages = WritingPageSerializer(many=True, read_only=True)
    pages_started = serializers.SerializerMethodField()

    class Meta:
        model = WritingNotebook
        fields = [
            "id",
            "exercise_id",
            "title",
            "pdf_url",
            "page_count",
            "pages_started",
            "last_page",
            "completed_at",
            "pages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_pdf_url(self, obj) -> str | None:
        return absolute_media_url(obj.exercise.pdf_file, self.context.get("request"))

    def get_pages_started(self, obj) -> int:
        # Counted off the prefetched rows rather than with a query, since the
        # notebook payload has already loaded every page.
        return sum(1 for page in obj.pages.all() if page.body)


class WritingNotebookListSerializer(serializers.ModelSerializer):
    """"Continue writing" rows. No page bodies - just where to pick up."""

    exercise_id = serializers.IntegerField(source="exercise.id", read_only=True)
    title = serializers.CharField(source="exercise.title", read_only=True)
    source = serializers.CharField(source="exercise.source", read_only=True)
    page_count = serializers.IntegerField(source="exercise.page_count", read_only=True)
    pages_started = serializers.IntegerField(read_only=True)

    class Meta:
        model = WritingNotebook
        fields = [
            "id",
            "exercise_id",
            "title",
            "source",
            "page_count",
            "pages_started",
            "last_page",
            "completed_at",
            "updated_at",
        ]
        read_only_fields = fields
