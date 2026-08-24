import pytest
from django.http import Http404
from django.test import RequestFactory

from common.media import serve_media


def _body(response) -> bytes:
    return b"".join(response.streaming_content)


def test_media_advertises_byte_ranges(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "clip.mp3").write_bytes(b"abcdefghij")

    response = serve_media(RequestFactory().get("/media/clip.mp3"), "clip.mp3")

    assert response.status_code == 200
    assert response["Accept-Ranges"] == "bytes"
    assert _body(response) == b"abcdefghij"


def test_media_honours_a_closed_byte_range(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "clip.mp3").write_bytes(b"abcdefghij")

    request = RequestFactory().get("/media/clip.mp3", HTTP_RANGE="bytes=2-5")
    response = serve_media(request, "clip.mp3")

    assert response.status_code == 206
    assert response["Content-Range"] == "bytes 2-5/10"
    assert response["Content-Length"] == "4"
    assert _body(response) == b"cdef"


def test_media_honours_an_open_ended_range(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "clip.mp3").write_bytes(b"abcdefghij")

    request = RequestFactory().get("/media/clip.mp3", HTTP_RANGE="bytes=7-")
    response = serve_media(request, "clip.mp3")

    assert response.status_code == 206
    assert response["Content-Range"] == "bytes 7-9/10"
    assert _body(response) == b"hij"


def test_unsatisfiable_range_is_416(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "clip.mp3").write_bytes(b"abcdefghij")

    request = RequestFactory().get("/media/clip.mp3", HTTP_RANGE="bytes=99-100")
    response = serve_media(request, "clip.mp3")

    assert response.status_code == 416
    assert response["Content-Range"] == "bytes */10"


def test_missing_file_is_404(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    request = RequestFactory().get("/media/missing.mp3")
    with pytest.raises(Http404):
        serve_media(request, "missing.mp3")
