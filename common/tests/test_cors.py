from django.conf import settings


def test_cors_uses_an_explicit_origin_allowlist():
    assert settings.CORS_ALLOWED_ORIGINS
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
    assert "corsheaders.middleware.CorsMiddleware" in settings.MIDDLEWARE


def test_cors_allows_both_loopback_frontend_hosts():
    # Browsers treat these as different origins; listing only localhost
    # blocks the app when it is opened at http://127.0.0.1:3000.
    assert "http://localhost:3000" in settings.CORS_ALLOWED_ORIGINS
    assert "http://127.0.0.1:3000" in settings.CORS_ALLOWED_ORIGINS


def test_csrf_trusted_origins_include_a_scheme():
    assert settings.CSRF_TRUSTED_ORIGINS
    for origin in settings.CSRF_TRUSTED_ORIGINS:
        assert origin.startswith(("http://", "https://"))
