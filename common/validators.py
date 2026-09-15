"""Upload validation shared across practice domains.

Lives here rather than in one app because more than one domain accepts the same
kind of file: ``listening`` and ``reading`` take audio, ``listening`` and
``writing`` take PDFs (with different size ceilings). Raised as DRF
``ValidationError`` subclasses carrying a domain code, so the client sees a
stable ``INVALID_PDF_FILE`` / ``INVALID_AUDIO_FILE`` rather than having to
match on prose.
"""

from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from common import errors


def validate_pdf_file(uploaded_file, *, max_mb: int | None = None):
    """Validate an uploaded PDF's extension, content type, size and header.

    ``max_mb`` defaults to ``settings.MAX_PDF_FILE_SIZE_MB``; callers storing
    larger documents pass their own ceiling. Returns the file unchanged so it
    can be used directly as a serializer field validator.
    """
    if uploaded_file in (None, ""):
        return uploaded_file

    if max_mb is None:
        max_mb = settings.MAX_PDF_FILE_SIZE_MB

    _validate_pdf_extension(uploaded_file)
    _validate_pdf_content_type(uploaded_file)
    _validate_pdf_size(uploaded_file, max_mb)
    _validate_pdf_magic_bytes(uploaded_file)
    return uploaded_file


def _validate_pdf_extension(uploaded_file):
    extension = Path(uploaded_file.name or "").suffix.lower()
    allowed = settings.ALLOWED_PDF_EXTENSIONS
    if extension not in allowed:
        raise serializers.ValidationError(
            f"Unsupported document format '{extension or 'unknown'}'. "
            f"Allowed formats: {', '.join(allowed)}.",
            code=errors.INVALID_PDF_FILE,
        )


def _validate_pdf_content_type(uploaded_file):
    content_type = getattr(uploaded_file, "content_type", None)
    # Only a defence-in-depth check, since clients control this header and some
    # do not send one at all.
    if content_type and content_type not in settings.ALLOWED_PDF_CONTENT_TYPES:
        raise serializers.ValidationError(
            f"Unsupported content type '{content_type}'.",
            code=errors.INVALID_PDF_FILE,
        )


def _validate_pdf_size(uploaded_file, max_mb: int):
    max_bytes = max_mb * 1024 * 1024
    size = getattr(uploaded_file, "size", 0) or 0
    if size > max_bytes:
        raise serializers.ValidationError(
            f"PDF file is too large ({size / 1024 / 1024:.1f} MB). "
            f"Maximum size is {max_mb} MB.",
            code=errors.PDF_FILE_TOO_LARGE,
        )
    if size == 0:
        raise serializers.ValidationError(
            "The uploaded PDF file is empty.",
            code=errors.INVALID_PDF_FILE,
        )


def _validate_pdf_magic_bytes(uploaded_file):
    """Check the file really starts with the PDF header.

    Worth the few lines: both the extension and the content type come from the
    client, and this file is served back to browsers from our own origin.
    Rewinds afterwards so the storage backend still writes the whole file.
    """
    uploaded_file.seek(0)
    header = uploaded_file.read(5)
    uploaded_file.seek(0)
    if header != b"%PDF-":
        raise serializers.ValidationError(
            "That file is not a PDF.",
            code=errors.INVALID_PDF_FILE,
        )


def validate_audio_file(uploaded_file):
    """Validate an uploaded audio file's extension, content type and size.

    Returns the file unchanged so it can be used directly as a serializer
    field validator.
    """
    if uploaded_file in (None, ""):
        return uploaded_file

    _validate_audio_extension(uploaded_file)
    _validate_audio_content_type(uploaded_file)
    _validate_audio_size(uploaded_file)
    return uploaded_file


def _validate_audio_extension(uploaded_file):
    extension = Path(uploaded_file.name or "").suffix.lower()
    allowed = settings.ALLOWED_AUDIO_EXTENSIONS
    if extension not in allowed:
        raise serializers.ValidationError(
            f"Unsupported audio format '{extension or 'unknown'}'. "
            f"Allowed formats: {', '.join(allowed)}.",
            code=errors.INVALID_AUDIO_FILE,
        )


def _validate_audio_content_type(uploaded_file):
    content_type = getattr(uploaded_file, "content_type", None)
    # Test clients and internal ingestion may not set a content type; the
    # extension check above is the required gate, this is defence in depth.
    if content_type and content_type not in settings.ALLOWED_AUDIO_CONTENT_TYPES:
        raise serializers.ValidationError(
            f"Unsupported content type '{content_type}'.",
            code=errors.INVALID_AUDIO_FILE,
        )


def _validate_audio_size(uploaded_file):
    max_bytes = settings.MAX_AUDIO_FILE_SIZE_MB * 1024 * 1024
    size = getattr(uploaded_file, "size", 0) or 0
    if size > max_bytes:
        raise serializers.ValidationError(
            f"Audio file is too large ({size / 1024 / 1024:.1f} MB). "
            f"Maximum size is {settings.MAX_AUDIO_FILE_SIZE_MB} MB.",
            code=errors.AUDIO_FILE_TOO_LARGE,
        )
    if size == 0:
        raise serializers.ValidationError(
            "The uploaded audio file is empty.",
            code=errors.INVALID_AUDIO_FILE,
        )
