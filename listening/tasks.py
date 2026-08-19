"""Background transcription.

The task is intentionally thin: it sequences existing services and holds no
transcription logic and no ORM writes of its own. Everything it calls -
``mark_processing``, ``ingest_transcript``, ``mark_failed`` - predates this
module and is independently tested, which is what makes the task small enough
to reason about.
"""

import logging

from celery import shared_task
from django.conf import settings

from .models import ExerciseStatus, ListeningExercise
from .services import TranscriptIngestError, ingest_transcript, mark_failed, mark_processing
from .transcription import TranscriptionError, get_provider

logger = logging.getLogger(__name__)

EMPTY_TRANSCRIPT_MESSAGE = (
    "The provider returned no speech for this audio. Check that the file "
    "contains audible speech, or add the transcript manually."
)


@shared_task(
    bind=True,
    max_retries=settings.TRANSCRIPTION_MAX_RETRIES,
    default_retry_delay=settings.TRANSCRIPTION_RETRY_DELAY,
)
def transcribe_exercise(self, exercise_id: int, audio_name: str | None = None) -> str:
    """Transcribe one exercise's audio and store the result.

    ``audio_name`` is the file this task was queued for. If the exercise's
    audio has changed since, the task aborts: a slow run must never overwrite a
    newer recording's transcript with the previous one's.
    """
    print(f"Transcribing exercise {exercise_id} with audio name {audio_name}")
    exercise = ListeningExercise.objects.filter(pk=exercise_id).first()
    if exercise is None:
        logger.info("Exercise %s no longer exists; nothing to transcribe.", exercise_id)
        return "missing"

    if not exercise.has_audio:
        mark_failed(exercise, "The exercise has no audio to transcribe.")
        return "no-audio"

    if exercise.is_published:
        # A published exercise is being practised right now; replacing its
        # transcript underneath learners is never right. Left untouched rather
        # than marked failed, because nothing about it is broken.
        logger.info("Exercise %s is published; skipping transcription.", exercise_id)
        return "published"

    if audio_name and exercise.audio_file.name != audio_name:
        logger.info(
            "Audio for exercise %s changed since this task was queued; skipping.",
            exercise_id,
        )
        return "superseded"

    mark_processing(exercise)

    try:
        provider = get_provider()
        result = provider.transcribe(exercise.audio_file, language=exercise.language)
    except TranscriptionError as exc:
        return _handle_error(self, exercise, exc)

    if result.is_empty:
        mark_failed(exercise, EMPTY_TRANSCRIPT_MESSAGE)
        return "empty"

    try:
        ingest_transcript(
            exercise,
            segments=result.as_ingest_payload(),
            duration=result.duration,
            provider=result.provider,
        )
    except TranscriptIngestError as exc:
        # The payload is malformed, not the network - retrying the same audio
        # would produce the same payload.
        mark_failed(exercise, f"Transcript could not be stored: {exc.detail}")
        return "rejected"

    logger.info(
        "Transcribed exercise %s: %s segment(s) via %s.",
        exercise_id,
        len(result.segments),
        result.provider,
    )
    return "ready"


def _handle_error(task, exercise: ListeningExercise, exc: TranscriptionError) -> str:
    """Retry transient failures; record permanent ones for the creator."""
    if exc.retryable and task.request.retries < task.max_retries:
        # Leave the exercise in PROCESSING: a retry is still in flight, and
        # flipping it to FAILED would tell the creator it is over when it isn't.
        logger.warning(
            "Transcription of exercise %s failed (attempt %s), retrying: %s",
            exercise.pk,
            task.request.retries + 1,
            exc,
        )
        raise task.retry(exc=exc)

    mark_failed(exercise, str(exc))
    logger.error("Transcription of exercise %s failed permanently: %s", exercise.pk, exc)
    return "failed"


def is_transcribable(exercise: ListeningExercise) -> bool:
    """Whether a re-run may be started for this exercise right now."""
    return exercise.has_audio and exercise.status != ExerciseStatus.PROCESSING
