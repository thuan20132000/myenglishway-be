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
