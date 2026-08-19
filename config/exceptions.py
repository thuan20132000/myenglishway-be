"""Project-wide DRF exception handler.

Two response shapes, deliberately:

1. Field validation keeps DRF's native ``{"field": ["message"]}`` body, with a
   top-level ``code`` added. Clients keep their field-to-error mapping and the
   generated schema stays honest.
2. Everything else - domain errors, auth failures, permission and lookup
   failures - returns ``{"code", "detail"}`` plus an optional ``extra``.

Successful responses are never wrapped.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_exception_handler

from common import errors

#: DRF exception class -> stable error code.
_CODE_BY_EXCEPTION = {
    exceptions.NotAuthenticated: errors.AUTHENTICATION_FAILED,
    exceptions.AuthenticationFailed: errors.AUTHENTICATION_FAILED,
    exceptions.PermissionDenied: errors.PERMISSION_DENIED,
    exceptions.NotFound: errors.NOT_FOUND,
}


#: simplejwt codes that mean "the token itself is unusable" rather than
#: "these credentials are wrong". Checked before the class mapping because
#: InvalidToken subclasses AuthenticationFailed and would match it first.
_TOKEN_CODES = {"token_not_valid", "bad_authorization_header"}


def _code_for(exc) -> str:
    detail_code = getattr(getattr(exc, "detail", None), "code", None)
    if detail_code in _TOKEN_CODES or getattr(exc, "default_code", None) in _TOKEN_CODES:
        return errors.TOKEN_INVALID

    for exc_class, code in _CODE_BY_EXCEPTION.items():
        if isinstance(exc, exc_class):
            return code
    return getattr(exc, "default_code", "ERROR")


def api_exception_handler(exc, context):
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled exception: let Django's 500 handling take over so the error
        # is logged and reported rather than silently flattened.
        return None

    if isinstance(exc, errors.DomainError):
        body = {"code": exc.detail.code, "detail": str(exc.detail)}
        if exc.extra:
            body["extra"] = exc.extra
        response.data = body
        return response

    if isinstance(exc, exceptions.ValidationError):
        response.data = _validation_body(response.data)
        return response

    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    response.data = {
        "code": _code_for(exc),
        "detail": str(detail) if detail else str(exc),
    }
    return response


def _validation_body(data):
    """Add a top-level code while preserving DRF's field-error mapping."""
    if isinstance(data, dict):
        if "code" in data:
            # A serializer field is literally named "code"; nest to avoid
            # clobbering its errors.
            return {"code": errors.VALIDATION_ERROR, "errors": data}
        return {"code": errors.VALIDATION_ERROR, **data}
    # Non-field errors raised as a bare list.
    return {"code": errors.VALIDATION_ERROR, "detail": data}
