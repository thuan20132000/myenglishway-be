# Frontend Implementation Guide

How to build a client against this API. Every shape below was read out of the
committed `backend/schema.yml`, not written from memory — but the schema is the
authority, and `/api/docs/` renders it interactively.

**Base URL:** `/api/v1/` · **Schema:** `/api/schema/` · **Swagger:** `/api/docs/`

This guide covers **listening practice** (`/listening/`, `/practice/`). Writing
practice is a separate domain with its own contract:
[writing-frontend.md](writing-frontend.md). Auth (§3), the error envelope (§8)
and the 404-not-403 rule are shared; the rest is not.

---

## 1. What the product does

A creator uploads audio. The backend transcribes it into timestamped segments.
A learner plays one segment at a time, types what they heard, and gets a score
with word-level corrections. Progress is tracked per segment.

```
Creator                              Learner
───────                              ───────
upload audio                         browse published exercises
   ↓ (background transcription)         ↓
review / correct segments            start → resume point
   ↓                                    ↓
publish  ─────────────────────────►  play segment → type → submit
                                        ↓
                                     score + corrections
                                        ↓
                                     progress / history / leaderboard
```

---

## 2. The one rule that matters

**Practice endpoints never return transcript text.** A segment fetched through
`/practice/` has exactly these fields:

```json
{ "id": 501, "sequence": 8, "start_time": 32.5, "end_time": 38.9, "word_count": 11 }
```

The learner earns the transcript in exactly two places:

- the response to `POST /practice/segments/{id}/submit/` → `correct_answer`
- the response to `POST /practice/segments/{id}/reveal/` → `text`

Do not cache transcripts from those responses into a store that the "not yet
answered" UI reads from. The backend guarantees it will never hand you the
answer early; keeping that guarantee on the client is your job. Practically:
hold the revealed text in the component that displays the result, keyed by
attempt, not in a global `segmentsById` cache.

`word_count` is given so you can show "11 words" or render blanks without
knowing the words.

---

## 3. Authentication

JWT bearer tokens. The four `POST` endpoints below and the read-only `public/`
routes (§5) need no header; everything else does.

```
Authorization: Bearer <access_token>
```

| Endpoint | Body | Returns |
|---|---|---|
| `POST /auth/register/` | `{email, password, full_name?, role?}` | `{user, access, refresh}` |
| `POST /auth/login/` | `{email, password}` | `{user, access, refresh}` |
| `POST /auth/google/` | `{id_token, role?}` | `{user, access, refresh, created}` |
| `POST /auth/token/refresh/` | `{refresh}` | `{access}` |
| `GET /auth/me/` | — | `User` |

`role` at registration is `"student"` (default) or `"creator"`. `"admin"` is
rejected — don't offer it in the signup UI.

**Google sign-in.** `id_token` is the credential Google Identity Services hands
you in the browser, issued for the same OAuth client ID the server is configured
with (`GOOGLE_OAUTH_CLIENT_ID`). One endpoint covers signup and login: `created`
is `true` when the call made the account, so you can route first-time users into
onboarding. An existing account is matched by Google's stable subject id, and on
the very first Google sign-in by the token's verified email — so a user who
registered with a password and later clicks "Continue with Google" lands in the
same account, keeping their password. `role` is applied only when the account is
created, and is ignored otherwise. A rejected token (expired, wrong client,
unverified email) returns `401`; treat it like a failed login.

