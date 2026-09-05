"""Practice domain operations.

The API layer never touches the scorer directly: it hands a raw answer to
``submit_answer`` and gets back a persisted attempt. That boundary is what lets
the scoring implementation change without any view knowing.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import (
    Count,
    DateTimeField,
    Exists,
    IntegerField,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
)
from django.db.models.functions import Coalesce

from common import errors
from listening.models import ListeningExercise, TranscriptSegment

from .models import AttemptKind, PracticeAttempt
from .normalization import DEFAULT_CONFIG, normalize_answer
from .scoring import score_answer

User = get_user_model()


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


def _best_scores_per_segment(user=None, *, user_ids=None, exercise_ids=None, extra_filters=None):
    """One row per (user, exercise, segment) submitted to.

    Grouped in SQL; the caller folds the rows together. Includes the segment's
    sequence so callers can report how far a learner has reached without a
    second query. ``user_id`` is always present so a leaderboard page can fold
    several learners in one pass.
    """
    queryset = PracticeAttempt.objects.filter(kind=AttemptKind.SUBMISSION)
    if user is not None:
        queryset = queryset.filter(user=user)
    if user_ids is not None:
        queryset = queryset.filter(user_id__in=user_ids)
    if exercise_ids is not None:
        queryset = queryset.filter(exercise_id__in=exercise_ids)
    if extra_filters is not None:
        queryset = queryset.filter(extra_filters)

    return list(
        queryset.values("user_id", "exercise_id", "segment_id", "segment__sequence")
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


# --------------------------------------------------------------------------
# Leaderboard
#
# Ranked by scored submission count across all learners. Reveals are excluded
# so giving up cannot raise a rank; retries count, because the metric is how
# much a learner practised, not how many distinct segments they covered.
# --------------------------------------------------------------------------


def _submission_queryset(*, date_from=None, date_to=None):
    queryset = PracticeAttempt.objects.filter(kind=AttemptKind.SUBMISSION)
    if date_from is not None:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def _date_q(*, date_from=None, date_to=None) -> Q:
    q = Q()
    if date_from is not None:
        q &= Q(created_at__date__gte=date_from)
    if date_to is not None:
        q &= Q(created_at__date__lte=date_to)
    return q


class _CountSubquery(Subquery):
    """COUNT(*) over a possibly-empty grouped subquery.

    Used so a learner's rank is ``1 + how many learners have a strictly higher
    submission count``. Unlike ``RANK() OVER (...)``, this stays correct when
    the outer queryset is later filtered to a single user (the ``me`` row).
    """

    template = "(SELECT COUNT(*) FROM (%(subquery)s) _count)"
    output_field = IntegerField()


def practice_leaderboard(*, date_from=None, date_to=None):
    """Learners ranked by scored submission count, highest first.

    Returns a User queryset annotated with ``practice_count``,
    ``last_practiced_at`` and ``rank`` so the caller can paginate in SQL.
    Rank is ``RANK()`` semantics (ties share a number: 1, 2, 2, 4) without a
    window function, so filtering to the caller still yields their global rank.

    Learners with no submissions in the window are omitted.
    """
    submissions = _submission_queryset(date_from=date_from, date_to=date_to)
    counts = submissions.values("user_id").annotate(
        practice_count=Count("id"),
        last_practiced_at=Max("created_at"),
    )
    higher = (
        submissions.values("user_id")
        .annotate(c=Count("id"))
        .filter(c__gt=OuterRef("practice_count"))
        .values("user_id")
    )

    return (
        User.objects.filter(pk__in=Subquery(counts.values("user_id")))
        .annotate(
            practice_count=Subquery(
                counts.filter(user_id=OuterRef("pk")).values("practice_count")[:1],
                output_field=IntegerField(),
            ),
            last_practiced_at=Subquery(
                counts.filter(user_id=OuterRef("pk")).values("last_practiced_at")[:1],
                output_field=DateTimeField(),
            ),
        )
        .annotate(rank=Coalesce(_CountSubquery(higher), 0) + 1)
        .order_by("-practice_count", "pk")
    )


def leaderboard_details(rows, *, date_from=None, date_to=None) -> list[dict]:
    """Attach per-learner segment summaries to one page of ranked users."""
    rows = list(rows)
    if not rows:
        return []

    user_ids = [row.pk for row in rows]
    grouped: dict[int, list[dict]] = {user_id: [] for user_id in user_ids}
    for score_row in _best_scores_per_segment(
        user_ids=user_ids, extra_filters=_date_q(date_from=date_from, date_to=date_to) or None
    ):
        grouped[score_row["user_id"]].append(score_row)

    details = []
    for row in rows:
        summary = _summarise(grouped[row.pk])
        details.append(
            {
                "rank": int(row.rank),
                "user": {"id": row.pk, "full_name": row.full_name},
                "practice_count": row.practice_count,
                "attempted_segments": summary["attempted_segments"],
                "completed_segments": summary["completed_segments"],
                "average_score": summary["average_score"],
                "last_practiced_at": row.last_practiced_at,
            }
        )
    return details
