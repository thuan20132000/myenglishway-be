"""Practice domain operations.

The API layer never touches the scorer directly: it hands a raw answer to
``submit_answer`` and gets back a persisted attempt. That boundary is what lets
the scoring implementation change without any view knowing.
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, QuerySet, Subquery

from common import errors
from listening.models import ListeningExercise, TranscriptSegment

from .models import AttemptKind, PracticeAttempt
from .normalization import DEFAULT_CONFIG, normalize_answer
from .scoring import score_answer


class TranscriptNotAvailable(errors.ConflictError):
    default_code = errors.TRANSCRIPT_NOT_AVAILABLE
    default_detail = "This segment has no transcript to score against."


@transaction.atomic
def submit_answer(*, user, segment: TranscriptSegment, raw_answer: str) -> PracticeAttempt:
    """Score a dictation answer and record the attempt.

    Repeat submissions are allowed and each creates a new attempt: retrying a
    segment is the core practice loop, not a duplicate to be suppressed.
    """
    expected = (segment.text or "").strip()
    if not expected:
        raise TranscriptNotAvailable()

    result = score_answer(expected, raw_answer, DEFAULT_CONFIG)
    counts = result["counts"]

    return PracticeAttempt.objects.create(
        user=user,
        segment=segment,
        exercise_id=segment.exercise_id,
        kind=AttemptKind.SUBMISSION,
        user_answer=raw_answer,
        normalized_answer=normalize_answer(raw_answer, DEFAULT_CONFIG),
        expected_snapshot=expected,
        score=result["score"],
        correct_count=counts["correct"],
        missing_count=counts["missing"],
        extra_count=counts["extra"],
        incorrect_count=counts["incorrect"],
        result=_display_result(result),
        scoring_version=result["version"],
    )


def _display_result(result: dict) -> dict:
    """Strip the fields already stored as columns from the JSON payload."""
    return {
        "correct": result["correct"],
        "missing": result["missing"],
        "extra": result["extra"],
        "incorrect": result["incorrect"],
        "counts": result["counts"],
    }


def is_segment_complete(attempt: PracticeAttempt) -> bool:
    """Whether an attempt's score clears the completion threshold."""
    return attempt.score is not None and attempt.score >= settings.PRACTICE_COMPLETION_THRESHOLD


# --------------------------------------------------------------------------
# Reveal
# --------------------------------------------------------------------------


@transaction.atomic
def reveal_segment(*, user, segment: TranscriptSegment) -> PracticeAttempt:
    """Record that a learner gave up and read the transcript.

    Stored as an attempt with ``kind=reveal`` and no score, so history and
    progress keep reading one table. Repeat reveals are allowed and each is
    recorded: how often a learner needed the answer is signal worth keeping,
    and collapsing them would lose it.
    """
    expected = (segment.text or "").strip()
    if not expected:
        raise TranscriptNotAvailable()

    return PracticeAttempt.objects.create(
        user=user,
        segment=segment,
        exercise_id=segment.exercise_id,
        kind=AttemptKind.REVEAL,
        user_answer="",
        normalized_answer="",
        expected_snapshot=expected,
        score=None,
        result={},
    )


# --------------------------------------------------------------------------
# Practice reads
# --------------------------------------------------------------------------


def _submissions_for(user, segment_ref=OuterRef("pk")):
    return PracticeAttempt.objects.filter(
        user=user, segment=segment_ref, kind=AttemptKind.SUBMISSION
    )


def practice_segments(user, exercise: ListeningExercise) -> QuerySet[TranscriptSegment]:
    """Segments annotated with this learner's progress on each.

    The annotations are correlated subqueries rather than per-row lookups, so
    the whole list costs one query no matter how many segments it holds.
    """
    submissions = _submissions_for(user)
    best_score = (
        submissions.values("segment")
        .annotate(best=Max("score"))
        .values("best")[:1]
    )

    return (
        exercise.segments.annotate(
            attempted=Exists(submissions),
            best_score=Subquery(best_score),
        )
        .only("id", "sequence", "start_time", "end_time", "word_count", "exercise_id")
        .order_by("sequence")
    )


def next_segment(user, exercise: ListeningExercise) -> TranscriptSegment | None:
    """The lowest-sequence segment this learner has not yet submitted an answer to.

    This is the whole of resume support: where a learner left off is derivable
    from their attempts, so no session row has to be written or kept in sync.
    Returns None once every segment has been attempted.
    """
    return (
        exercise.segments.annotate(attempted=Exists(_submissions_for(user)))
        .filter(attempted=False)
        .order_by("sequence")
        .first()
    )


def attempted_segment_count(user, exercise: ListeningExercise) -> int:
    """Distinct segments this learner has submitted an answer to."""
    return (
        PracticeAttempt.objects.filter(
            user=user, exercise=exercise, kind=AttemptKind.SUBMISSION
        )
        .values("segment")
        .distinct()
        .count()
    )


