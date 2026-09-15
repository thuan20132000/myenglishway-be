"""Everything that changes reading state.

Views validate shapes and services enforce rules, the same split ``writing``
uses. Animation and WPM clocks live in the client; the rules here are storing
a passage, optional audio, and finishing a session with a server-computed
actual WPM.
"""

from django.db import transaction
from django.utils import timezone

from common import errors
from common.errors import ConflictError

from .models import ReadingExercise, ReadingMode, ReadingSession


class PassageAlreadyPublished(ConflictError):
    default_code = errors.EXERCISE_ALREADY_PUBLISHED
    default_detail = "This passage is already published."


class SessionAlreadyFinished(ConflictError):
    default_code = errors.SESSION_ALREADY_FINISHED
    default_detail = "This reading session has already been finished."


def _discard_file(file_field) -> None:
    """Drop a replaced file from storage without touching the model row."""
    if file_field:
        file_field.delete(save=False)


def create_exercise(*, owner, **fields) -> ReadingExercise:
    return ReadingExercise.objects.create(owner=owner, **fields)


@transaction.atomic
def update_exercise(exercise: ReadingExercise, **fields) -> ReadingExercise:
    """Apply a metadata or body patch, routing a new audio file separately.

    ``word_count``, ``is_published`` and ``published_at`` are not settable here:
    the first is derived, the other two are state transitions with their own
    endpoints.
    """
    audio_file = fields.pop("audio_file", None)
    if audio_file:
        replace_audio(exercise, audio_file)

    for key, value in fields.items():
        setattr(exercise, key, value)
    if fields:
        # Body changes recompute word_count inside ReadingExercise.save().
        exercise.save()
    return exercise


@transaction.atomic
def replace_audio(exercise: ReadingExercise, audio_file) -> ReadingExercise:
    previous = exercise.audio_file
    exercise.audio_file = audio_file
    exercise.save(update_fields=["audio_file", "updated_at"])
    _discard_file(previous)
    return exercise


@transaction.atomic
def clear_audio(exercise: ReadingExercise) -> ReadingExercise:
    previous = exercise.audio_file
    exercise.audio_file = None
    exercise.save(update_fields=["audio_file", "updated_at"])
    _discard_file(previous)
    return exercise


def publish_exercise(exercise: ReadingExercise) -> ReadingExercise:
    if exercise.is_published:
        raise PassageAlreadyPublished()
    exercise.is_published = True
    exercise.published_at = timezone.now()
    exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


def unpublish_exercise(exercise: ReadingExercise) -> ReadingExercise:
    """Idempotent, matching writing: withdrawing twice is not an error."""
    if exercise.is_published:
        exercise.is_published = False
        exercise.published_at = None
        exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


def start_session(
    *,
    user,
    exercise: ReadingExercise,
    mode: str = ReadingMode.PACED,
    target_wpm: int,
    strict_mode: bool = False,
) -> ReadingSession:
    return ReadingSession.objects.create(
        user=user,
        exercise=exercise,
        mode=mode,
        target_wpm=target_wpm,
        strict_mode=strict_mode,
        word_count=exercise.word_count,
        started_at=timezone.now(),
    )


def finish_session(
    session: ReadingSession,
    *,
    reading_ms: int,
    paused_ms: int = 0,
    progress: int,
) -> ReadingSession:
    if session.finished_at is not None:
        raise SessionAlreadyFinished()
    session.reading_ms = reading_ms
    session.paused_ms = paused_ms
    session.progress = progress
    session.finished_at = timezone.now()
    session.actual_wpm = session.word_count * 60_000 / reading_ms
    session.save(
        update_fields=[
            "reading_ms",
            "paused_ms",
            "progress",
            "finished_at",
            "actual_wpm",
            "updated_at",
        ]
    )
    return session
