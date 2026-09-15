"""Models for the reading domain.

A reading exercise is a pasted passage plus optional follow-up audio. Timing
and the paced-reading overlay live in the client; this app stores the content
and each run as an append-only ``ReadingSession``. Sessions snapshot
``word_count`` at start so a later body edit does not rewrite history.
"""

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel
from common.text import tokenize


def reading_audio_path(instance: "ReadingExercise", filename: str) -> str:
    """Namespace uploads by owner and randomise the stored name.

    Its own prefix, separate from listening's ``audio/``: a reading clip is
    optional accompaniment, not the exercise, so storage rules can target it
    independently.
    """
    extension = Path(filename).suffix.lower()
    return f"reading-audio/{instance.owner_id}/{uuid.uuid4().hex}{extension}"


class ReadingMode(models.TextChoices):
    PACED = "paced", "Paced"
    HIGHLIGHT = "highlight", "Highlight"
    NORMAL = "normal", "Normal"


class ReadingExercise(TimeStampedModel):
    """A passage. Ready the moment it has a body - no status machine."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reading_exercises",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    source = models.CharField(
        max_length=255,
        blank=True,
        help_text="Where the passage came from - book title, publisher, unit.",
    )
    body = models.TextField(help_text="The passage itself. Plain text; paragraphs preserved.")
    word_count = models.PositiveIntegerField(
        editable=False,
        help_text="Derived from the body via tokenize(). Never supplied by the client.",
    )
    suggested_wpm = models.PositiveIntegerField(default=250)
    audio_file = models.FileField(
        upload_to=reading_audio_path,
        null=True,
        blank=True,
        max_length=255,
        help_text="Optional follow-up audio. Not synced to paced reading.",
    )
    language = models.CharField(max_length=10, default="en")
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"], name="rex_pub_created_idx"),
            models.Index(fields=["owner", "-created_at"], name="rex_owner_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(word_count__gte=1),
                name="reading_exercise_word_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(suggested_wpm__gte=100) & Q(suggested_wpm__lte=500),
                name="reading_exercise_suggested_wpm_range",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_file)

    def save(self, *args, **kwargs):
        self.word_count = len(tokenize(self.body))
        super().save(*args, **kwargs)


class ReadingSession(TimeStampedModel):
    """One paced-reading run against one passage.

    Append-only: the same learner may repeat a passage at 200 then 250 WPM.
    ``actual_wpm`` is written on finish from the snapshot ``word_count`` and
    ``reading_ms``; the client never supplies it.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reading_sessions",
    )
    exercise = models.ForeignKey(
        ReadingExercise,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    mode = models.CharField(
        max_length=20, choices=ReadingMode.choices, default=ReadingMode.PACED
    )
    target_wpm = models.PositiveIntegerField()
    strict_mode = models.BooleanField(default=False)
    word_count = models.PositiveIntegerField(
        help_text="Snapshot of passage length at start. Survives later body edits."
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    paused_ms = models.PositiveIntegerField(default=0)
    reading_ms = models.PositiveIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    actual_wpm = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "exercise", "-created_at"], name="rsess_user_ex_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(target_wpm__gte=100) & Q(target_wpm__lte=500),
                name="reading_session_target_wpm_range",
            ),
            models.CheckConstraint(
                condition=Q(progress__gte=0) & Q(progress__lte=100),
                name="reading_session_progress_range",
            ),
            models.CheckConstraint(
                condition=Q(word_count__gte=1),
                name="reading_session_word_count_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(finished_at__isnull=True, actual_wpm__isnull=True)
                    | Q(finished_at__isnull=False, actual_wpm__isnull=False)
                ),
                name="reading_session_wpm_only_when_finished",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.exercise_id} @ {self.target_wpm} WPM"

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None
