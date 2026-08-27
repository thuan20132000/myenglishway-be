import pytest
from django.db import IntegrityError, transaction

from writing.models import WritingExercise, WritingNotebook, WritingPage

pytestmark = pytest.mark.django_db


def test_pdf_is_namespaced_by_owner(workbook_factory, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    workbook = workbook_factory()
    assert workbook.pdf_file.name.startswith(f"writing-pdf/{workbook.owner_id}/")
    assert workbook.pdf_file.name.endswith(".pdf")


def test_page_count_must_be_positive(workbook_factory):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            workbook_factory(page_count=0)


def test_one_notebook_per_user_per_workbook(workbook_factory, notebook_factory, student):
    workbook = workbook_factory()
    notebook_factory(user=student, exercise=workbook)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            notebook_factory(user=student, exercise=workbook)


def test_two_learners_get_separate_notebooks(workbook_factory, notebook_factory, student, creator):
    workbook = workbook_factory()
    notebook_factory(user=student, exercise=workbook)
    notebook_factory(user=creator, exercise=workbook)
    assert workbook.notebooks.count() == 2


def test_one_row_per_page_number(notebook_factory, page_factory):
    notebook = notebook_factory()
    page_factory(notebook=notebook, page_number=3)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            page_factory(notebook=notebook, page_number=3)


def test_word_count_is_derived_on_save(notebook_factory, page_factory):
    page = page_factory(notebook=notebook_factory(), body="one two three")
    assert page.word_count == 3

    page.body = "one two three four five"
    page.save()
    page.refresh_from_db()
    assert page.word_count == 5


def test_clearing_a_page_zeroes_its_word_count(notebook_factory, page_factory):
    page = page_factory(notebook=notebook_factory(), body="something")
    page.body = ""
    page.save()
    assert page.word_count == 0


def test_deleting_a_workbook_cascades_to_notebooks_and_pages(
    workbook_factory, notebook_factory, page_factory
):
    notebook = notebook_factory(exercise=workbook_factory())
    page_factory(notebook=notebook, page_number=1)

    notebook.exercise.delete()

    assert not WritingExercise.objects.exists()
    assert not WritingNotebook.objects.exists()
    assert not WritingPage.objects.exists()


def test_is_complete_reflects_completed_at(notebook_factory):
    notebook = notebook_factory()
    assert notebook.is_complete is False
    notebook.completed_at = "2026-08-26T10:00:00Z"
    assert notebook.is_complete is True
