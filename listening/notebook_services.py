"""Notebook domain operations.

Views validate shapes; this module get-or-creates, upserts, and stamps
completion. There is no scoring and no page range — one body per learner per
exercise.
"""

from django.db import transaction
from django.utils import timezone

from .models import ListeningExercise, ListeningNotebook


def get_or_create_notebook(*, user, exercise: ListeningExercise) -> ListeningNotebook:
    """Opening an exercise is what starts a notebook — there is no enrol step."""
    notebook, _created = ListeningNotebook.objects.get_or_create(
        user=user, exercise=exercise
    )
    return notebook


@transaction.atomic
def upsert_body(*, notebook: ListeningNotebook, body: str) -> ListeningNotebook:
    """Overwrite the note. The autosave endpoint's whole job.

    Idempotent: a repeat save replaces rather than appends, which is the
    difference between a notebook and an attempt log. An empty ``body`` is a
    legitimate save — the learner cleared the textarea.
    """
    notebook.body = body
    notebook.save(update_fields=["body", "word_count", "updated_at"])
    return notebook


def mark_complete(notebook: ListeningNotebook) -> ListeningNotebook:
    """Idempotent: re-marking keeps the original completion time."""
    if notebook.completed_at is None:
        notebook.completed_at = timezone.now()
        notebook.save(update_fields=["completed_at", "updated_at"])
    return notebook


def clear_complete(notebook: ListeningNotebook) -> ListeningNotebook:
    if notebook.completed_at is not None:
        notebook.completed_at = None
        notebook.save(update_fields=["completed_at", "updated_at"])
    return notebook
