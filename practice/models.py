from django.conf import settings
from django.db import models
from django.db.models import Q

from listening.models import ListeningExercise, TranscriptSegment


class AttemptKind(models.TextChoices):
    SUBMISSION = "submission", "Submission"  # a scored dictation answer
    REVEAL = "reveal", "Reveal"  # learner gave up and read the transcript


class PracticeAttempt(models.Model):
    """One interaction with a segment: a scored answer, or a transcript reveal.

    Both kinds live in one table because they share a shape (user, segment,
    timestamp) and every history and progress query reads them together. A
    separate reveal model would force a UNION into each of those queries for no
    gain; the ``kind`` discriminator is enough to keep reveals out of score
    aggregates.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts"
    )
    segment = models.ForeignKey(
        TranscriptSegment, on_delete=models.CASCADE, related_name="attempts"
    )
    #: Denormalised from ``segment.exercise``. Immutable - a segment never moves
    #: between exercises - so there is nothing to keep in sync, and it saves a
    #: join through TranscriptSegment in every progress and history aggregate.
    exercise = models.ForeignKey(
        ListeningExercise, on_delete=models.CASCADE, related_name="attempts"
    )

    kind = models.CharField(
        max_length=20, choices=AttemptKind.choices, default=AttemptKind.SUBMISSION
    )

    user_answer = models.TextField(blank=True)
    normalized_answer = models.TextField(blank=True)
    #: The transcript as it read when this attempt was scored. Kept so a later
    #: correction by the creator does not make an old attempt look wrong.
    expected_snapshot = models.TextField(blank=True)

    score = models.FloatField(null=True, blank=True, help_text="0-100. Null for reveals.")

    # Small indexed columns for the aggregates progress and analytics run.
    correct_count = models.PositiveSmallIntegerField(default=0)
    missing_count = models.PositiveSmallIntegerField(default=0)
    extra_count = models.PositiveSmallIntegerField(default=0)
    incorrect_count = models.PositiveSmallIntegerField(default=0)

    #: Display-only word-level diff. JSON so the shape can evolve with the
    #: scorer without a migration; the numbers worth aggregating are columns.
    result = models.JSONField(default=dict, blank=True)

    scoring_version = models.CharField(max_length=20, default="v1")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "exercise", "-created_at"], name="att_user_ex_idx"),
            models.Index(fields=["user", "segment", "-created_at"], name="att_user_seg_idx"),
            models.Index(fields=["user", "kind"], name="att_user_kind_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(score__isnull=True) | (Q(score__gte=0) & Q(score__lte=100)),
                name="attempt_score_range",
            ),
            models.CheckConstraint(
                condition=~Q(kind="reveal") | Q(score__isnull=True),
                name="reveal_has_no_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} / segment {self.segment_id} / {self.kind}"

    @property
    def is_submission(self) -> bool:
        return self.kind == AttemptKind.SUBMISSION
