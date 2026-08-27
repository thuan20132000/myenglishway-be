"""Fixtures for the writing tests.

The workbook uploads here have to be *real* PDFs, not the `%PDF-1.4 fake`
placeholder the listening tests use: ``services.count_pdf_pages`` opens the
file and counts its pages, so a stub gets rejected before it reaches storage.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfWriter

from tests.factories import (
    WritingExerciseFactory,
    WritingNotebookFactory,
    WritingPageFactory,
)


def pdf_bytes(pages: int = 3) -> bytes:
    """A minimal, genuinely readable PDF with the requested number of pages."""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)  # A4 in points
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def pdf_upload(pages: int = 3, name: str = "workbook.pdf", content_type: str = "application/pdf"):
    return SimpleUploadedFile(name, pdf_bytes(pages), content_type=content_type)


@pytest.fixture
def workbook_factory():
    return WritingExerciseFactory


@pytest.fixture
def notebook_factory():
    return WritingNotebookFactory


@pytest.fixture
def page_factory():
    return WritingPageFactory
