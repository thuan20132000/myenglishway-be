"""Serializers that exist purely to document error shapes in the schema."""

from rest_framework import serializers


class ErrorSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Stable machine-readable error code.")
    detail = serializers.CharField(help_text="Human-readable explanation.")
    extra = serializers.DictField(
        required=False, help_text="Structured detail specific to the error code."
    )


class ValidationErrorSerializer(serializers.Serializer):
    """Field validation failures.

    Keeps DRF's native shape - one key per offending field, each holding a list
    of messages - with a top-level ``code`` added, so clients keep their
    field-to-error mapping.
    """

    code = serializers.CharField(
        default="VALIDATION_ERROR", help_text="Always VALIDATION_ERROR."
    )
    # Field errors appear as additional properties: {"title": ["This field is required."]}


class OwnerRefSerializer(serializers.Serializer):
    """Compact owner reference embedded in exercise payloads."""

    id = serializers.IntegerField(read_only=True)
    full_name = serializers.CharField(read_only=True)


def absolute_media_url(file_field, request) -> str | None:
    """Absolute URL for an uploaded file, or None when the field is empty.

    S3 (and any CDN domain) already returns an absolute URL; prefixing it with
    the API origin would break the fetch. Shared by every domain that hands a
    media URL to the client.
    """
    if not file_field:
        return None
    url = file_field.url
    if url.startswith(("http://", "https://")):
        return url
    return request.build_absolute_uri(url) if request else url
