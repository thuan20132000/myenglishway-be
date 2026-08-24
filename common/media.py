"""Serve uploaded files with HTTP byte ranges.

Django's development ``serve`` advertises a file and ignores ``Range``.
Browsers then report the audio as unseekable, so +10s on the worksheet
rewinds to the start instead of jumping. This view is the DEBUG stand-in
for whatever CDN handles ``/media/`` in production.
"""

import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.utils._os import safe_join

BLOCK = 8192


def serve_media(request, path: str):
    fullpath = Path(safe_join(settings.MEDIA_ROOT, path))
    if not fullpath.is_file():
        raise Http404()

    file_size = fullpath.stat().st_size
    content_type, _encoding = mimetypes.guess_type(str(fullpath))
    content_type = content_type or "application/octet-stream"

    try:
        span = _byte_range(request.headers.get("Range"), file_size)
    except ValueError:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{file_size}"
        response["Accept-Ranges"] = "bytes"
        return response

    if span is None:
        response = FileResponse(fullpath.open("rb"), content_type=content_type)
        response["Accept-Ranges"] = "bytes"
        return response

    start, end = span
    length = end - start + 1
    handle = fullpath.open("rb")
    handle.seek(start)
    response = StreamingHttpResponse(
        _read_limited(handle, length),
        status=206,
        content_type=content_type,
    )
    response["Content-Length"] = str(length)
    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    response["Accept-Ranges"] = "bytes"
    return response


def _byte_range(header: str | None, file_size: int) -> tuple[int, int] | None:
    """Parse a single ``bytes=`` range, or None when the client wants the lot."""
    if not header:
        return None
    if not header.startswith("bytes="):
        raise ValueError("not a byte range")
    spec = header[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        raise ValueError("empty range")
    start_s, end_s = spec.split("-", 1)
    if start_s == "" and end_s == "":
        raise ValueError("empty range")
    if start_s == "":
        suffix = int(end_s)
        if suffix <= 0:
            raise ValueError("empty suffix")
        start = max(file_size - suffix, 0)
        end = file_size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    if start < 0 or start >= file_size or end < start:
        raise ValueError("unsatisfiable")
    return start, min(end, file_size - 1)


def _read_limited(handle, length: int):
    remaining = length
    try:
        while remaining > 0:
            chunk = handle.read(min(BLOCK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        handle.close()
