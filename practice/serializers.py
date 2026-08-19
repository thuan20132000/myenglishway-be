from django.conf import settings
from rest_framework import serializers

from listening.models import TranscriptSegment

from .models import PracticeAttempt


class PracticeSegmentSerializer(serializers.ModelSerializer):
    """A segment as a learner sees it: timings only.

    There is deliberately no ``text`` field. Hiding the transcript is a
    structural property of this class rather than a runtime check, so it cannot
    leak through a context mistake or a forgotten condition.
    """

    class Meta:
        model = TranscriptSegment
        fields = ["id", "sequence", "start_time", "end_time", "word_count"]
        read_only_fields = fields


class PracticeSubmissionSerializer(serializers.Serializer):
    """Input for a dictation submission."""

    answer = serializers.CharField(
        allow_blank=True,
        trim_whitespace=False,
        help_text=(
            "What the learner typed. An empty string is a valid answer and "
            "scores zero; null is rejected."
        ),
    )

    def validate_answer(self, value: str) -> str:
        # Read at validation time rather than bound as ``max_length`` at import,
        # so the limit stays a live setting instead of a value frozen when the
        # module was first loaded.
        limit = settings.PRACTICE_MAX_ANSWER_LENGTH
        if len(value) > limit:
            raise serializers.ValidationError(
                f"Answer may not exceed {limit} characters."
            )
        return value


class PracticeResultSerializer(serializers.ModelSerializer):
    """The scored outcome, including the transcript now that it is earned."""

    attempt_id = serializers.IntegerField(source="id", read_only=True)
    segment_id = serializers.IntegerField(read_only=True)
    correct_answer = serializers.CharField(source="expected_snapshot", read_only=True)

    class Meta:
        model = PracticeAttempt
        fields = [
            "attempt_id",
            "segment_id",
            "score",
            "user_answer",
            "correct_answer",
            "result",
            "created_at",
        ]
        read_only_fields = fields


class PracticeSegmentProgressSerializer(PracticeSegmentSerializer):
    """A practice segment plus this learner's progress on it.

    Inherits the text-free field list rather than redeclaring it, so the
    transcript cannot reappear here by omission.
    """

    attempted = serializers.BooleanField(read_only=True)
    best_score = serializers.FloatField(read_only=True, allow_null=True)

    class Meta(PracticeSegmentSerializer.Meta):
        fields = PracticeSegmentSerializer.Meta.fields + ["attempted", "best_score"]
        read_only_fields = fields


class PracticeStartSerializer(serializers.Serializer):
    """Everything a client needs to open (or resume) an exercise."""

    exercise_id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    audio_url = serializers.CharField(read_only=True, allow_null=True)
    total_segments = serializers.IntegerField(read_only=True)
    attempted_segments = serializers.IntegerField(read_only=True)
    current_segment = PracticeSegmentSerializer(read_only=True, allow_null=True)


class RevealSerializer(serializers.Serializer):
    """The transcript, released because the learner asked to give up."""

    attempt_id = serializers.IntegerField(read_only=True)
    segment_id = serializers.IntegerField(read_only=True)
    text = serializers.CharField(read_only=True)
    revealed = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class ProgressSerializer(serializers.Serializer):
    """A learner's progress on one exercise.

    ``attempted`` counts distinct segments with at least one submission.
    ``completed`` counts distinct segments whose best submission reaches the
    configured threshold. ``average_score`` is the mean of each attempted
    segment's best score, so retrying can only raise it. Reveals are counted
    separately and never make a segment attempted or completed.
    """

    exercise_id = serializers.IntegerField(read_only=True)
    total_segments = serializers.IntegerField(read_only=True)
    attempted_segments = serializers.IntegerField(read_only=True)
    completed_segments = serializers.IntegerField(read_only=True)
    revealed_segments = serializers.IntegerField(read_only=True)
    progress_percentage = serializers.FloatField(read_only=True)
    average_score = serializers.FloatField(read_only=True, allow_null=True)
    last_segment_sequence = serializers.IntegerField(read_only=True, allow_null=True)
    last_practiced_at = serializers.DateTimeField(read_only=True, allow_null=True)


class HistoryExerciseSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True, allow_null=True)
    total_segments = serializers.IntegerField(read_only=True)


class HistoryItemSerializer(serializers.Serializer):
    """One exercise's worth of a learner's practice history."""

    exercise = HistoryExerciseSerializer(read_only=True)
    attempted_segments = serializers.IntegerField(read_only=True)
    completed_segments = serializers.IntegerField(read_only=True)
    total_attempts = serializers.IntegerField(read_only=True)
    average_score = serializers.FloatField(read_only=True, allow_null=True)
    last_practiced_at = serializers.DateTimeField(read_only=True)


class HistoryQuerySerializer(serializers.Serializer):
    """Validates history query parameters so bad dates fail cleanly."""

    exercise = serializers.IntegerField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        date_from, date_to = attrs.get("date_from"), attrs.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError(
                {"date_from": "date_from must not be later than date_to."}
            )
        return attrs


class AttemptSegmentSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    sequence = serializers.IntegerField(read_only=True)


class AttemptExerciseSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)


class PracticeAttemptSerializer(serializers.ModelSerializer):
    """One recorded attempt, as its own author sees it."""

    exercise = AttemptExerciseSerializer(read_only=True)
    segment = AttemptSegmentSerializer(read_only=True)

    class Meta:
        model = PracticeAttempt
        fields = [
            "id",
            "kind",
            "exercise",
            "segment",
            "score",
            "user_answer",
            "result",
            "created_at",
        ]
        read_only_fields = fields
