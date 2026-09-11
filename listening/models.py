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


def exercise_pdf_path(instance: "ListeningExercise", filename: str) -> str:
    """Same namespacing rules as the audio, in a separate prefix.

    Keeping PDFs out of ``audio/`` means a storage rule (lifecycle, CDN cache,
    content-type) can target one kind of file without touching the other.
    """
    extension = Path(filename).suffix.lower()
    return f"pdf/{instance.owner_id}/{uuid.uuid4().hex}{extension}"


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
    pdf_file = models.FileField(
        upload_to=exercise_pdf_path,
        null=True,
        blank=True,
        max_length=255,
        help_text="Optional question sheet shown alongside the audio during practice.",
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
    def has_pdf(self) -> bool:
        return bool(self.pdf_file)

    @property
    def is_available_for_practice(self) -> bool:
        return self.is_published and self.status == ExerciseStatus.READY


class ListeningNotebook(TimeStampedModel):
    """One learner's notes on one listening exercise.

    Unique per (user, exercise): notes are a document you keep editing, not a
    run you repeat, so saves upsert rather than append. There are no pages —
    the whole recording is one ``body``. Nothing here is scored, and nothing
    writes a ``PracticeAttempt``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listening_notebooks",
    )
    exercise = models.ForeignKey(
        ListeningExercise,
        on_delete=models.CASCADE,
        related_name="notebooks",
    )
    body = models.TextField(blank=True)
    word_count = models.PositiveIntegerField(default=0, editable=False)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the learner marks the notes done. Never inferred.",
    )

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "exercise"],
                name="listening_notebook_unique_user_exercise",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} on {self.exercise_id}"

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def save(self, *args, **kwargs):
        self.word_count = len(tokenize(self.body))
        super().save(*args, **kwargs)


class ExerciseAnswer(TimeStampedModel):
    """One entry of the creator's answer key for the PDF question sheet.

    The questions themselves live in the PDF, which is an opaque file to us -
    this is only the key a learner checks their sheet against. Nothing scores
    against it: the practice UI shows the answer beside what the learner typed
    and leaves the judgement to them.
    """

    exercise = models.ForeignKey(
        ListeningExercise,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    number = models.PositiveIntegerField(
        help_text="The question number as printed on the sheet."
    )
    text = models.CharField(max_length=500)

    class Meta:
        ordering = ["number"]
        indexes = [models.Index(fields=["exercise", "number"], name="ans_ex_num_idx")]
        constraints = [
            # Not DEFERRED, unlike the segment sequence: an answer key is always
            # replaced as a whole, so no update ever has to pass through a state
            # where two rows share a number.
            models.UniqueConstraint(
                fields=["exercise", "number"],
                name="uniq_answer_number_per_exercise",
            ),
            models.CheckConstraint(
                condition=Q(number__gte=1),
                name="answer_number_min_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.exercise_id} Q{self.number}"


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


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------


class ExerciseCollection(TimeStampedModel):
    """A folder grouping exercises, at most two levels deep.

    Models the shape of published IELTS material - a book ("Cambridge IELTS
    18") holding tests ("Test 1"), each test holding its four parts - and the
    flat case ("Everyday conversations") where exercises hang directly off the
    root. Deliberately holds no audio, PDF, transcript or answer key:
    ``ListeningExercise`` stays the only practice unit, and a collection is
    navigation, not content.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="collections",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        help_text="Null for a root collection. A child may not have children of its own.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(
        default=1, help_text="1-based display order among siblings."
    )
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Sibling position uniqueness is enforced in ``collection_services``
        # rather than by a constraint. Roots form one sibling group keyed on a
        # NULL parent, which needs a *conditional* unique index - and Django
        # cannot mark a conditional UniqueConstraint DEFERRABLE, which
        # reordering requires. See uniq_member_position_per_collection below
        # for the case where deferral is available.
        # nulls_first because PostgreSQL sorts NULL last in ASC, which would
        # otherwise file every root behind the children of other collections.
        ordering = [F("parent_id").asc(nulls_first=True), "position", "id"]
        indexes = [
            models.Index(fields=["owner", "is_published"], name="coll_owner_pub_idx"),
            models.Index(fields=["parent", "position"], name="coll_parent_pos_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="collection_position_min_1",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_root(self) -> bool:
        return self.parent_id is None


class CollectionMembership(TimeStampedModel):
    """Places one exercise at one position inside one collection.

    A join model rather than a foreign key on ``ListeningExercise`` so the
    ordering lives with the grouping: the exercise itself does not care where
    it is filed. The one-to-one is what enforces "at most one collection".
    """

    collection = models.ForeignKey(
        ExerciseCollection,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    exercise = models.OneToOneField(
        ListeningExercise,
        on_delete=models.CASCADE,
        related_name="membership",
    )
    position = models.PositiveIntegerField(help_text="1-based order within the collection.")

    class Meta:
        ordering = ["position"]
        indexes = [models.Index(fields=["collection", "position"], name="member_coll_pos_idx")]
        constraints = [
            # DEFERRED for the same reason as the segment sequence: replacing
            # the member list passes through duplicate positions before it
            # settles, which PostgreSQL only checks at COMMIT.
            models.UniqueConstraint(
                fields=["collection", "position"],
                name="uniq_member_position_per_collection",
                deferrable=Deferrable.DEFERRED,
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1),
                name="member_position_min_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.collection_id} #{self.position}"
