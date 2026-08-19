"""Serializers for the listening domain.

Read and write shapes are separate classes on purpose. Reads carry derived
fields (``audio_url``, ``segment_count``, nested owner) that are meaningless as
input; writes must never accept ``status``, ``is_published`` or ``owner``.
Merging them would mean a long ``read_only_fields`` list, ``required=False``
everywhere so PATCH works, and a schema that misrepresents what each endpoint
accepts.
"""

from django.conf import settings
from rest_framework import serializers

from accounts.serializers import UserSerializer
from common import errors

from .models import ListeningExercise, TranscriptSegment
from .validators import validate_audio_file


class InvalidSegmentRange(errors.DomainError):
    default_code = errors.INVALID_SEGMENT_RANGE
    default_detail = "The segment time range is invalid."


class ExerciseOwnerSerializer(serializers.Serializer):
    """Compact owner reference embedded in exercise payloads."""

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class AudioUrlMixin(serializers.Serializer):
    audio_url = serializers.SerializerMethodField()

    def get_audio_url(self, obj) -> str | None:
        if not obj.audio_file:
            return None
        url = obj.audio_file.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


# --------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------


class TranscriptSegmentCreatorSerializer(serializers.ModelSerializer):
    """Full segment representation, including transcript text.

    Only ever served to the exercise owner or an admin. The practice API uses
    a different serializer that has no ``text`` field at all.
    """

    sequence = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Omit to append after the current highest sequence.",
    )

    class Meta:
        model = TranscriptSegment
        fields = [
            "id",
            "sequence",
            "start_time",
            "end_time",
            "text",
            "word_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "word_count", "created_at", "updated_at"]

    def validate_text(self, value: str) -> str:
        if not value or not value.strip():
            raise serializers.ValidationError("Transcript text cannot be blank.")
        return value.strip()

    def validate(self, attrs):
        """Range checks that need no database state.

        Raised as a DomainError rather than a field error so the client sees the
        documented INVALID_SEGMENT_RANGE code; the offending field is reported
        in ``extra`` so a form can still highlight it. State-dependent rules
        (duplicate sequence, bounds against audio duration) belong to the
        service layer, which owns the surrounding transaction.
        """
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))

        if start is not None and start < 0:
            raise InvalidSegmentRange(
                "start_time must be zero or greater.", extra={"field": "start_time"}
            )
        if start is not None and end is not None and end <= start:
            raise InvalidSegmentRange(
                "end_time must be greater than start_time.", extra={"field": "end_time"}
            )
        return attrs


# --------------------------------------------------------------------------
# Exercises - read
# --------------------------------------------------------------------------


class ListeningExerciseListSerializer(serializers.ModelSerializer):
    """List rows. Deliberately excludes description and segments."""

    owner = ExerciseOwnerSerializer(read_only=True)
    segment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ListeningExercise
        fields = [
            "id",
            "title",
            "owner",
            "status",
            "is_published",
            "duration",
            "language",
            "segment_count",
            "created_at",
        ]
        read_only_fields = fields


