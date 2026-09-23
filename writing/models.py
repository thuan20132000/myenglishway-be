"""Models for the writing domain.

A writing exercise is a prompt the learner writes against. The optional PDF is
a workbook or question sheet, not the exercise itself: title and description
are enough to start (IELTS Task 1/2). When a file is present it stays opaque
to the server - validated, page-counted, served back for the client to render.
Nothing here parses, extracts or scores.

The learner's record is a ``WritingNotebook``: one living document per learner
per workbook, upserted page by page. There is no attempt history and no score.
"""

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel
from common.text import tokenize


def writing_pdf_path(instance: "WritingExercise", filename: str) -> str:
    """Namespace uploads by owner and randomise the stored name.

    Its own prefix, separate from listening's ``audio/`` and ``pdf/``: a
    workbook is an order of magnitude larger than a question sheet, so storage
    rules (lifecycle, CDN cache) want to target it independently.
    """
    extension = Path(filename).suffix.lower()
    return f"writing-pdf/{instance.owner_id}/{uuid.uuid4().hex}{extension}"


class WritingExercise(TimeStampedModel):
    """A writing prompt. Ready the moment it has a title - no status machine.

    ``pdf_file`` is optional: a Task 2 prompt in ``title``/``description`` is
    enough to open a one-page notebook. When a PDF is attached, ``page_count``
    is read from the file.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="writing_exercises",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    source = models.CharField(
        max_length=255,
        blank=True,
        help_text="Where the workbook came from - book title, publisher, unit.",
    )
    pdf_file = models.FileField(
        upload_to=writing_pdf_path,
        null=True,
        blank=True,
        max_length=255,
        help_text="Optional workbook or question sheet shown beside the notebook.",
    )
    page_count = models.PositiveIntegerField(
        help_text=(
            "Read from the PDF on upload, or 1 when there is no file. "
            "Never supplied by the client."
        )
    )
    language = models.CharField(max_length=10, default="en")
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"], name="wex_pub_created_idx"),
            models.Index(fields=["owner", "-created_at"], name="wex_owner_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(page_count__gte=1),
                name="writing_exercise_page_count_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf_file)


class WritingNotebook(TimeStampedModel):
    """One learner's answers to one workbook.

    Unique per (user, exercise): writing is a document you keep editing, not a
    run you repeat, so saves upsert rather than append. The row is worth its
    own table for ``last_page`` and ``completed_at``, and so that a second
    notebook against the same workbook can exist later without touching pages.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="writing_notebooks",
    )
    exercise = models.ForeignKey(
        WritingExercise,
        on_delete=models.CASCADE,
        related_name="notebooks",
    )
    last_page = models.PositiveIntegerField(
        default=1, help_text="Where the learner left off, for resuming."
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the learner marks the workbook done. Never inferred.",
    )

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "exercise"],
                name="writing_notebook_unique_user_exercise",
            ),
            models.CheckConstraint(
                condition=Q(last_page__gte=1),
                name="writing_notebook_last_page_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.exercise_id}"

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None


class WritingPage(TimeStampedModel):
    """What the learner typed beside one page of the prompt (or PDF).

    Rows are created lazily: a page with no row has never been started, which
    is what "12 / 40 pages started" counts. An existing row with an empty
    ``body`` is a page the learner cleared.
    """

    notebook = models.ForeignKey(
        WritingNotebook,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    page_number = models.PositiveIntegerField()
    body = models.TextField(blank=True)
    word_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["notebook", "page_number"],
                name="writing_page_unique_notebook_number",
            ),
            models.CheckConstraint(
                condition=Q(page_number__gte=1),
                name="writing_page_number_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"page {self.page_number}"

    def save(self, *args, **kwargs):
        # Derived rather than stored by the caller, exactly as TranscriptSegment
        # does it, so the count cannot drift from the text.
        self.word_count = len(tokenize(self.body))
        super().save(*args, **kwargs)
