# English Practice API

Django REST API for English practice. Two domains:

- **Listening** (`/api/v1/listening/`, `/api/v1/practice/`) — creators upload audio (and an optional PDF handout); Whisper transcribes it into timestamped segments. Learners play one segment at a time, type what they heard, and get a score.
- **Writing** (`/api/v1/writing/`) — anyone uploads a workbook PDF and keeps a private, page-by-page notebook against it. Nothing is graded; saving a page is an upsert, not a submission.

**Base URL:** `/api/v1/` · **Swagger:** `/api/docs/` · **OpenAPI:** `/api/schema/`

Client contracts: [docs/frontend-implementation.md](docs/frontend-implementation.md) (listening) · [docs/writing-frontend.md](docs/writing-frontend.md) (writing).

## Requirements

- Python 3.11+
- PostgreSQL
- Redis (Celery broker for transcription)

## Setup

```bash
make setup
make superuser
```

`make setup` creates `env/`, installs dependencies, copies `.env.example` to `.env` if needed, creates the Postgres database, and migrates. Equivalent by hand:

```bash
python3.11 -m venv env
source env/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env
createdb english_practice
python manage.py migrate
python manage.py createsuperuser
```

`make help` lists every target. Commands use `env/bin` so the venv does not have to be activated.

`.env` is django-environ. Put comments on their own lines — an inline `#` (especially one with an apostrophe) is treated as part of the value.

## Run

Three processes for local development:

```bash
# 1. Redis (Homebrew)
brew services start redis

# 2. API
make run

# 3. Worker (separate terminal)
make worker
```

The API listens on `http://127.0.0.1:8000/`. Admin: `/admin/`.

### Production hosts

Behind TLS termination (ALB / nginx), set the public names with schemes:

```
ALLOWED_HOSTS=myenglishway-api-prod.bookngon.com
CSRF_TRUSTED_ORIGINS=https://myenglishway-api-prod.bookngon.com,https://your-frontend.example.com
CORS_ALLOWED_ORIGINS=https://your-frontend.example.com
```

If `CSRF_TRUSTED_ORIGINS` is omitted, production still trusts `https://` plus each `ALLOWED_HOSTS` entry. Add the frontend origin to both `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` when it is a different host.

### Media storage

Local and tests store uploads under `media/` on disk (`DEBUG` serves them with byte-range support for seeking).

Production uses S3. Set these (IAM role preferred over keys):

```
AWS_STORAGE_BUCKET_NAME=your-bucket
AWS_S3_REGION_NAME=us-east-1
```

Optional: `AWS_S3_CUSTOM_DOMAIN` (CloudFront), `AWS_S3_QUERYSTRING_AUTH=True` for private objects with signed URLs.

To try S3 from `runserver`, set `USE_S3=True` in `.env` as well — the **Celery worker must use the same settings** so Whisper can open the file.

The bucket needs CORS for the frontend origin (`GET`, `HEAD`, header `Range`) if the player loads `audio_url` / `pdf_url` in the browser. Existing local files:

```bash
aws s3 sync media/ s3://your-bucket/
```

Database rows already store paths like `audio/1/<uuid>.mp3`; they do not need rewriting.

Uploads are namespaced by kind so storage rules can target them separately: `audio/`, `pdf/` (listening handouts) and `writing-pdf/` (writing workbooks). Size limits are independent — `MAX_PDF_FILE_SIZE_MB` (default 20) caps a handout, `MAX_WRITING_PDF_FILE_SIZE_MB` (default 60) caps a workbook. A workbook's page count is read with `pypdf` on upload and never taken from the request.

Transcription needs `OPENAI_API_KEY` and `TRANSCRIPTION_PROVIDER=openai`. Use `stub` to develop without calling OpenAI. Whisper rejects files over 25 MB (`TRANSCRIPTION_MAX_FILE_SIZE_MB`); that cap is independent of `MAX_AUDIO_FILE_SIZE_MB`.

To load a transcript by hand instead of Whisper:

```bash
python manage.py load_transcript <exercise_id> listening/fixtures/sample_transcript.json
```

## Tests

```bash
make test
make lint
```

Tests use `config.settings.test` (stub transcription, eager Celery). No Redis or API key required. `make schema` regenerates the committed OpenAPI file; `make check` runs lint and tests together.

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
| `writing/` | PDF workbooks and private per-page notebooks |
| `common/` | Shared errors, validators, text helpers, media serving |
| `config/` | Settings, URLs, Celery |
| `docs/` | Frontend implementation guides |

curl -sv http://127.0.0.1:8005/api/docs/ -H 'Host: myenglishway-api-prod.bookngon.com'
