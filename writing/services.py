"""Everything that changes writing state.

Views validate shapes and services enforce rules, the same split ``listening``
uses. The rules here are few, because there is nothing to score and no async
pipeline: count the pages of an uploaded PDF, keep a notebook's pages in range,
and refuse to swap a workbook out from under learners who have started it.
"""

from django.db import transaction
from django.utils import timezone
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from common import errors
from common.errors import ConflictError, DomainError

from .models import WritingExercise, WritingNotebook, WritingPage


class UnreadablePdf(DomainError):
    default_code = errors.UNREADABLE_PDF_FILE
    default_detail = "That PDF could not be read."


class PageOutOfRange(DomainError):
    default_code = errors.PAGE_OUT_OF_RANGE
    default_detail = "That page is outside this workbook."


class WorkbookInUse(ConflictError):
    default_code = errors.WRITING_EXERCISE_HAS_NOTEBOOKS
    default_detail = "This workbook cannot be changed now."


class WorkbookAlreadyPublished(ConflictError):
    default_code = errors.EXERCISE_ALREADY_PUBLISHED
    default_detail = "This workbook is already published."


# --------------------------------------------------------------------------
# The PDF
# --------------------------------------------------------------------------


def count_pdf_pages(uploaded_file) -> int:
    """Read the page count out of an uploaded PDF.

    Runs after ``validate_writing_pdf``, and rewinds afterwards so the storage
    backend still writes the whole file. The client never supplies this number:
    every page range check downstream trusts it.
    """
    uploaded_file.seek(0)
    try:
        reader = PdfReader(uploaded_file)
        # An encrypted workbook reports zero pages rather than raising, so the
        # check below catches it along with anything else that yields nothing.
        page_count = len(reader.pages)
    except (PyPdfError, ValueError, OSError) as exc:
        raise UnreadablePdf(
            "That PDF could not be read. It may be corrupt or password-protected."
        ) from exc
    finally:
        uploaded_file.seek(0)

    if page_count < 1:
        raise UnreadablePdf("That PDF has no pages, or is password-protected.")
    return page_count


def _discard_file(file_field) -> None:
    """Drop a replaced file from storage without touching the model row."""
    if file_field:
        file_field.delete(save=False)


# --------------------------------------------------------------------------
# Workbooks
# --------------------------------------------------------------------------


def create_exercise(*, owner, pdf_file=None, **fields) -> WritingExercise:
    page_count = count_pdf_pages(pdf_file) if pdf_file else 1
    return WritingExercise.objects.create(
        owner=owner, pdf_file=pdf_file, page_count=page_count, **fields
    )


def assert_pdf_replaceable(exercise: WritingExercise) -> None:
    """Refuse a swap that would strand work already done against the old file.

    Page numbers are the only link between a notebook and the PDF, so a shorter
    or re-paginated replacement silently misfiles every page a learner wrote.
    Rejecting is the honest answer; the owner can delete the workbook or upload
    a new one.

    Attaching the *first* PDF is not a replacement: a prompt-only task has one
    writing page, and adding a sheet only expands the range.
    """
    if not exercise.pdf_file:
        return
    if exercise.is_published:
        raise WorkbookInUse(
            "Unpublish the workbook before replacing its PDF.",
            extra={"reasons": ["workbook_is_published"]},
        )
    if exercise.notebooks.exists():
        raise WorkbookInUse(
            "Learners have started this workbook, so its PDF can no longer be "
            "replaced. Upload the new edition as a separate workbook.",
            extra={"reasons": ["notebooks_exist"]},
        )


@transaction.atomic
def replace_pdf(exercise: WritingExercise, pdf_file) -> WritingExercise:
    assert_pdf_replaceable(exercise)
    previous = exercise.pdf_file
    exercise.page_count = count_pdf_pages(pdf_file)
    exercise.pdf_file = pdf_file
    exercise.save(update_fields=["pdf_file", "page_count", "updated_at"])
    _discard_file(previous)
    return exercise


@transaction.atomic
def update_exercise(exercise: WritingExercise, **fields) -> WritingExercise:
    """Apply a metadata patch, routing a new PDF through ``replace_pdf``.

    ``page_count``, ``is_published`` and ``published_at`` are not settable here:
    the first is derived, the other two are state transitions with their own
    endpoints.
    """
    pdf_file = fields.pop("pdf_file", None)
    if pdf_file:
        replace_pdf(exercise, pdf_file)

    for key, value in fields.items():
        setattr(exercise, key, value)
    if fields:
        exercise.save(update_fields=[*fields.keys(), "updated_at"])
    return exercise


def check_publish_preconditions(exercise: WritingExercise) -> list[str]:
    """Reasons the workbook cannot be published, empty when it can.

    A prompt-only task (no PDF) is publishable: publishing shares the title and
    description, the same way reading publishes a body. A missing PDF is not a
    blocker.
    """
    return []


def publish_exercise(exercise: WritingExercise) -> WritingExercise:
    if exercise.is_published:
        raise WorkbookAlreadyPublished()
    exercise.is_published = True
    exercise.published_at = timezone.now()
    exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


def unpublish_exercise(exercise: WritingExercise) -> WritingExercise:
    """Idempotent, matching listening: withdrawing twice is not an error."""
    if exercise.is_published:
        exercise.is_published = False
        exercise.published_at = None
        exercise.save(update_fields=["is_published", "published_at", "updated_at"])
    return exercise


# --------------------------------------------------------------------------
# Notebooks
# --------------------------------------------------------------------------


def get_or_create_notebook(*, user, exercise: WritingExercise) -> WritingNotebook:
    """Opening a workbook is what starts a notebook - there is no "enrol" step."""
    notebook, _created = WritingNotebook.objects.get_or_create(user=user, exercise=exercise)
    return notebook


def assert_page_in_range(exercise: WritingExercise, page_number: int) -> None:
    if page_number < 1 or page_number > exercise.page_count:
        raise PageOutOfRange(
            f"This workbook has {exercise.page_count} pages, so page "
            f"{page_number} does not exist.",
            extra={"page_count": exercise.page_count},
        )


@transaction.atomic
def upsert_page(*, notebook: WritingNotebook, page_number: int, body: str) -> WritingPage:
    """Save one page. The autosave endpoint's whole job.

    Idempotent by (notebook, page): a repeat save overwrites rather than
    appending, which is the difference between a notebook and an attempt log.
    An empty ``body`` is a legitimate save - the learner cleared the page.
    """
    assert_page_in_range(notebook.exercise, page_number)

    page, _created = WritingPage.objects.get_or_create(
        notebook=notebook, page_number=page_number
    )
    page.body = body
    # Not queryset.update(): word_count is derived in WritingPage.save().
    page.save(update_fields=["body", "word_count", "updated_at"])

    # Saved unconditionally, even when the bookmark has not moved: the notebook's
    # updated_at is what orders the "continue writing" list, and re-saving the
    # page you are already on is exactly the case that should float it to the top.
    notebook.last_page = page_number
    notebook.save(update_fields=["last_page", "updated_at"])
    return page


def mark_complete(notebook: WritingNotebook) -> WritingNotebook:
    """Idempotent: re-marking keeps the original completion time."""
    if notebook.completed_at is None:
        notebook.completed_at = timezone.now()
        notebook.save(update_fields=["completed_at", "updated_at"])
    return notebook


def clear_complete(notebook: WritingNotebook) -> WritingNotebook:
    if notebook.completed_at is not None:
        notebook.completed_at = None
        notebook.save(update_fields=["completed_at", "updated_at"])
    return notebook
