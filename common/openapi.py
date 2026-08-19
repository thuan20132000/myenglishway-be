"""OpenAPI helpers shared across the API.

The 401 response is injected by a postprocessing hook rather than declared on
every view: it applies to every authenticated operation without exception, so
repeating it 25 times would be noise that eventually drifts out of sync.
"""

from drf_spectacular.utils import OpenApiExample

from .serializers import ErrorSerializer


def error_responses(*status_codes: int) -> dict:
    """Map status codes to the standard error body."""
    return {code: ErrorSerializer for code in status_codes}


UNAUTHENTICATED_RESPONSE = {
    "description": "Authentication credentials were not provided or are invalid.",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/Error"},
            "example": {
                "code": "AUTHENTICATION_FAILED",
                "detail": "Authentication credentials were not provided.",
            },
        }
    },
}


def add_unauthenticated_response(result, generator, request, public):
    """Document 401 on every operation that requires authentication.

    Operations that opted out of auth (``auth=[]``, i.e. register, login and
    token refresh) declare their own 401 semantics and are left alone.
    """
    for path_item in result.get("paths", {}).values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            # The schema declares no global security requirement, so an
            # operation is authenticated only if it carries a non-empty one of
            # its own. Public endpoints (auth=[]) have the key omitted entirely
            # rather than set to [], so test for falsiness, not equality.
            if not operation.get("security"):
                continue
            operation.setdefault("responses", {}).setdefault(
                "401", UNAUTHENTICATED_RESPONSE
            )
    return result


PERMISSION_DENIED_EXAMPLE = OpenApiExample(
    "Permission denied",
    response_only=True,
    status_codes=["403"],
    value={
        "code": "PERMISSION_DENIED",
        "detail": "You do not have permission to modify this exercise.",
    },
)

NOT_FOUND_EXAMPLE = OpenApiExample(
    "Not found",
    response_only=True,
    status_codes=["404"],
    value={"code": "NOT_FOUND", "detail": "No ListeningExercise matches the given query."},
)
