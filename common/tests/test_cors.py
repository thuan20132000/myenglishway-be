from django.conf import settings


def test_cors_uses_an_explicit_origin_allowlist():
    assert settings.CORS_ALLOWED_ORIGINS
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
    assert "corsheaders.middleware.CorsMiddleware" in settings.MIDDLEWARE


def test_csrf_trusted_origins_include_a_scheme():
    assert settings.CSRF_TRUSTED_ORIGINS
    for origin in settings.CSRF_TRUSTED_ORIGINS:
        assert origin.startswith(("http://", "https://"))
