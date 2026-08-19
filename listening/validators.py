"""Upload validation for exercise audio.

Raised as DRF ValidationError subclasses carrying a domain code, so the client
sees a stable ``INVALID_AUDIO_FILE`` / ``AUDIO_FILE_TOO_LARGE`` rather than
having to match on prose.
"""

from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from common import errors


def validate_audio_file(uploaded_file):
    """Validate an uploaded audio file's extension, content type and size.

    Returns the file unchanged so it can be used directly as a serializer
    field validator.
    """
    if uploaded_file in (None, ""):
        return uploaded_file

    _validate_extension(uploaded_file)
    _validate_content_type(uploaded_file)
    _validate_size(uploaded_file)
    return uploaded_file


def _validate_extension(uploaded_file):
    extension = Path(uploaded_file.name or "").suffix.lower()
    allowed = settings.ALLOWED_AUDIO_EXTENSIONS
    if extension not in allowed:
        raise serializers.ValidationError(
            f"Unsupported audio format '{extension or 'unknown'}'. "
            f"Allowed formats: {', '.join(allowed)}.",
            code=errors.INVALID_AUDIO_FILE,
        )


def _validate_content_type(uploaded_file):
    content_type = getattr(uploaded_file, "content_type", None)
    # Test clients and internal ingestion may not set a content type; the
    # extension check above is the required gate, this is defence in depth.
    if content_type and content_type not in settings.ALLOWED_AUDIO_CONTENT_TYPES:
        raise serializers.ValidationError(
            f"Unsupported content type '{content_type}'.",
            code=errors.INVALID_AUDIO_FILE,
        )


def _validate_size(uploaded_file):
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
