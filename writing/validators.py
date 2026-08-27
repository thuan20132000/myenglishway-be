"""Upload validation for writing workbooks.

The rules are ``common.validators``'; only the ceiling differs, because a
workbook is a whole book rather than a two-page question sheet.
"""

from django.conf import settings

from common.validators import validate_pdf_file


def validate_writing_pdf(uploaded_file):
    """Validate a workbook PDF, returning it unchanged."""
    return validate_pdf_file(uploaded_file, max_mb=settings.MAX_WRITING_PDF_FILE_SIZE_MB)