# --------------------------------------------------------------------------
# Progress and history
#
# Both are derived from PracticeAttempt rather than stored. The aggregates are
# grouped in the database and combined in Python only over already-grouped
# rows, so the query count is constant in the number of segments and attempts.
#
# Definitions used throughout, and exposed in the API docs:
#   attempted  - distinct segments with at least one submission
#   completed  - distinct segments whose BEST submission score reaches
#                settings.PRACTICE_COMPLETION_THRESHOLD
#   average    - mean of each attempted segment's BEST score, so retrying a
#                segment can only ever raise a learner's average
#   revealed   - distinct segments the learner asked to see the answer for;
#                never counted as attempted or completed
# --------------------------------------------------------------------------


def _best_scores_per_segment(user, *, exercise_ids=None, extra_filters=None):
    """One row per (exercise, segment) the learner has submitted to.

    Grouped in SQL; the caller folds the rows together. Includes the segment's
    sequence so callers can report how far a learner has reached without a
    second query.
    """
    queryset = PracticeAttempt.objects.filter(user=user, kind=AttemptKind.SUBMISSION)
    if exercise_ids is not None:
        queryset = queryset.filter(exercise_id__in=exercise_ids)
    if extra_filters is not None:
        queryset = queryset.filter(extra_filters)

    return list(
        queryset.values("exercise_id", "segment_id", "segment__sequence")
        .annotate(best=Max("score"))
        .order_by()
    )


def _summarise(rows: list[dict]) -> dict:
    """Fold per-segment best scores into learner-facing totals."""
    threshold = settings.PRACTICE_COMPLETION_THRESHOLD
    scores = [row["best"] for row in rows if row["best"] is not None]

    return {
        "attempted_segments": len(rows),
        "completed_segments": sum(1 for score in scores if score >= threshold),
        "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        "last_segment_sequence": (
            max(row["segment__sequence"] for row in rows) if rows else None
        ),
    }


def exercise_progress(user, exercise: ListeningExercise) -> dict:
    """This learner's progress on one exercise."""
    rows = _best_scores_per_segment(user, exercise_ids=[exercise.id])
    summary = _summarise(rows)

    totals = PracticeAttempt.objects.filter(user=user, exercise=exercise).aggregate(
        revealed_segments=Count(
            "segment", distinct=True, filter=Q(kind=AttemptKind.REVEAL)
        ),
        last_practiced_at=Max("created_at"),
    )

    total_segments = exercise.segments.count()
    completed = summary["completed_segments"]

    return {
        "exercise_id": exercise.id,
        "total_segments": total_segments,
        "progress_percentage": (
            round(100.0 * completed / total_segments, 1) if total_segments else 0.0
        ),
        **summary,
        **totals,
    }


def practice_history(user, *, exercise_id=None, date_from=None, date_to=None):
    """Per-exercise practice summaries, most recently practised first.

    Returns a queryset of grouped rows so the caller can paginate in SQL; the
    expensive per-segment work in :func:`history_details` then runs only for the
    exercises on the requested page.
    """
    queryset = PracticeAttempt.objects.filter(user=user)

    if exercise_id is not None:
        queryset = queryset.filter(exercise_id=exercise_id)
    if date_from is not None:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(created_at__date__lte=date_to)

    return (
        queryset.values("exercise_id")
        .annotate(
            total_attempts=Count("id"),
            last_practiced_at=Max("created_at"),
        )
        .order_by("-last_practiced_at")
    )


def history_details(user, rows: list[dict], *, date_from=None, date_to=None) -> list[dict]:
    """Attach per-exercise summaries and metadata to one page of history rows."""
    exercise_ids = [row["exercise_id"] for row in rows]
    if not exercise_ids:
        return []

    date_filter = Q()
    if date_from is not None:
        date_filter &= Q(created_at__date__gte=date_from)
    if date_to is not None:
        date_filter &= Q(created_at__date__lte=date_to)

    grouped: dict[int, list[dict]] = {exercise_id: [] for exercise_id in exercise_ids}
    for row in _best_scores_per_segment(
        user, exercise_ids=exercise_ids, extra_filters=date_filter or None
    ):
        grouped[row["exercise_id"]].append(row)

    exercises = {
        exercise.id: exercise
        for exercise in ListeningExercise.objects.filter(id__in=exercise_ids).annotate(
            total_segments=Count("segments")
        )
    }

    details = []
    for row in rows:
        exercise = exercises.get(row["exercise_id"])
        summary = _summarise(grouped[row["exercise_id"]])
        details.append(
            {
                "exercise": {
                    "id": row["exercise_id"],
                    "title": exercise.title if exercise else None,
                    "total_segments": exercise.total_segments if exercise else 0,
                },
                "total_attempts": row["total_attempts"],
                "last_practiced_at": row["last_practiced_at"],
                "attempted_segments": summary["attempted_segments"],
                "completed_segments": summary["completed_segments"],
                "average_score": summary["average_score"],
            }
        )
    return details
