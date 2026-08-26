"""Serializers for the listening domain.

Read and write shapes are separate classes on purpose. Reads carry derived
fields (``audio_url``, ``segment_count``, nested owner) that are meaningless as
input; writes must never accept ``status``, ``is_published`` or ``owner``.
Merging them would mean a long ``read_only_fields`` list, ``required=False``
everywhere so PATCH works, and a schema that misrepresents what each endpoint
accepts.
"""

from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.serializers import UserSerializer
from common import errors

from .models import (
    ExerciseAnswer,
    ExerciseCollection,
    ListeningExercise,
    TranscriptSegment,
)
from .validators import validate_audio_file, validate_pdf_file


class InvalidSegmentRange(errors.DomainError):
    default_code = errors.INVALID_SEGMENT_RANGE
    default_detail = "The segment time range is invalid."


class ExerciseOwnerSerializer(serializers.Serializer):
    """Compact owner reference embedded in exercise payloads."""

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)


class CollectionParentSerializer(serializers.Serializer):
    """The grandparent rung of a breadcrumb - a book above a test."""

    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)


class CollectionRefSerializer(serializers.Serializer):
    """Where something is filed, as a breadcrumb rather than a nested tree."""

    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    parent = CollectionParentSerializer(read_only=True, allow_null=True)


def may_see_collection(collection, request) -> bool:
    """Published, or the caller's own, or the caller is an admin."""
    if collection is None:
        return False
    if collection.is_published:
        return True
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    return user.is_admin or collection.owner_id == user.id


def collection_ref(collection, request) -> dict | None:
    """A breadcrumb with every rung the caller may not see removed.

    Each rung is checked separately: a published test inside a withdrawn book
    stays visible, but naming the book in its breadcrumb would leak a title the
    caller cannot otherwise reach.
    """
    if not may_see_collection(collection, request):
        return None

    parent = collection.parent
    return {
        "id": collection.id,
        "title": collection.title,
        "parent": (
            {"id": parent.id, "title": parent.title}
            if may_see_collection(parent, request)
            else None
        ),
    }


class ExerciseCollectionRefMixin(serializers.Serializer):
    """Adds the visibility-filtered ``collection`` breadcrumb to an exercise.

    Null when the exercise is ungrouped, and also when the collection exists
    but the caller may not see it - a published exercise inside a draft book
    must not leak the book's title.
    """

    collection = serializers.SerializerMethodField()

    @extend_schema_field(CollectionRefSerializer(allow_null=True))
    def get_collection(self, instance):
        membership = getattr(instance, "membership", None)
        if membership is None:
            return None
        return collection_ref(membership.collection, self.context.get("request"))


class ExerciseMediaUrlMixin(serializers.Serializer):
    """Absolute URLs for the exercise's uploaded files.

    Both are plain media URLs carrying no authorisation: whoever can see the
    exercise can fetch them.
    """

    audio_url = serializers.SerializerMethodField()
    pdf_url = serializers.SerializerMethodField()

    def _absolute_media_url(self, file_field) -> str | None:
        if not file_field:
            return None
        url = file_field.url
        # S3 (and any CDN domain) already returns an absolute URL. Prefixing
        # it with the API origin would break the player.
        if url.startswith(("http://", "https://")):
            return url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_audio_url(self, obj) -> str | None:
        return self._absolute_media_url(obj.audio_file)

    def get_pdf_url(self, obj) -> str | None:
        return self._absolute_media_url(obj.pdf_file)


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


class ListeningExerciseListSerializer(ExerciseCollectionRefMixin, serializers.ModelSerializer):
    """List rows. Deliberately excludes description and segments."""

    owner = ExerciseOwnerSerializer(read_only=True)
    segment_count = serializers.IntegerField(read_only=True)
    #: Backed by the model property, so a row can show a "has handout" badge
    #: without the client fetching the detail payload.
    has_pdf = serializers.BooleanField(read_only=True)

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
            "has_pdf",
            "segment_count",
            "collection",
            "created_at",
        ]
        read_only_fields = fields


