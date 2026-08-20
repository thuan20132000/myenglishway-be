from django.conf import settings


def test_cors_uses_an_explicit_origin_allowlist():
    assert settings.CORS_ALLOWED_ORIGINS
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
    assert "corsheaders.middleware.CorsMiddleware" in settings.MIDDLEWARE