**Token lifetimes:** access 60 min, refresh 7 days (both configurable server
side — don't hardcode; decode the JWT `exp` or refresh reactively).

**Recommended handling.** Register a response interceptor: on `401`, attempt a
single refresh, replay the original request, and on a second failure clear
tokens and route to login. Queue concurrent requests during the refresh so a
page issuing four calls doesn't fire four refreshes.

Refresh tokens are not rotated, so the same refresh token is reusable until it
expires.

**Storage.** There is no cookie/CSRF flow — tokens are yours to hold. In-memory
access token with the refresh token in `localStorage` is the usual compromise;
if the app has any XSS surface, prefer keeping both in memory and re-logging in
on reload.

---

## 4. Roles

| | anonymous | student | creator | admin |
|---|---|---|---|---|
| Browse published collections & exercises | ✅ | ✅ | ✅ | ✅ |
| Play audio, read segment timings | ✅ | ✅ | ✅ | ✅ |
| Submit answers / reveal / worksheet key | ❌ | ✅ | ✅ | ✅ |
| See own attempts / progress / history | ❌ | ✅ | ✅ | ✅ |
| See the practice leaderboard | ❌ | ✅ | ✅ | ✅ |
| Create & edit own exercises | ❌ | ❌ | ✅ | ✅ |
| See transcripts (`/listening/` routes) | ❌ | ❌ | own only | all |
| Publish / unpublish | ❌ | ❌ | own only | all |
| See any exercise | published only | published only | published + own | all |

`GET /auth/me/` returns `role`. Gate navigation on it, but treat the server as
the authority — a student hitting a creator route gets 403 or 404 regardless.

Anonymous is not a role, it is the absence of a token: it selects the `public/`
routes described in §5, not a different view of the authenticated ones.

---

## 5. Anonymous browsing (no account)

Everything a visitor needs to find material and start a dictation drill is
available without a token, under `public/`. Send **no** `Authorization` header
to these — they ignore it — and route the app's landing page at them so the
catalogue renders before login.

| Endpoint | Returns |
|---|---|
| `GET /listening/public/collections/` | Published **root** collections, paginated |
| `GET /listening/public/collections/{id}/` | One collection with its published `children` and `members` |
| `GET /listening/public/exercises/` | Published exercises, paginated |
| `GET /listening/public/exercises/{id}/` | One published exercise |
| `GET /practice/public/exercises/{id}/segments/` | Segment playback windows, bare array |

The two collection and two exercise routes return **exactly what a signed-in
student receives** from their authenticated twins (§7.1) — same serializers,
same fields, same pagination envelope. So the browse screen can be written once
and pointed at either surface depending on whether a token is held:

```ts
const base = token ? "/listening" : "/listening/public";
const { data } = await api.get(`${base}/exercises/`, { params });
```

The segment route is the one exception: the authenticated version (§7.3) adds
`attempted` and `best_score` per row, which need an account to compute. The
public one returns the same rows without those two keys.

### 5.1 What is and isn't visible

**Published rows only.** An unpublished collection or exercise returns **404**,
never 403 — the API refuses to confirm that someone's draft exists. Don't write
a "this is private, log in to see it" branch off a 404; there is nothing to log
in for.

Filtering runs all the way down: opening a published book returns only its
published tests, and only its published exercises. A published exercise filed
inside an unpublished collection has `collection: null` in its breadcrumb
rather than leaking the book's title — so render the breadcrumb defensively,
`collection` and `collection.parent` are independently nullable.

**No transcript, ever.** The rule in §2 holds here with no escape hatch: the
public exercise detail has no `segments` key, and the public segment list has no
`text` field. There is no anonymous equivalent of `submit/` or `reveal/`.

### 5.2 The shape of the catalogue

Collections nest exactly two deep: a **book** holds **tests**, a test holds
**exercises**. Three requests take a visitor from the landing page to a
playable segment. The payloads below are real responses, trimmed only of
timestamps.

**1. Landing page** — `GET /listening/public/collections/`

```json
{ "count": 1, "next": null, "previous": null,
  "results": [
    { "id": 1, "title": "Cambridge IELTS 18",
      "owner": {"id": 1, "full_name": "Ben Ito"},
      "is_published": true, "position": 1,
      "child_count": 1, "member_count": 0, "created_at": "..." }
  ] }
```

**2. Open the book** — `GET /listening/public/collections/1/`

```json
{ "id": 1, "title": "Cambridge IELTS 18", "description": "",
  "owner": {"id": 1, "full_name": "Ben Ito"},
  "parent": null, "is_published": true, "published_at": "...",
  "position": 1, "child_count": 1, "member_count": 0,
  "children": [
    { "id": 2, "title": "Test 1", "owner": {"id": 1, "full_name": "Ben Ito"},
      "is_published": true, "position": 1,
      "child_count": 0, "member_count": 4, "created_at": "..." }
  ],
  "members": [],
  "created_at": "...", "updated_at": "..." }
```

**3. Open the test** — `GET /listening/public/collections/2/`. Same shape, but
now `children` is empty and `members` holds exercise rows identical to those
from the exercise list, each with its own breadcrumb:

```json
{ "id": 2, "title": "Test 1",
  "parent": {"id": 1, "title": "Cambridge IELTS 18", "parent": null},
  "child_count": 0, "member_count": 4,
  "children": [],
  "members": [
    { "id": 1, "title": "Part 1: Accommodation",
      "owner": {"id": 1, "full_name": "Ben Ito"},
      "status": "ready", "is_published": true, "duration": 184.2,
      "language": "en", "has_pdf": true, "segment_count": 24,
      "collection": {"id": 2, "title": "Test 1",
                     "parent": {"id": 1, "title": "Cambridge IELTS 18"}},
      "created_at": "..." }
  ] }
```

**4. Open the exercise** — `GET /listening/public/exercises/1/`

```json
{ "id": 1, "title": "Part 1: Accommodation",
  "description": "IELTS Section 1 practice",
  "owner": {"id": 1, "full_name": "Ben Ito"},
  "status": "ready", "is_published": true, "published_at": "...",
  "duration": 184.2, "language": "en",
  "audio_url": "http://localhost:8000/media/audio/1/0d1e116c.mp3",
  "pdf_url": "http://localhost:8000/media/pdf/1/6f06b2c0.pdf",
  "segment_count": 24, "answer_count": 10,
  "collection": {"id": 2, "title": "Test 1",
                 "parent": {"id": 1, "title": "Cambridge IELTS 18"}},
  "created_at": "...", "updated_at": "..." }
```

Note there is **no** `segments` key — that is the §2 rule, not an omission.

**5. Load the playback windows** — `GET /practice/public/exercises/1/segments/`.
A **bare array**, not paginated, ordered by `sequence`:

```json
[ { "id": 1, "sequence": 1, "start_time": 0.0,  "end_time": 5.0,  "word_count": 7 },
  { "id": 2, "sequence": 2, "start_time": 6.0,  "end_time": 11.0, "word_count": 9 } ]
```

That is everything the player needs: `audio_url` from step 4, and a
`[start_time, end_time)` window per segment to seek and loop.

Two nullability traps in the breadcrumb: `collection` is `null` for an
ungrouped exercise **and** for one whose collection the visitor may not see,
and `collection.parent` is `null` both for a test directly under no book and
for a book the visitor may not see. It never nests further than
`collection.parent`, so two optional-chained levels cover every case.

### 5.3 Filters and ordering

`/listening/public/exercises/` accepts the same query parameters as the
authenticated list: `search`, `language`, `collection`, `ungrouped`,
`ordering`, `page`, `page_size`. `status` and `is_published` are accepted but
pointless here: the surface is published-only, and a database constraint makes
"published but still processing" impossible, so every row is `ready`.

Two that matter for a catalogue landing page:

```
GET /listening/public/exercises/?collection=3&ordering=position
GET /listening/public/exercises/?ungrouped=true
```

`position` is the membership order inside a collection — "Part 1, Part 2,
Part 3, Part 4" of a test. It is `null` for an ungrouped exercise, so only ask
for it alongside `?collection=`. `ungrouped=true` gives the loose exercises to
show beside the collection cards.

`/listening/public/collections/` lists **roots only** — a child collection is
reached by opening its parent, never as a top-level card. A row tells you which
it is:

```json
{ "id": 3, "title": "Cambridge IELTS 18",
  "owner": {"id": 3, "full_name": "Ben Ito"},
  "is_published": true, "position": 1,
  "child_count": 4, "member_count": 0, "created_at": "..." }
```

`child_count > 0` → the detail response fills `children` (drill down again).
`member_count > 0` → it fills `members` with exercise rows (this is the leaf).
A collection never has both, so branch on whichever is non-zero.

### 5.4 How far an anonymous visitor can go

They can browse, open an exercise, load its audio, open the question PDF, and
fetch the segment timings — enough to play a segment and type an answer
locally. They **cannot** be scored: `submit/`, `reveal/`, `start/`, `progress/`,
`history/`, `leaderboard/` and the worksheet answer key all require a token,
because all of them read or write attempts belonging to a user.

That gives you a natural signup wall. Recommended shape:

1. Render the catalogue and the exercise page anonymously.
2. Let them play audio and type into the answer box.
3. On **submit**, if there is no token, keep the typed answer in memory, prompt
   for login/registration, and replay `POST /practice/segments/{id}/submit/`
   once a token arrives.

Don't fake a score client-side to fill the gap — you don't have the transcript,
and normalisation (§7.4) is deliberately server-side.

### 5.5 Migrating the session on login

The public and authenticated payloads carry the same `id`s, so nothing needs
remapping. After login, re-fetch through the authenticated routes to pick up
what the anonymous surface cannot know:

- `GET /practice/exercises/{id}/segments/` adds `attempted` and `best_score`
  per segment.
- `POST /practice/exercises/{id}/start/` gives the resume point.

Treat anything the visitor typed before signing in as unsaved: it exists only
in your store until a `submit/` succeeds.

---

## 6. Creator flow

### 6.1 Upload

`POST /listening/exercises/` — **`multipart/form-data`**, not JSON.

```
title=Accommodation Practice     (required)
description=IELTS Section 1      (optional)
language=en                      (optional, default "en")
audio_file=<File>                (optional; .mp3 .m4a .wav .ogg)
pdf_file=<File>                  (optional; .pdf, max 20 MB)
duration=184.2                   (optional; set automatically by transcription)
```

`pdf_file` is the question sheet the learner reads while listening. It is
optional and purely presentational — it does not affect `status` and is not a
publish precondition. To swap it later, `PATCH` a new `pdf_file`; to detach it,
`PATCH remove_pdf=true` (a flag rather than `pdf_file=null`, because multipart
has no null). Unlike the audio, **the PDF may be replaced while the exercise is
published** — the segment timings are untouched.

Returns `201` with the detail shape. `status` will be `"uploaded"` when audio
was included, `"draft"` when it wasn't.

**Size limits.** The upload cap is `MAX_AUDIO_FILE_SIZE_MB` (default 50), but
automatic transcription refuses anything over 25 MB. Validate client-side
against 25 MB if the server has auto-transcription on, otherwise the upload
succeeds and fails minutes later. Ask your backend which limits are configured.

### 6.2 Wait for transcription

Uploading queues a background job. Poll:

`GET /listening/exercises/{id}/status/` (owner/admin only)

```json
{
  "id": 125,
  "status": "processing",
  "is_published": false,
  "duration": null,
  "segment_count": 0,
  "processing_started_at": "2026-08-19T10:00:00Z",
  "processing_error": "",
  "transcription_provider": ""
}
```

Status machine:

```
draft ──(audio added)──► uploaded ──► processing ──► ready
                                          │
                                          └────────► failed   (processing_error explains why)
```

- **`processing`** — keep polling. Every 3–5 s is plenty; transcription of a
  10-minute file takes tens of seconds. Back off after a minute or two.
- **`ready`** — `segment_count > 0`, `duration` populated. Stop polling.
- **`failed`** — show `processing_error` verbatim; it is written for the
  creator (e.g. *"Audio is 41.2 MB, above the 25 MB limit for automatic
  transcription."*). Offer a **Retry** button → `POST /listening/exercises/{id}/transcribe/`.

There is no WebSocket push. Polling is the intended mechanism.

### 6.3 Review and correct segments

`GET /listening/exercises/{id}/segments/` — **not paginated**, returns a bare
array. Includes `text`.

```json
[{ "id": 501, "sequence": 1, "start_time": 0.5, "end_time": 5.8,
   "text": "Good morning, how can I help you?", "word_count": 7,
   "created_at": "...", "updated_at": "..." }]
```

Editing:

| Action | Call |
|---|---|
| Fix text / timings | `PATCH /listening/segments/{id}/` |
| Add a segment | `POST /listening/exercises/{id}/segments/` (omit `sequence` to append) |
| Delete | `DELETE /listening/segments/{id}/` |
| Reorder | `POST /listening/exercises/{id}/segments/reorder/` |

Reorder takes **every** segment exactly once — it is a whole-list operation,
not a delta:

```json
{ "ordering": [ {"id": 502, "sequence": 1}, {"id": 501, "sequence": 2} ] }
```

A partial list is a 400. Build the payload from your full local list after a
drag-and-drop.

Two behaviours to surface in the UI:
- **Deleting the last segment unpublishes the exercise** and returns it to
  `uploaded`. Warn before doing it.
- **Correcting text does not change past scores** — learners' attempts keep a
  snapshot of the transcript as it read when they answered.

### 6.4 The answer key

`GET` / `PUT /listening/exercises/{id}/answers/` — **owner or admin only, for
reads as well as writes.** This is the solution to the question sheet; learners
read the same answers through the practice API instead (§7.8).

```json
PUT { "answer_key": "11. library\n12. 9.30\n13. blue" }
→   { "answer_key": "11. library\n12. 9.30\n13. blue",
      "answers": [ {"number": 11, "text": "library"}, … ] }
```

The key is pasted the way it is printed, one answer per line. Rules:

- **Number every line or none of them.** An unnumbered list is numbered from 1
  by position. A half-numbered list is rejected with `INVALID_ANSWER_KEY` and
  the offending line in `extra.line` — it is a typo, not a request to renumber.
- Separators `.` `)` `-` `:` all work, and **a space after the separator is
  required**. That is what stops the unnumbered answer `9.30` (an ordinary
  time) from parsing as question 9 answered "30".
- **Numbers need not start at 1 or be contiguous** — a Section 2 sheet is
  questions 11–20.
- `PUT` replaces the **whole** key. An empty string clears it.
- Permitted while published: no segment timing depends on the key.

The key is optional. `answer_count` on the exercise detail says how many
answers exist.

### 6.5 Publish

`POST /listening/exercises/{id}/publish/` → `200` with
`{id, status, is_published, published_at}`.

Failure is `409` with every unmet precondition at once:

```json
{ "code": "EXERCISE_NOT_READY",
  "detail": "This exercise does not meet the requirements for publishing.",
  "extra": { "reasons": ["missing_duration", "no_segments"] } }
```

Map `extra.reasons` to a checklist rather than showing the raw strings:

| reason | Show |
|---|---|
| `missing_audio` | Upload an audio file |
| `missing_duration` | Audio length unknown — re-run transcription or set it manually |
| `no_segments` | Add at least one transcript segment |
| `segments_exceed_duration` | Some segments end after the audio does |
| `status_is_*` | Transcription hasn't finished (or failed) |

`POST .../unpublish/` is idempotent and always `200`.

Note `is_published` is **read-only** on PATCH — publishing is only ever these
two actions.

---

## 7. Learner flow

There are two ways to practise the same exercise. **Worksheet mode** (§7.8) is
the paper workflow: question sheet open, audio playing straight through, fill in
an answer column, then read the key. **Dictation mode** (§7.2–7.6) is the
segment-by-segment transcription drill with server-side scoring. They share
nothing but the exercise.

### 7.1 Browse

`GET /listening/exercises/` — **paginated** (`{count, next, previous, results}`),
20 per page, `page_size` up to 100.

For a signed-out visitor this is `GET /listening/public/exercises/` instead
(§5); the rows are identical, so build the screen once and switch the base path.

Filters: `status`, `is_published`, `owner`, `language`, `search` (title +
description), `ordering` (`created_at`, `-created_at`, `title`, `duration`),
`page`, `page_size`.

List rows carry **no** `description` and **no** `segments` — they are for cards:

```json
{ "id": 125, "title": "Accommodation Practice",
  "owner": {"id": 3, "full_name": "Ben Ito"},
  "status": "ready", "is_published": true, "duration": 184.2,
  "language": "en", "has_pdf": true, "segment_count": 24,
  "collection": {"id": 7, "title": "Test 1", "parent": {"id": 3, "title": "Cambridge IELTS 18"}},
  "created_at": "..." }
```

`audio_url` and `pdf_url` are **not** on list rows — fetch the detail route (or
`start/`) for those. `has_pdf` is there so a card can show a handout badge
without one request per row.

### 7.2 Start / resume

`POST /practice/exercises/{id}/start/` (POST, but it writes nothing — no
session is created).

```json
{ "exercise_id": 125, "title": "Accommodation Practice",
  "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
  "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
  "total_segments": 24, "attempted_segments": 8,
  "current_segment": { "id": 501, "sequence": 9, "start_time": 32.5,
                       "end_time": 38.9, "word_count": 11 } }
```

- `current_segment` is the **lowest-sequence segment with no submission** —
  not the furthest reached. A learner who skipped segment 1 is sent back to it.
- `current_segment` is `null` when every segment has been attempted → show a
  completion screen.
- **A revealed segment still counts as unanswered.** Revealing doesn't advance
  the resume point.

### 7.3 The practice loop

`GET /practice/exercises/{id}/segments/` — **not paginated**, bare array, with
per-learner progress:

```json
[{ "id": 501, "sequence": 1, "start_time": 0.5, "end_time": 5.8,
   "word_count": 7, "attempted": true, "best_score": 91.0 }]
```

Use it for a segment rail / progress strip. `best_score` is `null` until the
segment has a submission.

**Audio playback.** One file, many segments. Load `audio_url` once and seek:

```js
function playSegment(audio, segment) {
  audio.currentTime = segment.start_time;
  audio.play();
  const stop = () => {
    if (audio.currentTime >= segment.end_time) {
      audio.pause();
      audio.removeEventListener('timeupdate', stop);
    }
  };
  audio.addEventListener('timeupdate', stop);
}
```

`timeupdate` fires roughly every 250 ms, so playback overshoots the end by up
to a quarter second. If that's too loose, drive it with
`requestAnimationFrame` instead. Dictation UIs normally offer replay,
half-speed (`audio.playbackRate = 0.75`) and a repeat count — all client-side,
no API involvement.

**The handout pane.** When `pdf_url` is non-null, the practice screen is a
two-pane layout: the question sheet beside the player and answer box, the way a
learner works from a printed paper. Rules the layout has to respect:

- The PDF pane **scrolls and zooms independently of playback**. Nothing about
  the audio may move it, and paging through the document must never pause or
  seek the audio. There is no page↔segment mapping in the API — do not invent
  one.
- Audio controls must **never wait on the PDF**. Render the player immediately
  and let the document stream in beside it; a slow or failed PDF leaves a fully
  working dictation exercise.
- When `pdf_url` is `null`, drop the pane entirely and give the player the full
  width — most exercises will have no handout.
- Render with `react-pdf` / `pdfjs-dist`, and fall back to
  `<a href={pdf_url} target="_blank" rel="noopener">Open the question sheet</a>`
  when the viewer fails. On narrow screens, prefer a tab toggle between sheet
  and player over a cramped split.
- `pdf_url`, like `audio_url`, is a **plain unauthenticated `/media/` URL** — no
  `Authorization` header is applied, so don't fetch it through your API client.

**Submit.**

`POST /practice/segments/{id}/submit/` with `{"answer": "..."}` → `201`:

```json
{
  "attempt_id": 991,
  "segment_id": 501,
  "score": 85.7,
  "user_answer": "I would like accommodation near university",
  "correct_answer": "I would like accommodation near the university.",
  "result": {
    "correct": ["i","would","like","accommodation","near","university"],
    "missing": [{"expected": "the", "position": 5}],
    "extra": [],
    "incorrect": [],
    "counts": {"correct": 6, "missing": 1, "extra": 0, "incorrect": 0, "expected_total": 7}
  },
  "created_at": "..."
}
```

An empty string is a **valid** answer (scores 0). `null` is a 400. Repeat
submissions are allowed and each creates a new attempt — offer "try again"
freely; a learner's average uses their *best* score per segment, so retrying
can never hurt them.

### 7.4 Rendering the diff — read this carefully

Two things trip people up:

**1. Diff tokens are normalized; `correct_answer` is not.** The scorer works on
lowercased, punctuation-stripped tokens, so `result.correct` contains
`"university"` while `correct_answer` reads `"...the university."` Don't
string-match the arrays against the reference text. Render from the token
arrays, or render `correct_answer` and use `position` to locate words.

**2. `position` indexes different sequences depending on the bucket.**

| Bucket | `position` indexes | Render against |
|---|---|---|
| `missing` | the **expected** token list | the reference answer |
| `incorrect` | the **expected** token list | the reference answer |
| `extra` | the **submitted** token list | what the learner typed |

That is deliberate: you highlight omissions in the reference, and additions in
the learner's own text.

A workable rendering: tokenize `correct_answer` yourself the same way (lower,
strip edge punctuation), walk `expected_total` positions, and mark each index
as correct / missing / incorrect from the arrays. Show `incorrect` entries as
`expected` vs `received` side by side — that pairing is the most useful
feedback in the payload.

**Scoring semantics worth telling the user:**

- Case, punctuation and spacing never matter.
- Spelling errors are **not** forgiven — `acommodation` is an `incorrect` pair.
- Contractions are not expanded: `"I'd"` ≠ `"I would"`.
- Score is `correct / max(expected_words, submitted_words)`, so padding an
  answer with extra words lowers the score rather than being free.

### 7.5 Reveal

`POST /practice/segments/{id}/reveal/` → `200`:

```json
{ "attempt_id": 992, "segment_id": 501,
  "text": "I would like accommodation near the university.",
  "revealed": true, "created_at": "..." }
```

Put this behind a confirm — it is the "give up" action. It is recorded (visible
in history as `kind: "reveal"`), does **not** count as attempted or completed,
and does **not** affect `average_score`.

### 7.6 Progress and history

`GET /practice/exercises/{id}/progress/`:

```json
{ "exercise_id": 125, "total_segments": 24,
  "attempted_segments": 10, "completed_segments": 8, "revealed_segments": 2,
  "progress_percentage": 33.3, "average_score": 86.4,
  "last_segment_sequence": 9, "last_practiced_at": "..." }
```

Definitions the UI should reflect honestly:

- **attempted** — segments with ≥1 submission
- **completed** — segments whose **best** score reached the threshold (default 80)
- **progress_percentage** — `completed / total`, *not* attempted / total
- **average_score** — mean of each segment's **best** score
- `average_score` and `last_segment_sequence` are `null` for a fresh learner

`GET /practice/history/` — **paginated**, one row per exercise, most recently
practised first. Filters: `exercise`, `date_from`, `date_to` (both `YYYY-MM-DD`).

```json
{ "exercise": {"id": 125, "title": "...", "total_segments": 24},
  "attempted_segments": 20, "completed_segments": 17, "total_attempts": 34,
  "average_score": 87.5, "last_practiced_at": "..." }
```

Exercises stay in history after being unpublished, so a row may reference an
exercise the learner can no longer open — handle that link gracefully.

`GET /practice/attempts/` — **paginated**, individual attempts, newest first.
Filters: `exercise`, `segment`, `kind` (`submission`|`reveal`), `date_from`,
`date_to`. Always the caller's own; another learner's attempts are not
addressable.

### 7.7 Leaderboard

`GET /practice/leaderboard/` — **paginated**, one row per learner who has at
least one scored dictation submission. **Requires a token.** There is no
`public/` twin: gate the screen on login, and treat `401` like history.

Ranks by **`practice_count`**: how many times the learner submitted an answer.
Retries count; reveals do not. Coverage (`attempted_segments`) and quality
(`average_score`) are extra columns, not the sort key.

```
GET /practice/leaderboard/?date_from=2026-09-01&date_to=2026-09-04&page=1&page_size=20
Authorization: Bearer <access_token>
```

```json
{
  "count": 42,
  "next": "http://localhost:8000/api/v1/practice/leaderboard/?page=2",
  "previous": null,
  "results": [
    {
      "rank": 1,
      "user": { "id": 12, "full_name": "Ada Nguyen" },
      "practice_count": 340,
      "attempted_segments": 86,
      "completed_segments": 71,
      "average_score": 88.2,
      "last_practiced_at": "2026-09-04T12:01:00Z"
    }
  ],
  "me": {
    "rank": 7,
    "user": { "id": 3, "full_name": "Lin Tran" },
    "practice_count": 120,
    "attempted_segments": 40,
    "completed_segments": 31,
    "average_score": 81.0,
    "last_practiced_at": "2026-09-04T09:10:00Z"
  }
}
```

Filters and pagination:

| Param | Meaning |
|---|---|
| `date_from`, `date_to` | Inclusive `YYYY-MM-DD`, **UTC calendar date** of `created_at`. Omit both for all-time. |
| `page` | 1-based. Default `1`. |
| `page_size` | Default `20`, max `100`. |

`date_from` after `date_to` is `400 VALIDATION_ERROR` with a `date_from` field
error — same envelope as history. For "This week" / "This month" chips, compute
the two dates in UTC on the client; the API has no `period=` shortcut.

**Render `rank` as given.** Equal `practice_count` shares a rank (`1, 2, 2, 4`).
Do not replace it with the row index. Order of `results` is already
highest-count first, with `user.id` as a stable tie-break.

**`me` is a sticky row, not a second list.** It is the caller's standing even
when they are off the current page. `null` when they have zero submissions in
the window (the board can still have other people). Recommended layout:

1. Table from `results`. Highlight the row whose `user.id === me.user.id`
   when they are on this page; do not paint them twice.
2. A pinned bar from `me` ("You · #7 · 120 submissions") so they still see
   their place on page 1 of a long board.
3. Empty `results` and `me === null` → "No one has practised yet."
4. Non-empty `results` and `me === null` → show the board, plus "Submit an
   answer to join."

**`user` is `id` + `full_name` only.** Email is never in this payload — do not
reach for `User` from `/auth/me/` to fill other people's names. `full_name` may
be `""`; show a fallback such as "Learner", not a blank cell. Compare identity
with `me.user.id` or `/auth/me/`'s `id`, never with email.

Column meanings (same definitions as §7.6):

| Field | Show as | Notes |
|---|---|---|
| `rank` | `#1` | Shared on ties |
| `user.full_name` | Display name | Fallback when blank |
| `practice_count` | Submissions | The ranking metric; retries raise it |
| `attempted_segments` | Segments tried | Distinct segments with ≥1 submission |
| `completed_segments` | Completed | Best score ≥ threshold (default 80) |
| `average_score` | Average | Mean of each segment's **best** score; one decimal |
| `last_practiced_at` | Last practice | ISO-8601 UTC |

Retrying one segment raises `practice_count` by 1 and can only raise
`average_score`; `attempted_segments` stays the same. Writing notebooks are
not on this board.

Suggested types:

```ts
type LeaderboardUser = { id: number; full_name: string };

type LeaderboardEntry = {
  rank: number;
  user: LeaderboardUser;
  practice_count: number;
  attempted_segments: number;
  completed_segments: number;
  average_score: number | null;
  last_practiced_at: string;
};

type LeaderboardPage = {
  count: number;
  next: string | null;
  previous: string | null;
  results: LeaderboardEntry[];
  me: LeaderboardEntry | null;
};
```

---

### 7.8 Worksheet mode

Three GETs, none of which record anything. All are gated by the same rule as the
rest of practice: published, `ready`, or you own it.

**`GET /practice/exercises/{id}/worksheet/`** — everything the page needs:

```json
{ "exercise_id": 125, "title": "Section 2 — Museum tour",
  "audio_url": "http://localhost:8000/media/audio/3/9f2c.mp3",
  "pdf_url": "http://localhost:8000/media/pdf/3/4b71.pdf",
  "duration": 184.2,
  "question_numbers": [11,12,13,14,15,16,17,18,19,20],
  "has_transcript": true }
```

`question_numbers` lists the numbers **as printed**, not a count — render one
input per entry, labelled with that number. Empty when the creator wrote no
key; the page is still usable as question sheet plus player.

**`GET /practice/exercises/{id}/answers/`** →
`[{"number": 11, "text": "library"}, …]`, or `[]` when there is no key.

A separate request so the answers are not sitting in the page payload while the
learner is still working. Fetch it when they ask to see the key. It is **not**
gated on the audio having finished — that rule is a UI affordance, and the
endpoint answers whenever it is asked.

Show the correct answer **beside** what the learner typed, and do not mark it
right or wrong. "library" against "the library" is a judgement a string compare
gets wrong and a person gets right instantly.

**`GET /practice/exercises/{id}/transcript/`** →
`[{"id", "sequence", "start_time", "end_time", "text"}, …]`, ordered. `409
TRANSCRIPT_NOT_AVAILABLE` when the exercise has no segments.

Unlike `reveal/` (§7.5), **this records nothing** — worksheet mode has no notion
of giving up, so reading along is not an event worth logging. Each line carries
its `start_time`, so clicking one can seek the player.

### Suggested layout

PDF pane on the left, sticky and full height. Answer inputs on the right, in a
column that scrolls. Keep the player **pinned** — a −10s button that scrolls out
of reach defeats the whole workflow. Below a tablet width, make the two panes a
tab toggle rather than a cramped split.

## 8. Errors

Two shapes. **Field validation** keeps a key per field:

```json
{ "code": "VALIDATION_ERROR", "title": ["This field is required."] }
```

Map those onto form fields directly. **Everything else** is flat:

```json
{ "code": "EXERCISE_NOT_READY", "detail": "...", "extra": {"reasons": [...]} }
```

Branch on `code`, never on `detail` — the prose may change.

| Code | Status | Suggested UX |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Inline field errors |
| `AUTHENTICATION_FAILED` | 401 | Refresh once, then log out |
| `TOKEN_INVALID` | 401 | Log out |
| `PERMISSION_DENIED` | 403 | "You don't have access" |
| `NOT_FOUND` | 404 | Not-found page |
| `INVALID_AUDIO_FILE` / `AUDIO_FILE_TOO_LARGE` | 400 | Upload field error |
| `INVALID_PDF_FILE` / `PDF_FILE_TOO_LARGE` | 400 | Upload field error on `pdf_file` |
| `INVALID_ANSWER_KEY` | 400 | Highlight the line in `extra.line`, or the question in `extra.number` |
| `INVALID_SEGMENT_RANGE` | 400 | Highlight the field in `extra.field` |
| `SEGMENT_OUTSIDE_AUDIO` | 400 | "Segment ends after the audio does" |
| `DUPLICATE_SEGMENT_SEQUENCE` | 400 | "Position already taken" |
| `EXERCISE_NOT_READY` | 409 | Publish checklist from `extra.reasons` |
| `EXERCISE_ALREADY_PUBLISHED` | 409 | Refresh state; it's already live |
| `EXERCISE_NOT_PUBLISHED` | 409 | "No longer available" — leave practice |
| `EXERCISE_PROCESSING` | 409 | "Transcription in progress" — disable editing |
| `TRANSCRIPT_NOT_AVAILABLE` | 409 | Segment has no transcript |

**404 vs 403 is meaningful.** Resources you may not see return **404**, because
a 403 would confirm they exist. Don't render "permission denied" for a 404 —
say not found.

---

## 9. Gotchas

- **`public/` routes 404 rather than 403.** Everywhere else a 404 can mean
  "exists but not yours"; on the public surface it only ever means "not
  published". Don't offer a login prompt off it.
- **Don't send a bearer token to `public/` routes.** They ignore authentication
  entirely, so a creator hitting them sees the learner view of their own draft
  — which is to say, a 404. Switch the base path on login, don't just add the
  header (§5).
- **Pagination is inconsistent by design.** Exercise list, history, attempts
  and the leaderboard are paginated objects. Segment lists (both creator and
  practice) are **bare arrays** — a transcript is read whole. Don't write one
  generic list handler that assumes `.results`. The leaderboard page also
  carries `me` beside `results`; a list helper that only unwraps `results`
  will drop the caller's sticky row.
- **The schema under-documents the exercise detail response.** `segments`,
  `processing_error`, `processing_started_at` and `transcription_provider` are
  added conditionally for the owner/admin and **do not appear in `schema.yml`**.
  A generated client will not have them. Add them to your types by hand:

  ```ts
  type ExerciseDetail = GeneratedDetail & {
    segments?: TranscriptSegment[];      // owner/admin only
    processing_error?: string;           // owner/admin only
    processing_started_at?: string | null;
    transcription_provider?: string;
  };
  ```

  For a student the keys are **absent**, not null — use presence checks.
- **Uploads are multipart, everything else is JSON.** Don't set
  `Content-Type` manually on multipart requests; let the browser add the boundary.
- **`audio_url` and `pdf_url` are absolute** and point at `/media/` in
  development. They are plain static files — no auth header is applied by the
  `<audio>` element or the PDF viewer, so don't assume the media path is
  protected.
- **`pdf_url` is `null` on most exercises.** List rows carry `has_pdf` so you
  can badge them without a detail fetch.
- **`duration` may be `null`** on any unprocessed exercise. Guard your player.
- **`score` is `null` on reveal attempts** in `/practice/attempts/`.
- **Leaderboard `user` is `id` + `full_name` only.** Never email. `me` can be
  `null` while `results` is not; `rank` is shared on ties (`1, 2, 2, 4`) — do
  not replace it with the row index.
- **Segment `sequence` starts at 1**, and reorder requires the complete set.

---

## 10. Suggested build order

1. **Anonymous catalogue** — the `public/` collection and exercise lists
   (§5). Read-only and unauthenticated, so it proves pagination, filtering and
   error handling before any token plumbing exists. The same components serve
   the signed-in browse screen by swapping the base path.
2. **Auth shell** — login, register, token storage, refresh interceptor, `me`
   bootstrap, role-based routing.
3. **Practice loop** — the core product: `start/` → audio player with segment
   seeking → answer box → submit → diff rendering. Build this before any
   creator UI; it is where the design risk is.
4. **Progress, history & leaderboard** — progress bar on the practice screen,
   history list, and the ranked board from `GET /practice/leaderboard/` (§7.7).
5. **Creator: upload + status polling** — the upload form and the
   `processing → ready|failed` state machine with retry.
6. **Creator: segment editor** — table with inline text/timing edits,
   drag-to-reorder, per-segment audio preview.
7. **Creator: publish** — the precondition checklist driven by `extra.reasons`.

Steps 3 and 5–7 are independent; the practice loop can be built against a
manually-seeded exercise.

## 11. Local setup

```bash
# backend
cd backend && ../.venv/bin/python manage.py runserver     # :8000

# background transcription (optional for frontend work)
docker compose up -d redis
cd backend && ../.venv/bin/celery -A config worker --loglevel=info
```

For frontend development you rarely need a real transcription: set
`TRANSCRIPTION_PROVIDER=stub` in `backend/.env` and uploads produce four fixed
segments instantly, with no API key and no network.

Generate a typed client from the committed schema:

```bash
npx openapi-typescript backend/schema.yml -o src/api/schema.d.ts
```

Regenerate whenever `schema.yml` changes — it is committed and kept in sync by
a build check, so a diff there means the contract moved.
