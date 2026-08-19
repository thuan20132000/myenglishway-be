"""Production settings.

Deliberately minimal: the deployment architecture (storage backend, cache,
broker, TLS termination) is a later phase. Everything here is either a hard
security requirement or a placeholder marked as such.
"""

from .base import *  # noqa: F403

DEBUG = False

SECRET_KEY = env("SECRET_KEY")  # noqa: F405 - must be provided, no default
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # noqa: F405

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# EXTENSION POINT: swap DEFAULT_FILE_STORAGE / STORAGES for S3 here. No model
# migration is required because audio is a plain FileField.
