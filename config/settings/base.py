"""Shared settings for all environments.

Environment-specific modules (local, test, production) import * from here and
override what they need. Nothing in this module may read a secret without a
default suitable for local development.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "insecure-dev-key-change-me"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:3000"]),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
]

LOCAL_APPS = [
    "common",
    "accounts",
    "listening",
    "practice",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://localhost:5432/english_practice2",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# I18N / static / media
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --------------------------------------------------------------------------
# DRF
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "config.pagination.DefaultPageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "config.exceptions.api_exception_handler",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ("v1",),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", 60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": False,
    "UPDATE_LAST_LOGIN": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "IELTS Listening Practice API",
    "DESCRIPTION": """
Backend API for uploading listening audio, managing timestamped transcript
segments, and running dictation practice.

## Authentication

All endpoints except registration, login and token refresh require a JWT:

    Authorization: Bearer <access_token>

Obtain a token pair from `POST /api/v1/auth/register/` or
`POST /api/v1/auth/login/`, and renew the access token with
`POST /api/v1/auth/token/refresh/`.

## Roles

| Role | Can do |
|---|---|
| `student` | Practise published exercises, submit answers, view their own attempts |
| `creator` | Everything a student can, plus author, edit and publish their own exercises |
| `admin` | Full access to every exercise |

Only `student` and `creator` may be chosen at registration.

## Transcript visibility

Practice endpoints never return transcript text. A learner sees the reference
transcript only in the response to `POST /practice/segments/{id}/submit/` or
`POST /practice/segments/{id}/reveal/`. The creator-facing endpoints under
`/listening/` do return transcripts, and are restricted to the exercise owner
and admins.

## Errors

Field validation keeps DRF's native shape with a top-level code added:

    {"code": "VALIDATION_ERROR", "title": ["This field is required."]}

Everything else returns a stable machine-readable code:

    {"code": "EXERCISE_NOT_READY", "detail": "...", "extra": {"reasons": [...]}}

| Code | Status | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 400 | One or more fields failed validation |
| `AUTHENTICATION_FAILED` | 401 | Missing, invalid or expired credentials |
| `TOKEN_INVALID` | 401 | The refresh token is expired or malformed |
| `PERMISSION_DENIED` | 403 | Authenticated, but not allowed to do this |
| `NOT_FOUND` | 404 | No such resource, or none the caller may see |
| `INVALID_AUDIO_FILE` | 400 | Unsupported audio format or empty file |
| `AUDIO_FILE_TOO_LARGE` | 400 | Audio exceeds the configured size limit |
| `INVALID_SEGMENT_RANGE` | 400 | `end_time` <= `start_time`, or negative `start_time` |
| `SEGMENT_OUTSIDE_AUDIO` | 400 | Segment extends past the known audio duration |
| `DUPLICATE_SEGMENT_SEQUENCE` | 400 | Another segment already uses that sequence |
| `EXERCISE_NOT_READY` | 409 | Publish preconditions unmet; see `extra.reasons` |
| `EXERCISE_ALREADY_PUBLISHED` | 409 | Publish called on a published exercise |
| `EXERCISE_NOT_PUBLISHED` | 409 | Practice attempted on an unpublished exercise |
| `EXERCISE_PROCESSING` | 409 | Transcription is in flight; segments are locked |
| `TRANSCRIPT_NOT_AVAILABLE` | 409 | The segment has no transcript to score against |
| `COLLECTION_DEPTH_EXCEEDED` | 400 | Collections nest two levels deep at most |
| `COLLECTION_CYCLE` | 400 | The requested parent would place a collection inside itself |
| `COLLECTION_OWNER_MISMATCH` | 400 | The parent or exercise belongs to another user |
| `COLLECTION_HAS_CHILDREN` | 409 | A collection of collections cannot also hold exercises |
| `COLLECTION_HAS_MEMBERS` | 409 | A collection of exercises cannot also hold collections |
| `EXERCISE_ALREADY_IN_COLLECTION` | 409 | That exercise is already filed elsewhere |
| `COLLECTION_ALREADY_PUBLISHED` | 409 | Publish called on a published collection |