class ListeningExerciseDetailSerializer(
    ExerciseCollectionRefMixin, ExerciseMediaUrlMixin, serializers.ModelSerializer
):
    """Detail view. ``segments`` is present only for the owner or an admin."""

    owner = ExerciseOwnerSerializer(read_only=True)
    segment_count = serializers.IntegerField(read_only=True)
    answer_count = serializers.IntegerField(read_only=True)

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
            "pdf_url",
            "segment_count",
            "answer_count",
            "collection",
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

    pdf_file = serializers.FileField(
        required=False,
        allow_null=True,
        validators=[validate_pdf_file],
        help_text=(
            "Optional question sheet the learner reads while listening. "
            f"PDF only, up to {settings.MAX_PDF_FILE_SIZE_MB} MB."
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
        fields = ["title", "description", "language", "audio_file", "pdf_file", "duration"]

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

    pdf_file = serializers.FileField(
        required=False,
        allow_null=True,
        validators=[validate_pdf_file],
        help_text=(
            "Optional question sheet the learner reads while listening. "
            f"PDF only, up to {settings.MAX_PDF_FILE_SIZE_MB} MB."
        ),
    )

    remove_pdf = serializers.BooleanField(
        required=False,
        write_only=True,
        help_text=(
            "Set true to detach the current PDF. Ignored when pdf_file is also "
            "sent. A flag rather than pdf_file=null because these uploads are "
            "multipart, which has no null."
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
        fields = [
            "title",
            "description",
            "language",
            "audio_file",
            "pdf_file",
            "remove_pdf",
            "duration",
        ]

    validate_title = ListeningExerciseCreateSerializer.validate_title
    validate_language = ListeningExerciseCreateSerializer.validate_language


class ExerciseAnswerSerializer(serializers.ModelSerializer):
    """One entry of the answer key."""

    class Meta:
        model = ExerciseAnswer
        fields = ["number", "text"]
        read_only_fields = fields


class AnswerKeySerializer(serializers.Serializer):
    """The answer key, both as rows and as the text the creator pasted.

    ``answer_key`` is the write field and round-trips on read, so an edit form
    can load the key in the same shape it was authored rather than reassembling
    it from rows.
    """

    answer_key = serializers.CharField(
        allow_blank=True,
        trim_whitespace=False,
        style={"base_template": "textarea.html"},
        help_text=(
            "One answer per line, optionally numbered: '1. library'. Number "
            "every line or none of them. Send an empty string to clear the key."
        ),
    )
    answers = ExerciseAnswerSerializer(many=True, read_only=True)


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


# --------------------------------------------------------------------------
# Collections - read
# --------------------------------------------------------------------------


class CollectionListSerializer(serializers.ModelSerializer):
    """List rows. ``child_count``/``member_count`` come from queryset annotations."""

    owner = ExerciseOwnerSerializer(read_only=True)
    child_count = serializers.IntegerField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ExerciseCollection
        fields = [
            "id",
            "title",
            "owner",
            "is_published",
            "position",
            "child_count",
            "member_count",
            "created_at",
        ]
        read_only_fields = fields


class CollectionDetailSerializer(serializers.ModelSerializer):
    """Detail view: the folder plus one level of what is inside it.

    ``children`` and ``members`` are filtered to what the caller may see, so a
    student opening a published book gets only its published tests.
    """

    owner = ExerciseOwnerSerializer(read_only=True)
    parent = serializers.SerializerMethodField()
    child_count = serializers.IntegerField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    children = serializers.SerializerMethodField()
    members = serializers.SerializerMethodField()

    class Meta:
        model = ExerciseCollection
        fields = [
            "id",
            "title",
            "description",
            "owner",
            "parent",
            "is_published",
            "published_at",
            "position",
            "child_count",
            "member_count",
            "children",
            "members",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(CollectionRefSerializer(allow_null=True))
    def get_parent(self, instance):
        return collection_ref(instance.parent, self.context.get("request"))

    @extend_schema_field(CollectionListSerializer(many=True))
    def get_children(self, instance):
        return CollectionListSerializer(
            self._visible(instance.children.all()), many=True, context=self.context
        ).data

    @extend_schema_field(ListeningExerciseListSerializer(many=True))
    def get_members(self, instance):
        exercises = [
            membership.exercise for membership in instance.memberships.all()
        ]
        return ListeningExerciseListSerializer(
            self._visible(exercises), many=True, context=self.context
        ).data

    def _visible(self, items):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_admin:
            return list(items)
        owner_id = getattr(user, "id", None)
        return [
            item
            for item in items
            if item.is_published or (owner_id is not None and item.owner_id == owner_id)
        ]


class CollectionPublicationSerializer(serializers.ModelSerializer):
    """Response for the publish and unpublish transitions."""

    class Meta:
        model = ExerciseCollection
        fields = ["id", "is_published", "published_at"]
        read_only_fields = fields


# --------------------------------------------------------------------------
# Collections - write
# --------------------------------------------------------------------------


class CollectionCreateSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=ExerciseCollection.objects.all(),
        required=False,
        allow_null=True,
        help_text="Omit for a root collection. The parent must be a root you own.",
    )
    position = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="1-based position among siblings. Appended to the end if omitted.",
    )

    class Meta:
        model = ExerciseCollection
        fields = ["title", "description", "parent", "position"]

    def validate_title(self, value: str) -> str:
        title = value.strip()
        if not title:
            raise serializers.ValidationError("This field may not be blank.")
        return title


class CollectionUpdateSerializer(CollectionCreateSerializer):
    """Same fields as create; every one optional so PATCH works.

    ``is_published`` is absent on purpose - publication is a transition with
    its own endpoints, exactly as it is for exercises.
    """

    class Meta(CollectionCreateSerializer.Meta):
        extra_kwargs = {"title": {"required": False}}


class CollectionMembersSerializer(serializers.Serializer):
    """The complete, ordered member list. Position is the index plus one.

    Ordered ids rather than explicit positions: the client states the order it
    wants and gaps or duplicate positions become unrepresentable.
    """

    exercise_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        help_text="Every exercise in the collection, in display order.",
    )

    def validate_exercise_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Each exercise may appear only once.")
        return value
