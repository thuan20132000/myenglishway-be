from .base import *  # noqa: F403

DEBUG = False

# Long enough for HMAC-SHA256 without triggering PyJWT key-length warnings.
SECRET_KEY = "test-secret-key-long-enough-for-hmac-sha256-signing"

# Fast, deterministic hashing in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Tests that upload audio override MEDIA_ROOT to a tmp_path.
MEDIA_ROOT = BASE_DIR / "media_test"  # noqa: F405

# Run Celery tasks inline, so tests need no broker and no worker.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False

# Never call a real transcription API from the test suite.
TRANSCRIPTION_PROVIDER = "stub"
OPENAI_API_KEY = ""

# Transcription is triggered explicitly in the tests that cover it, so uploads
# in unrelated tests do not queue work.
TRANSCRIPTION_AUTO_START = False