Requesting a resource the caller may not see returns **404, not 403** - a 403
would confirm that it exists.
""",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SORT_OPERATIONS": True,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "common.openapi.add_unauthenticated_response",
    ],
    "TAGS": [
        {"name": "auth", "description": "Registration, login and token renewal."},
        {
            "name": "listening",
            "description": (
                "Creator-facing exercise and transcript management. Payloads here "
                "contain transcript text and are restricted to the owner and admins."
            ),
        },
        {
            "name": "practice",
            "description": (
                "Learner-facing dictation. Segment representations never include "
                "transcript text; it is released on submit or reveal."
            ),
        },
    ],
    "ENUM_NAME_OVERRIDES": {
        "ExerciseStatusEnum": "listening.models.ExerciseStatus.choices",
        "RoleEnum": "accounts.models.Role.choices",
    },
    "SWAGGER_UI_SETTINGS": {
        "deepLinking": True,
        "persistAuthorization": True,
        "displayOperationId": False,
    },
}

# --------------------------------------------------------------------------
# Domain configuration knobs
# --------------------------------------------------------------------------

MAX_AUDIO_FILE_SIZE_MB = env.int("MAX_AUDIO_FILE_SIZE_MB", 50)
ALLOWED_AUDIO_EXTENSIONS = [".mp3", ".m4a", ".wav", ".ogg"]
ALLOWED_AUDIO_CONTENT_TYPES = [
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/ogg",
    "application/ogg",
]
MAX_PDF_FILE_SIZE_MB = env.int("MAX_PDF_FILE_SIZE_MB", 20)
ALLOWED_PDF_EXTENSIONS = [".pdf"]
ALLOWED_PDF_CONTENT_TYPES = ["application/pdf", "application/x-pdf"]

SUPPORTED_LANGUAGES = ["en"]

# A segment counts as completed when its best submission score reaches this.
PRACTICE_COMPLETION_THRESHOLD = env.float("PRACTICE_COMPLETION_THRESHOLD", 80.0)
PRACTICE_MAX_ANSWER_LENGTH = env.int("PRACTICE_MAX_ANSWER_LENGTH", 5000)

# Tolerance (seconds) when checking a segment end_time against audio duration.
SEGMENT_DURATION_TOLERANCE = 0.5

# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------

#: "openai" for real Whisper transcription, "stub" for offline development.
TRANSCRIPTION_PROVIDER = env("TRANSCRIPTION_PROVIDER", default="openai")
OPENAI_API_KEY = env("OPENAI_API_KEY", default="")

#: Transcribe automatically when audio is uploaded or replaced.
TRANSCRIPTION_AUTO_START = env.bool("TRANSCRIPTION_AUTO_START", default=True)

#: OpenAI caps Whisper uploads at 25 MB. This is deliberately separate from
#: MAX_AUDIO_FILE_SIZE_MB: a creator may upload larger audio and write the
#: transcript by hand, but automatic transcription will refuse it.
TRANSCRIPTION_MAX_FILE_SIZE_MB = env.int("TRANSCRIPTION_MAX_FILE_SIZE_MB", 25)
TRANSCRIPTION_TIMEOUT = env.float("TRANSCRIPTION_TIMEOUT", 600.0)
TRANSCRIPTION_MAX_RETRIES = env.int("TRANSCRIPTION_MAX_RETRIES", 3)
TRANSCRIPTION_RETRY_DELAY = env.int("TRANSCRIPTION_RETRY_DELAY", 30)

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
#: Hard ceiling per task; transcription of long audio is slow but not endless.
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", 1800)
CELERY_TASK_SOFT_TIME_LIMIT = env.int("CELERY_TASK_SOFT_TIME_LIMIT", 1500)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
