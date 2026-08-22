"""Stable domain error codes and the exception type that carries them.

Field validation keeps DRF's native ``{"field": ["message"]}`` body so clients
and the generated schema can map errors back to inputs. Domain errors - the
ones that describe a state the resource is in rather than a bad input field -
carry one of the codes below so clients can branch on a stable string instead
of parsing prose.
"""

from rest_framework import status
from rest_framework.exceptions import APIException

# --------------------------------------------------------------------------
# Code registry
# --------------------------------------------------------------------------

VALIDATION_ERROR = "VALIDATION_ERROR"
AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
TOKEN_INVALID = "TOKEN_INVALID"
PERMISSION_DENIED = "PERMISSION_DENIED"
NOT_FOUND = "NOT_FOUND"

EXERCISE_NOT_FOUND = "EXERCISE_NOT_FOUND"
EXERCISE_NOT_READY = "EXERCISE_NOT_READY"
EXERCISE_NOT_PUBLISHED = "EXERCISE_NOT_PUBLISHED"
EXERCISE_ALREADY_PUBLISHED = "EXERCISE_ALREADY_PUBLISHED"
EXERCISE_PROCESSING = "EXERCISE_PROCESSING"

INVALID_AUDIO_FILE = "INVALID_AUDIO_FILE"
AUDIO_FILE_TOO_LARGE = "AUDIO_FILE_TOO_LARGE"

INVALID_PDF_FILE = "INVALID_PDF_FILE"
PDF_FILE_TOO_LARGE = "PDF_FILE_TOO_LARGE"
INVALID_ANSWER_KEY = "INVALID_ANSWER_KEY"

SEGMENT_NOT_FOUND = "SEGMENT_NOT_FOUND"
INVALID_SEGMENT_RANGE = "INVALID_SEGMENT_RANGE"
DUPLICATE_SEGMENT_SEQUENCE = "DUPLICATE_SEGMENT_SEQUENCE"
SEGMENT_OUTSIDE_AUDIO = "SEGMENT_OUTSIDE_AUDIO"

TRANSCRIPT_NOT_AVAILABLE = "TRANSCRIPT_NOT_AVAILABLE"
TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"

COLLECTION_NOT_FOUND = "COLLECTION_NOT_FOUND"
COLLECTION_DEPTH_EXCEEDED = "COLLECTION_DEPTH_EXCEEDED"
COLLECTION_CYCLE = "COLLECTION_CYCLE"
COLLECTION_HAS_CHILDREN = "COLLECTION_HAS_CHILDREN"
COLLECTION_HAS_MEMBERS = "COLLECTION_HAS_MEMBERS"
COLLECTION_OWNER_MISMATCH = "COLLECTION_OWNER_MISMATCH"
COLLECTION_ALREADY_PUBLISHED = "COLLECTION_ALREADY_PUBLISHED"
EXERCISE_ALREADY_IN_COLLECTION = "EXERCISE_ALREADY_IN_COLLECTION"


class DomainError(APIException):
    """An error about resource state, carrying a stable machine-readable code.

    ``extra`` holds structured detail the client can act on - for example the
    list of reasons an exercise failed its publish preconditions.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The request could not be completed."
    default_code = "DOMAIN_ERROR"

    def __init__(self, detail=None, code=None, extra=None, status_code=None):
        self.extra = extra or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail or self.default_detail, code or self.default_code)


class ConflictError(DomainError):
    """A domain error where the resource exists but is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
