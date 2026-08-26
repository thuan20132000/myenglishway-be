# English Practice API

Django REST API for IELTS listening dictation practice. Creators upload audio (and an optional PDF handout); Whisper transcribes it into timestamped segments. Learners play one segment at a time, type what they heard, and get a score.

**Base URL:** `/api/v1/` · **Swagger:** `/api/docs/` · **OpenAPI:** `/api/schema/`

Client contract: [docs/frontend-implementation.md](docs/frontend-implementation.md).

## Requirements

- Python 3.11+
- PostgreSQL
- Redis (Celery broker for transcription)

## Setup

```bash
python3.11 -m venv env
source env/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
createdb english_practice
python manage.py migrate
python manage.py createsuperuser
```

`.env` is django-environ. Put comments on their own lines — an inline `#` (especially one with an apostrophe) is treated as part of the value.

## Run

Three processes for local development:

```bash
# 1. Redis (Homebrew)
brew services start redis

# 2. API
source env/bin/activate
python manage.py runserver

# 3. Worker (separate terminal)
source env/bin/activate
celery -A config worker -l info
```

The API listens on `http://127.0.0.1:8000/`. Admin: `/admin/`.

Transcription needs `OPENAI_API_KEY` and `TRANSCRIPTION_PROVIDER=openai`. Use `stub` to develop without calling OpenAI. Whisper rejects files over 25 MB (`TRANSCRIPTION_MAX_FILE_SIZE_MB`); that cap is independent of `MAX_AUDIO_FILE_SIZE_MB`.

To load a transcript by hand instead of Whisper:

```bash
python manage.py load_transcript <exercise_id> listening/fixtures/sample_transcript.json
```

## Tests

```bash
source env/bin/activate
python -m pytest -q
```

Tests use `config.settings.test` (stub transcription, eager Celery). No Redis or API key required.

```bash
ruff check .
```

## Auth

JWT in `Authorization: Bearer <access>`. Register or log in with **email** (not username):

```http
POST /api/v1/auth/register/
{ "email": "you@example.com", "password": "s3cret-passphrase", "role": "creator" }
```

`role` may be `student` (default) or `creator`. Admin is not self-assignable.

Read-only `public/` routes need no token: published collections, exercises, and segment playback windows. Submit, reveal, and progress still require an account. Practice payloads never include transcript text until submit or reveal.

## Project layout

| Path | Role |
|---|---|
| `accounts/` | Users, JWT register/login |
| `listening/` | Exercises, collections, transcripts, transcription |
| `practice/` | Dictation scoring, progress, worksheets |
| `common/` | Shared errors, text helpers, media serving |
| `config/` | Settings, URLs, Celery |
| `docs/` | Frontend implementation guide |
