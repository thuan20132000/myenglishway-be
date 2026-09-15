"""Serializers for the reading domain.

Read and write shapes are separate classes, as in ``writing``: reads carry
derived fields (``audio_url``, ``session_count``) that are meaningless as input,
and writes must never accept ``owner``, ``word_count``, ``is_published`` or
``published_at``.
"""

from django.conf import settings
from rest_framework import serializers

from common.serializers import OwnerRefSerializer, absolute_media_url
from common.text import tokenize
from common.validators import validate_audio_file

from .models import ReadingExercise, ReadingMode, ReadingSession

AUDIO_HELP_TEXT = (
    "Optional follow-up audio, not synced to paced reading. Allowed: "
    f"{', '.join(settings.ALLOWED_AUDIO_EXTENSIONS)}."
)


class ReadingAudioUrlMixin(serializers.Serializer):
    """The passage's optional audio URL - a plain media URL, no authorisation.

    The client must fetch it *without* an ``Authorization`` header, exactly
    like listening's audio and writing's PDF.
    """

    audio_url = serializers.SerializerMethodField()

    def get_audio_url(self, obj) -> str | None:
        return absolute_media_url(obj.audio_file, self.context.get("request"))


class ReadingProgressMixin(serializers.Serializer):
    """The caller's own standing against this passage.

    Backed by the annotations in ``reading.views.annotate_for_user``; absent
    for an anonymous caller on the public catalogue, hence the defaults.
    """

    session_count = serializers.SerializerMethodField()
    last_actual_wpm = serializers.SerializerMethodField()
    last_target_wpm = serializers.SerializerMethodField()
    has_audio = serializers.SerializerMethodField()

    def get_session_count(self, obj) -> int:
        return getattr(obj, "session_count", None) or 0

    def get_last_actual_wpm(self, obj) -> float | None:
        return getattr(obj, "last_actual_wpm", None)

    def get_last_target_wpm(self, obj) -> int | None:
        return getattr(obj, "last_target_wpm", None)

    def get_has_audio(self, obj) -> bool:
        return bool(obj.audio_file)


class ReadingExerciseListSerializer(ReadingProgressMixin, serializers.ModelSerializer):
    """List rows. No ``body`` or ``audio_url``: those live on the detail view."""

    owner = OwnerRefSerializer(read_only=True)

    class Meta:
        model = ReadingExercise
        fields = [
            "id",
            "title",
            "source",
            "owner",
            "word_count",
            "suggested_wpm",
            "language",
            "is_published",
            "has_audio",
            "session_count",
            "last_actual_wpm",
            "last_target_wpm",
            "created_at",
        ]
        read_only_fields = fields


class ReadingExerciseDetailSerializer(
    ReadingAudioUrlMixin, ReadingProgressMixin, serializers.ModelSerializer
):
    owner = OwnerRefSerializer(read_only=True)

    class Meta:
        model = ReadingExercise
        fields = [
            "id",
            "title",
            "description",
            "source",
            "owner",
            "body",
            "word_count",
            "suggested_wpm",
            "audio_url",
            "language",
            "is_published",
            "published_at",
            "has_audio",
            "session_count",
            "last_actual_wpm",
            "last_target_wpm",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


def _validate_title(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise serializers.ValidationError("Title cannot be blank.")
    return value


def _validate_language(value: str) -> str:
    if value not in settings.SUPPORTED_LANGUAGES:
        raise serializers.ValidationError(
            f"Unsupported language '{value}'. "
            f"Supported: {', '.join(settings.SUPPORTED_LANGUAGES)}."
        )
    return value


def _validate_body(value: str) -> str:
    limit = settings.MAX_READING_BODY_LENGTH
    if len(value) > limit:
        raise serializers.ValidationError(f"A passage holds at most {limit} characters.")
    if not tokenize(value):
        raise serializers.ValidationError("The passage must contain at least one word.")
    return value


class ReadingExerciseCreateSerializer(serializers.ModelSerializer):
    """Any authenticated user may paste a passage; it starts private."""

    suggested_wpm = serializers.IntegerField(
        required=False,
        min_value=settings.MIN_READING_WPM,
        max_value=settings.MAX_READING_WPM,
    )

    class Meta:
        model = ReadingExercise
        fields = ["title", "description", "source", "language", "body", "suggested_wpm"]

    def validate_title(self, value: str) -> str:
        return _validate_title(value)

    def validate_language(self, value: str) -> str:
        return _validate_language(value)

    def validate_body(self, value: str) -> str:
        return _validate_body(value)


class ReadingExerciseUpdateSerializer(serializers.ModelSerializer):
    audio_file = serializers.FileField(
        required=False, validators=[validate_audio_file], help_text=AUDIO_HELP_TEXT
    )
    suggested_wpm = serializers.IntegerField(
        required=False,
        min_value=settings.MIN_READING_WPM,
        max_value=settings.MAX_READING_WPM,
    )

    class Meta:
        model = ReadingExercise
        fields = [
            "title",
            "description",
            "source",
            "language",
            "body",
            "suggested_wpm",
            "audio_file",
        ]
        extra_kwargs = {"title": {"required": False}, "body": {"required": False}}

    def validate_title(self, value: str) -> str:
        return _validate_title(value)

    def validate_language(self, value: str) -> str:
        return _validate_language(value)

    def validate_body(self, value: str) -> str:
        return _validate_body(value)


class ReadingExercisePublicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReadingExercise
        fields = ["id", "is_published", "published_at"]
        read_only_fields = fields


class ReadingSessionSerializer(serializers.ModelSerializer):
    exercise_id = serializers.IntegerField(source="exercise.id", read_only=True)

    class Meta:
        model = ReadingSession
        fields = [
            "id",
            "exercise_id",
            "mode",
            "target_wpm",
            "strict_mode",
            "word_count",
            "started_at",
            "finished_at",
            "paused_ms",
            "reading_ms",
            "progress",
            "actual_wpm",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ReadingSessionStartSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=ReadingMode.choices, default=ReadingMode.PACED)
    target_wpm = serializers.IntegerField(
        min_value=settings.MIN_READING_WPM, max_value=settings.MAX_READING_WPM
    )
    strict_mode = serializers.BooleanField(required=False, default=False)


class ReadingSessionFinishSerializer(serializers.Serializer):
    reading_ms = serializers.IntegerField(min_value=1)
    paused_ms = serializers.IntegerField(min_value=0, required=False, default=0)
    progress = serializers.IntegerField(min_value=0, max_value=100)