class ListeningExerciseDetailSerializer(AudioUrlMixin, serializers.ModelSerializer):
    """Detail view. ``segments`` is present only for the owner or an admin."""

    owner = ExerciseOwnerSerializer(read_only=True)
    segment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ListeningExercise
        fields = [
            "id",
            "title",
            "description",
            "owner",
            "status",
            "is_published",
            "published_at",
            "duration",
            "language",
            "audio_url",
            "segment_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self._may_see_transcript(instance):
            data["segments"] = TranscriptSegmentCreatorSerializer(
                instance.segments.all(), many=True, context=self.context
            ).data
            # Diagnostics for the creator: without these a failed transcription
            # shows as status "failed" with no reason anywhere in the API.
            # Never exposed to learners.
            data["processing_error"] = instance.processing_error
            data["processing_started_at"] = (
                instance.processing_started_at.isoformat()
                if instance.processing_started_at
                else None
            )
            data["transcription_provider"] = instance.transcription_provider
        return data

    def _may_see_transcript(self, instance) -> bool:
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return False
        return request.user.is_admin or instance.owner_id == request.user.id


# --------------------------------------------------------------------------
# Exercises - write
# --------------------------------------------------------------------------


class ListeningExerciseCreateSerializer(serializers.ModelSerializer):
    """Create input. ``owner``, ``status`` and ``is_published`` are derived."""

    audio_file = serializers.FileField(
        required=False,
        allow_null=True,
        validators=[validate_audio_file],
        help_text=(
            "Optional at creation. Allowed: "
            f"{', '.join(settings.ALLOWED_AUDIO_EXTENSIONS)}."
        ),
    )

    duration = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0.0001,
        help_text=(
            "Audio length in seconds. Creator-supplied for now; the future "
            "transcription pipeline will set it automatically. Required before "
            "an exercise can be published."
        ),
    )

    class Meta:
        model = ListeningExercise
        fields = ["title", "description", "language", "audio_file", "duration"]

    def validate_title(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value

    def validate_language(self, value: str) -> str:
        if value not in settings.SUPPORTED_LANGUAGES:
            raise serializers.ValidationError(
                f"Unsupported language '{value}'. "
                f"Supported: {', '.join(settings.SUPPORTED_LANGUAGES)}."
            )
        return value


class ListeningExerciseUpdateSerializer(serializers.ModelSerializer):
    """Update input. Replacing audio is a state transition handled by the service."""

    audio_file = serializers.FileField(
        required=False, allow_null=True, validators=[validate_audio_file]
    )

    duration = serializers.FloatField(
        required=False,
        allow_null=True,
        min_value=0.0001,
        help_text=(
            "Audio length in seconds. Creator-supplied for now; the future "
            "transcription pipeline will set it automatically. Required before "
            "an exercise can be published."
        ),
    )

    class Meta:
        model = ListeningExercise
        fields = ["title", "description", "language", "audio_file", "duration"]

    validate_title = ListeningExerciseCreateSerializer.validate_title
    validate_language = ListeningExerciseCreateSerializer.validate_language


class SegmentReorderItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sequence = serializers.IntegerField(min_value=1)


class SegmentReorderSerializer(serializers.Serializer):
    """Whole-list reassignment of segment sequences.

    Requires the complete set rather than a delta: a partial payload cannot be
    validated as collision-free without guessing what the client intended for
    the segments it left out.
    """

    ordering = SegmentReorderItemSerializer(many=True, allow_empty=False)

    def validate_ordering(self, value):
        ids = [item["id"] for item in value]
        sequences = [item["sequence"] for item in value]

        if len(set(ids)) != len(ids):
            raise serializers.ValidationError("Each segment may appear only once.")
        if len(set(sequences)) != len(sequences):
            raise serializers.ValidationError("Each sequence value must be unique.")

        exercise = self.context["exercise"]
        existing = set(exercise.segments.values_list("id", flat=True))
        if set(ids) != existing:
            missing = sorted(existing - set(ids))
            unknown = sorted(set(ids) - existing)
            raise serializers.ValidationError(
                "The ordering must list every segment of this exercise exactly once. "
                f"Missing: {missing or 'none'}. Not in this exercise: {unknown or 'none'}."
            )
        return value


class ExercisePublicationSerializer(serializers.ModelSerializer):
    """Response for the publish and unpublish transitions."""

    class Meta:
        model = ListeningExercise
        fields = ["id", "status", "is_published", "published_at"]
        read_only_fields = fields


class ExerciseStatusSerializer(serializers.ModelSerializer):
    """Cheap polling target while transcription runs.

    Deliberately smaller than the detail payload: a client waiting for `ready`
    may hit this every few seconds.
    """

    segment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ListeningExercise
        fields = [
            "id",
            "status",
            "is_published",
            "duration",
            "segment_count",
            "processing_started_at",
            "processing_error",
            "transcription_provider",
        ]
        read_only_fields = fields
