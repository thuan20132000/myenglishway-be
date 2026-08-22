"""State transitions and multi-field rules for the listening domain.

Views parse and serialize; anything that decides what ``status`` should become,
or that touches more than one model, lives here. Publishing and transcript
ingestion are added in later steps and will slot in alongside these.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from common import errors

from common.text import tokenize

from .answer_key import parse_answer_key
from .models import (
    ExerciseAnswer,
    ExerciseStatus,
    ListeningExercise,
    TranscriptSegment,
)


class ExerciseLocked(errors.ConflictError):
    """Raised when an exercise cannot be modified in its current state."""


class ExerciseNotReady(errors.ConflictError):
    default_code = errors.EXERCISE_NOT_READY
    default_detail = "This exercise does not meet the requirements for publishing."


class ExerciseAlreadyPublished(errors.ConflictError):
    default_code = errors.EXERCISE_ALREADY_PUBLISHED
    default_detail = "This exercise is already published."


class ExerciseProcessing(errors.ConflictError):
    default_code = errors.EXERCISE_PROCESSING
    default_detail = "This exercise is being transcribed and cannot be edited right now."


class TranscriptIngestError(errors.DomainError):
    default_code = errors.INVALID_SEGMENT_RANGE
    default_detail = "The transcript payload is not valid."


class DuplicateSegmentSequence(errors.DomainError):
    default_code = errors.DUPLICATE_SEGMENT_SEQUENCE
    default_detail = "Another segment in this exercise already uses that sequence."


class SegmentOutsideAudio(errors.DomainError):
    default_code = errors.SEGMENT_OUTSIDE_AUDIO
    default_detail = "The segment extends beyond the end of the audio."


def schedule_transcription(exercise: ListeningExercise) -> bool:
    """Queue automatic transcription for an exercise's audio.

    Enqueued with ``transaction.on_commit`` rather than directly: callers run
    inside atomic blocks, and a worker that picks the job up before the row is
    committed fails with a bewildering "exercise does not exist". The audio
    name is captured now so a slow run can detect that the file was replaced
    while it was queued.

    Returns whether a task was queued, so callers can tell the difference
    between "started" and "auto-start is off".
    """
    print(f"Scheduling transcription for exercise {exercise.pk} with audio name {exercise.audio_file.name}")
    if not settings.TRANSCRIPTION_AUTO_START or not exercise.has_audio:
        print(f"Transcription not scheduled for exercise {exercise.pk} because auto-start is off or no audio")
        return False

    # Imported here: listening.tasks imports this module, and a module-level
    # import would be circular.
    from .tasks import transcribe_exercise

    exercise_id = exercise.pk
    audio_name = exercise.audio_file.name
    print(f"Queuing transcription for exercise {exercise_id} with audio name {audio_name}")
    transaction.on_commit(lambda: transcribe_exercise.delay(exercise_id, audio_name))
    return True


def create_exercise(*, owner, **fields) -> ListeningExercise:
    """Create an exercise, deriving its initial status from the audio."""
    status = ExerciseStatus.UPLOADED if fields.get("audio_file") else ExerciseStatus.DRAFT
    exercise = ListeningExercise.objects.create(owner=owner, status=status, **fields)
    schedule_transcription(exercise)
    return exercise


@transaction.atomic
def update_exercise(exercise: ListeningExercise, **fields) -> ListeningExercise:
    """Apply metadata edits, treating an audio swap as a state transition."""
    new_audio = fields.pop("audio_file", None)
    new_pdf = fields.pop("pdf_file", None)
    remove_pdf = fields.pop("remove_pdf", False)
    explicit_duration = fields.pop("duration", "unset")

    for field, value in fields.items():
        setattr(exercise, field, value)

    if new_audio is not None:
        _replace_audio(exercise, new_audio)

    # A new file wins over the removal flag: sending both is a replace.
    if new_pdf is not None:
        _replace_pdf(exercise, new_pdf)
    elif remove_pdf:
        _remove_pdf(exercise)

    # Applied after the audio swap, which clears the old duration: a client
    # replacing the file and stating the new length in one request means it.
    if explicit_duration != "unset":
        exercise.duration = explicit_duration

    exercise.save()

    # Scheduled after save so the queued task reads the new file name.
    if new_audio is not None:
        schedule_transcription(exercise)

    return exercise


def _replace_audio(exercise: ListeningExercise, audio_file) -> None:
    """Swap the audio and reset everything derived from the old file.

    Rejected while published: the segments still describe the previous audio,
    so learners would practise against timings that no longer line up.
    """
    if exercise.is_published:
        raise ExerciseLocked(
            "Unpublish the exercise before replacing its audio.",
            code=errors.EXERCISE_NOT_READY,
            extra={"reasons": ["exercise_is_published"]},
        )

    exercise.audio_file = audio_file
    exercise.duration = None
    exercise.status = ExerciseStatus.UPLOADED
    exercise.processing_error = ""
    exercise.processing_started_at = None


def _replace_pdf(exercise: ListeningExercise, pdf_file) -> None:
    """Swap the handout, leaving every other field alone.

    Deliberately unlike :func:`_replace_audio`: nothing is derived from the PDF,
    so there is no duration to clear, no status to reset and nothing to
    transcribe. It is also allowed while published - the audio and its segment
    timings are untouched, so a creator fixing a typo in the question sheet has
    no reason to take the exercise offline first.
    """
    _discard_pdf_file(exercise)
    exercise.pdf_file = pdf_file


def _remove_pdf(exercise: ListeningExercise) -> None:
    """Detach the handout, leaving the exercise otherwise practisable."""
    _discard_pdf_file(exercise)
    exercise.pdf_file = None


def _discard_pdf_file(exercise: ListeningExercise) -> None:
    """Delete the stored file so a swap does not orphan it.

    ``save=False`` because the caller saves the row once, after all the field
    changes have been applied.
    """
    if exercise.pdf_file:
        exercise.pdf_file.delete(save=False)


# --------------------------------------------------------------------------
# Answer key
# --------------------------------------------------------------------------


@transaction.atomic
def replace_answer_key(exercise: ListeningExercise, raw: str) -> list[ExerciseAnswer]:
    """Replace an exercise's whole answer key with a pasted list.

    Wholesale replacement rather than a row-by-row diff: the input *is* the
    entire key, and the rows it describes have no identity to match existing
    ones against. Empty input clears the key.

    Allowed while published, like the PDF swap and unlike an audio swap: no
    segment timing depends on the key, so a creator correcting a typo has no
    reason to take the exercise offline.
    """
    pairs = parse_answer_key(raw)

    exercise.answers.all().delete()
    if not pairs:
        return []

    return ExerciseAnswer.objects.bulk_create(
        ExerciseAnswer(exercise=exercise, number=number, text=text)
        for number, text in pairs
    )


def recompute_ready_state(exercise: ListeningExercise) -> ListeningExercise:
    """Move an exercise between UPLOADED and READY based on its segments.

    Called after any segment mutation. Never touches an exercise mid-transcription
    or one that has failed - those states are owned by the ingestion pipeline.
    """
    if exercise.status in (ExerciseStatus.PROCESSING, ExerciseStatus.FAILED):
        return exercise

    has_segments = exercise.segments.exists()

    if has_segments and exercise.has_audio:
        target = ExerciseStatus.READY
    elif exercise.has_audio:
        target = ExerciseStatus.UPLOADED
    else:
        target = ExerciseStatus.DRAFT

    if target == exercise.status:
        return exercise

    exercise.status = target
    # A published exercise that loses its last segment cannot stay published:
    # the DB constraint requires published => ready.
    if target != ExerciseStatus.READY and exercise.is_published:
        exercise.is_published = False
        exercise.published_at = None

    exercise.save(update_fields=["status", "is_published", "published_at", "updated_at"])
    return exercise


# --------------------------------------------------------------------------
# Segments
# --------------------------------------------------------------------------


def _assert_segments_editable(exercise: ListeningExercise) -> None:
    """Block edits while the transcription pipeline owns the segment set."""
    if exercise.status == ExerciseStatus.PROCESSING:
        raise ExerciseProcessing()


def _next_sequence(exercise: ListeningExercise) -> int:
    highest = exercise.segments.aggregate(highest=Max("sequence"))["highest"]
    return (highest or 0) + 1


def _assert_sequence_available(
    exercise: ListeningExercise, sequence: int, *, exclude_pk: int | None = None
) -> None:
    """Reject a colliding sequence before the database would.

    The unique constraint is DEFERRABLE INITIALLY DEFERRED so reordering works,
    which means a duplicate insert does not fail until COMMIT - by which point
    the response is already being built and the IntegrityError would surface as
    a 500. Checking here is what turns it into a clean 400.
    """
    clashes = exercise.segments.filter(sequence=sequence)
    if exclude_pk is not None:
        clashes = clashes.exclude(pk=exclude_pk)
    if clashes.exists():
        raise DuplicateSegmentSequence(extra={"sequence": sequence})


def _assert_within_audio(exercise: ListeningExercise, end_time: float) -> None:
    """Keep timestamps inside the audio, when its length is known."""
    if exercise.duration is None:
        return
    limit = exercise.duration + settings.SEGMENT_DURATION_TOLERANCE
    if end_time > limit:
        raise SegmentOutsideAudio(
            f"end_time {end_time} exceeds the audio duration of {exercise.duration} seconds.",
            extra={"duration": exercise.duration, "end_time": end_time},
        )


@transaction.atomic
def create_segment(exercise: ListeningExercise, **fields) -> TranscriptSegment:
    _assert_segments_editable(exercise)

    sequence = fields.pop("sequence", None)
    if sequence is None:
        sequence = _next_sequence(exercise)
    else:
        _assert_sequence_available(exercise, sequence)

    _assert_within_audio(exercise, fields["end_time"])

    segment = TranscriptSegment.objects.create(exercise=exercise, sequence=sequence, **fields)
    recompute_ready_state(exercise)
    return segment


@transaction.atomic
def update_segment(segment: TranscriptSegment, **fields) -> TranscriptSegment:
    exercise = segment.exercise
    _assert_segments_editable(exercise)

    if "sequence" in fields and fields["sequence"] != segment.sequence:
        _assert_sequence_available(exercise, fields["sequence"], exclude_pk=segment.pk)

    for field, value in fields.items():
        setattr(segment, field, value)

    _assert_within_audio(exercise, segment.end_time)

    segment.save()
    return segment


@transaction.atomic
def delete_segment(segment: TranscriptSegment) -> None:
    exercise = segment.exercise
    _assert_segments_editable(exercise)
    segment.delete()
    recompute_ready_state(exercise)


@transaction.atomic
def reorder_segments(exercise: ListeningExercise, ordering: list[dict]) -> None:
    """Reassign every segment's sequence in one transaction.

    Relies on the deferred unique constraint: intermediate states may contain
    duplicate sequences, which PostgreSQL only checks at COMMIT.
    """
    _assert_segments_editable(exercise)

    sequence_by_id = {item["id"]: item["sequence"] for item in ordering}
    segments = list(exercise.segments.filter(pk__in=sequence_by_id))

    for segment in segments:
        segment.sequence = sequence_by_id[segment.pk]

    TranscriptSegment.objects.bulk_update(segments, ["sequence"])


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------


def check_publish_preconditions(exercise: ListeningExercise) -> list[str]:
    """Return the reasons an exercise cannot be published, empty if it can.

    Returned as a list rather than raising on the first failure so a creator
    sees everything left to fix in one response instead of discovering the
    problems one request at a time.
    """
    reasons: list[str] = []

    if not exercise.has_audio:
        reasons.append("missing_audio")
    if exercise.duration is None:
        reasons.append("missing_duration")

    segment_bounds = exercise.segments.aggregate(latest=Max("end_time"))["latest"]
    if segment_bounds is None:
        reasons.append("no_segments")
    elif exercise.duration is not None:
        limit = exercise.duration + settings.SEGMENT_DURATION_TOLERANCE
        if segment_bounds > limit:
            reasons.append("segments_exceed_duration")

    if exercise.status != ExerciseStatus.READY:
        # PROCESSING and FAILED are owned by the ingestion pipeline; DRAFT and
        # UPLOADED are already explained by one of the reasons above.
        reasons.append(f"status_is_{exercise.status}")

    return reasons


@transaction.atomic
def publish_exercise(exercise: ListeningExercise) -> ListeningExercise:
    if exercise.is_published:
        raise ExerciseAlreadyPublished()

    reasons = check_publish_preconditions(exercise)
    if reasons:
        raise ExerciseNotReady(extra={"reasons": reasons})

    exercise.is_published = True
    exercise.published_at = timezone.now()
    exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


@transaction.atomic
def unpublish_exercise(exercise: ListeningExercise) -> ListeningExercise:
    """Withdraw an exercise from learners. Idempotent.

    Unlike publish, repeating this is not an error: the caller's intent is
    "make sure this is not visible", and it already is not. Existing attempts
    are retained either way.
    """
    if not exercise.is_published:
        return exercise

    exercise.is_published = False
    exercise.published_at = None
    exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


# --------------------------------------------------------------------------
# Transcription ingest
#
# This is the seam the future transcription pipeline plugs into. A Celery task
# will call mark_processing(), run a provider, then call ingest_transcript()
# with the provider's output - or mark_failed() if it raised. Nothing in the
# views or serializers needs to change when that happens, and the payload
# contract below is already exercised by the load_transcript command and its
# tests, so the integration point is proven before the pipeline exists.
# --------------------------------------------------------------------------

#: Keys accepted on each segment of an ingest payload. ``words`` is accepted and
#: ignored: providers emit word-level timings today, and dropping them here
#: rather than rejecting them means adding a TranscriptWord model later is a
#: change to this function alone.
_SEGMENT_KEYS = {"sequence", "start_time", "end_time", "text", "words"}


def mark_processing(exercise: ListeningExercise) -> ListeningExercise:
    """Flag an exercise as being transcribed. Blocks segment edits meanwhile.

    Refuses a published exercise: the database requires published => ready, so
    moving one to PROCESSING would raise an IntegrityError, and learners would
    lose access to an exercise mid-practice.
    """
    if exercise.is_published:
        raise ExerciseLocked(
            "Unpublish the exercise before transcribing it again.",
            extra={"reasons": ["exercise_is_published"]},
        )
    if not exercise.has_audio:
        raise ExerciseNotReady(
            "Cannot transcribe an exercise with no audio.",
            extra={"reasons": ["missing_audio"]},
        )

    exercise.status = ExerciseStatus.PROCESSING
    exercise.processing_started_at = timezone.now()
    exercise.processing_error = ""
    exercise.save(
        update_fields=["status", "processing_started_at", "processing_error", "updated_at"]
    )
    return exercise


def mark_failed(exercise: ListeningExercise, error: str) -> ListeningExercise:
    """Record that transcription failed, keeping the reason for the creator."""
    exercise.status = ExerciseStatus.FAILED
    exercise.processing_error = str(error)[:2000]
    exercise.save(update_fields=["status", "processing_error", "updated_at"])
    return exercise


@transaction.atomic
def ingest_transcript(
    exercise: ListeningExercise,
    *,
    segments: list[dict],
    duration: float | None = None,
    provider: str = "manual",
) -> ListeningExercise:
    """Replace an exercise's transcript with a provider's output.

    Wholesale replacement rather than a merge: a transcription run produces a
    complete transcript, and reconciling it segment-by-segment with hand edits
    would need a merge policy nobody has specified. Creators who have edited by
    hand should not re-run transcription.

    Raises TranscriptIngestError on an invalid payload, leaving the exercise
    untouched - the transaction means a partial transcript is never stored.
    """
    if exercise.is_published:
        raise ExerciseLocked(
            "Unpublish the exercise before replacing its transcript.",
            extra={"reasons": ["exercise_is_published"]},
        )

    cleaned = _validate_ingest_payload(segments, duration)

    exercise.segments.all().delete()
    TranscriptSegment.objects.bulk_create(
        [
            TranscriptSegment(
                exercise=exercise,
                sequence=row["sequence"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                text=row["text"],
                # bulk_create bypasses Model.save(), so the derived field has
                # to be computed here or every ingested segment would store 0.
                word_count=len(tokenize(row["text"])),
            )
            for row in cleaned
        ]
    )

    if duration is not None:
        exercise.duration = duration
    exercise.status = ExerciseStatus.READY
    exercise.processing_error = ""
    exercise.transcription_provider = provider
    exercise.save(
        update_fields=[
            "duration",
            "status",
            "processing_error",
            "transcription_provider",
            "updated_at",
        ]
    )

    return exercise


def _validate_ingest_payload(segments: list[dict], duration: float | None) -> list[dict]:
    """Validate and normalise an ingest payload, assigning sequences if absent."""
    if not segments:
        raise TranscriptIngestError(
            "A transcript must contain at least one segment.",
            code=errors.TRANSCRIPT_NOT_AVAILABLE,
        )
    if duration is not None and duration <= 0:
        raise TranscriptIngestError("duration must be greater than zero.")

    cleaned = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            raise TranscriptIngestError(f"Segment {index} is not an object.")

        unknown = set(raw) - _SEGMENT_KEYS
        if unknown:
            raise TranscriptIngestError(
                f"Segment {index} has unknown keys: {', '.join(sorted(unknown))}."
            )

        cleaned.append(
            {
                "sequence": raw.get("sequence"),
                "start_time": _require_number(raw, "start_time", index),
                "end_time": _require_number(raw, "end_time", index),
                "text": _require_text(raw, index),
            }
        )

    for index, row in enumerate(cleaned):
        if row["start_time"] < 0:
            raise TranscriptIngestError(f"Segment {index}: start_time must not be negative.")
        if row["end_time"] <= row["start_time"]:
            raise TranscriptIngestError(
                f"Segment {index}: end_time must be greater than start_time."
            )
        if duration is not None:
            limit = duration + settings.SEGMENT_DURATION_TOLERANCE
            if row["end_time"] > limit:
                raise TranscriptIngestError(
                    f"Segment {index}: end_time {row['end_time']} exceeds the audio "
                    f"duration of {duration} seconds.",
                    code=errors.SEGMENT_OUTSIDE_AUDIO,
                )

    return _assign_sequences(cleaned)


def _assign_sequences(cleaned: list[dict]) -> list[dict]:
    """Number the segments, honouring explicit sequences when every row has one."""
    provided = [row["sequence"] for row in cleaned if row["sequence"] is not None]

    if provided and len(provided) != len(cleaned):
        raise TranscriptIngestError(
            "Either every segment must carry a sequence, or none of them."
        )

    if provided:
        if len(set(provided)) != len(provided):
            raise TranscriptIngestError(
                "Segment sequences must be unique.", code=errors.DUPLICATE_SEGMENT_SEQUENCE
            )
        if any(value < 1 for value in provided):
            raise TranscriptIngestError("Segment sequences must start at 1.")
        return sorted(cleaned, key=lambda row: row["sequence"])

    # No sequences given: order by start time, which is the order a listener
    # hears them, and number from 1.
    ordered = sorted(cleaned, key=lambda row: row["start_time"])
    for position, row in enumerate(ordered, start=1):
        row["sequence"] = position
    return ordered


def _require_number(raw: dict, key: str, index: int) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptIngestError(f"Segment {index}: {key} must be a number.")
    return float(value)


def _require_text(raw: dict, index: int) -> str:
    value = raw.get("text")
    if not isinstance(value, str) or not value.strip():
        raise TranscriptIngestError(f"Segment {index}: text must be a non-empty string.")
    return value.strip()
