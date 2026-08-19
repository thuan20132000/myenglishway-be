import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.constraints import Deferrable

from common.models import TimeStampedModel
from common.text import tokenize


class ExerciseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"  # created, no audio yet
    UPLOADED = "uploaded", "Uploaded"  # audio present, not yet transcribed
    PROCESSING = "processing", "Processing"  # transcription in flight
    READY = "ready", "Ready"  # has valid segments, publishable
    FAILED = "failed", "Failed"


def exercise_audio_path(instance: "ListeningExercise", filename: str) -> str:
    """Namespace uploads by owner and randomise the stored name.

    Keeps the original extension (validated elsewhere) but discards the client
    filename, so uploads cannot collide or smuggle a path.
    """
    extension = Path(filename).suffix.lower()
    return f"audio/{instance.owner_id}/{uuid.uuid4().hex}{extension}"


class ListeningExercise(TimeStampedModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="exercises",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    audio_file = models.FileField(
        upload_to=exercise_audio_path, null=True, blank=True, max_length=255
    )
    duration = models.FloatField(null=True, blank=True, help_text="Audio length in seconds.")
    language = models.CharField(max_length=10, default="en")
    status = models.CharField(
        max_length=20,
        choices=ExerciseStatus.choices,
        default=ExerciseStatus.DRAFT,
        db_index=True,
    )
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    # EXTENSION POINT: the future transcription task writes these. Adding the
    # columns now avoids a migration against a populated table later.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True)
    #: Which engine produced the current transcript ("openai-whisper", "manual",
    #: "stub"). Worth knowing when comparing quality or re-running after a
    #: provider change.
    transcription_provider = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"], name="ex_pub_created_idx"),
            models.Index(fields=["owner", "status"], name="ex_owner_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(duration__isnull=True) | Q(duration__gt=0),
                name="exercise_duration_positive",
            ),
            models.CheckConstraint(
                condition=Q(is_published=False) | Q(status=ExerciseStatus.READY),
                name="exercise_published_requires_ready",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_file)

    @property
    def is_available_for_practice(self) -> bool:
        return self.is_published and self.status == ExerciseStatus.READY


class TranscriptSegment(TimeStampedModel):
    exercise = models.ForeignKey(
        ListeningExercise,
        on_delete=models.CASCADE,
        related_name="segments",
    )
    sequence = models.PositiveIntegerField(help_text="1-based display order within the exercise.")
    start_time = models.FloatField(help_text="Seconds from the start of the audio.")
    end_time = models.FloatField(help_text="Seconds from the start of the audio.")
    text = models.TextField(help_text="The reference transcript for this segment.")
    word_count = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["sequence"]
        indexes = [models.Index(fields=["exercise", "sequence"], name="seg_ex_seq_idx")]
        constraints = [
            # DEFERRED so a reorder can shuffle sequences inside one
            # transaction without tripping the constraint mid-update.
            models.UniqueConstraint(
                fields=["exercise", "sequence"],
                name="uniq_segment_sequence_per_exercise",
                deferrable=Deferrable.DEFERRED,
            ),
            models.CheckConstraint(
                condition=Q(start_time__gte=0),
                name="segment_start_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="segment_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1),
                name="segment_sequence_min_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.exercise_id} #{self.sequence}"

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def save(self, *args, **kwargs):
        # Derived so the practice API can tell a learner how many words to
        # expect without exposing the transcript itself.
        self.word_count = len(tokenize(self.text or ""))
        super().save(*args, **kwargs)
