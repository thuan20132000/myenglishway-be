"""Production settings.

Deliberately minimal: the deployment architecture (cache, broker, TLS
termination) is a later phase. Everything here is either a hard security
requirement or the S3 media backend.
"""

from .base import *  # noqa: F403
from .storage import s3_storages

DEBUG = False

SECRET_KEY = env("SECRET_KEY")  # noqa: F405 - must be provided, no default
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # noqa: F405

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Django 4+ rejects POSTs whose Origin is not in this list. Behind an ALB the
# public https host often differs from the internal Host header, so the API
# URL itself must be listed (scheme included). Add the frontend origin too.
_csrf_origins = env.list("CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405
_from_hosts = [f"https://{host}" for host in ALLOWED_HOSTS if host not in ("*",)]
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([*_csrf_origins, *_from_hosts]))

# User uploads (audio, PDFs). FileField stores a path; no migration.
STORAGES = s3_storages(
    bucket_name=env("AWS_STORAGE_BUCKET_NAME"),  # noqa: F405
    region_name=env("AWS_S3_REGION_NAME", default="us-east-1"),  # noqa: F405
    custom_domain=env("AWS_S3_CUSTOM_DOMAIN", default=""),  # noqa: F405
    querystring_auth=env.bool("AWS_S3_QUERYSTRING_AUTH", default=False),  # noqa: F405
)
