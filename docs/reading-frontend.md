# Reading Practice — Frontend Implementation Guide

How to build the paced-reading client. Every payload below matches the
serializers in `reading/` — but `backend/schema.yml` is the authority, and
`/api/docs/` renders it interactively.

**Base URL:** `/api/v1/reading/` · **Schema:** `/api/schema/` · **Swagger:** `/api/docs/`

Listening and writing are separate domains:
[frontend-implementation.md](frontend-implementation.md) ·
[writing-frontend.md](writing-frontend.md). Read the listening guide for auth,
token refresh and the shared error envelope — they are identical here and are
not repeated in full.

---

## 1. What this domain is

A learner pastes a **passage** (typically a Cambridge IELTS Listening Section 4
transcript), optionally attaches the lecture audio, picks a target WPM, and
reads against a moving boundary. **The animation lives entirely in the
browser.** Django stores the text, the optional file, and each run's result.

```
Anyone with an account                 The same person, later
──────────────────────                 ──────────────────────
paste a passage (+ optional audio)     open it → pick WPM → Start
   ↓                                      ↓
word_count derived from the body       POST /sessions/  (server clocks start)
   ↓                                      ↓
(optional) creators publish            rAF fade / pause / restart
   ↓                                      ↓
public catalogue                       PATCH /sessions/{id}/ when the boundary
                                       hits the end → display actual_wpm
```

If you are porting patterns over from the listening or writing client, unlearn
these:

| Listening / writing | Reading |
|---|---|
| Audio or PDF *is* the exercise | **The body text is the exercise** |
| Unit of work is a segment or PDF page | Unit of work is a **whole-passage run** |
| `POST` an attempt, or `PUT` a page | `POST` a session, `PATCH` it finished |
| Score or notebook upsert | Append-only history (200 WPM, then 250, then 300) |

There is no `status`, no transcription, no polling, no dictation score, no
notebook. Do not build UI for any of them.

**Out of scope for v1 (do not invent API for these):** highlight/guided mode
UI, comprehension questions, IELTS Passage 1/2/3 time budgets (15/20/25 min),
importing a listening transcript into a passage.

---

## 2. The one rule that matters

**A session is private to the person who ran it. Nobody else can read it —
not the passage's author, not an admin.**

Publishing a passage shares the body (and `audio_url` if present). It never
shares anyone's WPM history. Two consequences for the client:

- There is no "see everyone's speeds" screen for a passage's owner.
- Session list URLs are scoped by the caller's token.
  `GET /reading/exercises/{id}/sessions/` is *your* runs. Someone else's
  session id is **404**, not 403.

Anonymous visitors can **read** a published passage via `/reading/public/`.
They must log in to press Start, because a session row needs a user.

---

## 3. Two objects

**Passage** (`ReadingExercise`) — title, body, derived `word_count`, optional
audio, `suggested_wpm`. Shared, possibly published, owned by whoever pasted it.

**Session** (`ReadingSession`) — one run: `mode`, `target_wpm`, `strict_mode`,
a **snapshot** of `word_count` at start, `reading_ms`, `paused_ms`, `progress`,
and a server-computed `actual_wpm` on finish. Repeatable. Editing the passage
body later does **not** rewrite old sessions.

---

## 4. Roles

Same JWT and same three roles as listening/writing:

| Action | Who |
|---|---|
| Create a passage | **any authenticated user**, including `student` |
| Read your own passage | you |
| Read someone else's passage | only if `is_published` |
| Edit / delete a passage | owner or admin |
| Attach / replace / delete audio | owner or admin |
| **Publish / unpublish** | owner **and** `creator` role |
| Start / finish / list sessions | any authenticated user who can see the passage |
| Read a session | **only its author** |

Creating is not creator-gated on purpose: "I copied this Section 4 transcript,
let me train WPM" is the driving use case. A student's passage is simply
private — they get a 403 with `PERMISSION_DENIED` if they try to publish it.

Gate the "Publish" button on `user.role !== 'student' && passage.owner.id === user.id`.

---

## 5. Passages

### 5.1 Create

`POST /reading/exercises/` — **JSON**. `body` required.

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Trimmed |
| `body` | yes | Plain text, paragraphs preserved. At least one tokenizable word. Max `MAX_READING_BODY_LENGTH` (default 80_000) |
| `description` | no | |
| `source` | no | Free text: "Cambridge IELTS 18 Test 1 Section 4" |
| `language` | no | Defaults `"en"` |
| `suggested_wpm` | no | Defaults `250`. Range **100–500** |

**Do not send `word_count`.** It is derived with the same `tokenize()` used
for listening transcripts and writing pages, and silently ignored if you send
it.

```json
{ "id": 1, "title": "AI in education", "description": "",
  "source": "Cambridge IELTS 18 Test 1 Section 4",
  "owner": { "id": 1, "full_name": "Tyler Reese" },
  "body": "The development of artificial intelligence has changed many aspects.",
  "word_count": 9, "suggested_wpm": 250, "audio_url": null, "language": "en",
  "is_published": false, "published_at": null, "has_audio": false,
  "session_count": 0, "last_actual_wpm": null, "last_target_wpm": null,
  "created_at": "2026-09-15T16:00:00Z",
  "updated_at": "2026-09-15T16:00:00Z" }
```

Create is **synchronous**. When the 201 lands, the passage is fully usable.

Empty or punctuation-only `body` is `400` on the `body` field
(`VALIDATION_ERROR`).

### 5.2 List

`GET /reading/exercises/` — your own passages in any state, plus everyone's
published ones. Admins see all. Paginated (`?page`, `?page_size`, max 100).

Filters: `search` (title, description, source), `mine=true`, `is_published`,
`ordering` (`created_at`, `-created_at`, `title`, `-title`, `word_count`).

**List rows omit `body` and `audio_url`.** Fetch detail for those.

```json
{ "count": 1, "next": null, "previous": null,
  "results": [
    { "id": 1, "title": "AI in education", "source": "Cambridge IELTS 18 Test 1 Section 4",
      "owner": { "id": 1, "full_name": "Tyler Reese" },
      "word_count": 9, "suggested_wpm": 250, "language": "en", "is_published": false,
      "has_audio": false, "session_count": 0, "last_actual_wpm": null,
      "last_target_wpm": null, "created_at": "2026-09-15T16:00:00Z" }
  ] }
```

`session_count` / `last_actual_wpm` / `last_target_wpm` are **the caller's**
standing. Someone else's finished run on a published passage does not show up
in your list row.

### 5.3 Detail

`GET /reading/exercises/{id}/` — includes `body` and `audio_url`.

`audio_url` is a **plain media URL**. Fetch it *without* an `Authorization`
header, exactly like listening audio. `null` when there is no file.

Someone else's draft is **404**, not 403.

### 5.4 Update

`PATCH /reading/exercises/{id}/` — owner or admin.

JSON for metadata/`body`. Multipart when attaching `audio_file`.

Editing `body` recomputes `word_count`. Existing sessions keep the snapshot
they took at Start, so history stays honest.

Replacing `audio_file` deletes the previous object from storage.

### 5.5 Delete audio

`DELETE /reading/exercises/{id}/audio/` — owner or admin. **204**, idempotent.

### 5.6 Delete passage

`DELETE /reading/exercises/{id}/` — owner or admin. Cascades every session.
Prefer unpublish to withdraw a published passage.

### 5.7 Publish / unpublish

`POST /reading/exercises/{id}/publish/` — owner **and** `creator`.

```json
{ "id": 1, "is_published": true, "published_at": "2026-09-15T16:10:00Z" }
```

Publishing twice is `409 EXERCISE_ALREADY_PUBLISHED`.

`POST /reading/exercises/{id}/unpublish/` — idempotent. Sessions already
recorded stay readable by their owners.

---

## 6. Public catalogue

No token. Stale `Authorization` headers are ignored (the view has
`authentication_classes = []`).

| Method | Path | Notes |
|---|---|---|
| GET | `/reading/public/exercises/` | Published only. No `body`. |
| GET | `/reading/public/exercises/{id}/` | Includes `body`. Draft → 404. |

There is **no** public session route. Show the passage; send the visitor to
log in before Start.

---

## 7. Sessions

### 7.1 Start

`POST /reading/exercises/{id}/sessions/`

```json
{ "mode": "paced", "target_wpm": 250, "strict_mode": false }
```

`mode` defaults to `"paced"` (`highlight` and `normal` are stored for later;
v1 UI only sends `paced`). `target_wpm` is required, 100–500. `strict_mode`
defaults `false`.

The server sets `started_at` and snapshots `word_count` from the passage.
**Do not send `started_at`, `word_count` or `actual_wpm`.**

```json
{ "id": 12, "exercise_id": 1, "mode": "paced", "target_wpm": 250,
  "strict_mode": false, "word_count": 750,
  "started_at": "2026-09-15T16:20:00Z", "finished_at": null,
  "paused_ms": 0, "reading_ms": 0, "progress": 0, "actual_wpm": null,
  "created_at": "2026-09-15T16:20:00Z", "updated_at": "2026-09-15T16:20:00Z" }
```

Hold `id` for the finish call. Restart in the UI is a **new** POST, not a
reset of this row.

A draft the caller cannot see is 404.

### 7.2 Finish

`PATCH /reading/sessions/{id}/`

```json
{ "reading_ms": 180000, "paused_ms": 5000, "progress": 100 }
```

`reading_ms` is elapsed reading time **excluding pauses**, in milliseconds,
and must be ≥ 1. `paused_ms` defaults to 0. `progress` is 0–100.

The server writes `finished_at` and:

```text
actual_wpm = word_count * 60000 / reading_ms
```

750 words in 180_000 ms → **250**. Display **the server's** `actual_wpm`,
never a client-side copy.

Already finished → `409 SESSION_ALREADY_FINISHED`. Someone else's id → 404.

### 7.3 List

`GET /reading/exercises/{id}/sessions/` — the caller's runs, newest first,
paginated.

Use this for a "your times on this passage" history: target vs actual WPM.

---

## 8. Paced reader (client-only)

Nothing in this section is sent to Django except the session start/finish
payloads above. Do **not** persist pixels/second, fade height, or content
height.

### 8.1 Duration

```text
duration_ms = (word_count / target_wpm) * 60_000
```

750 words at 250 WPM → **3:00**. Drive the footer with `elapsed` /
`duration_ms` and `round(t * 100)%`, where `t` is elapsed reading time
(pauses frozen) divided by `duration_ms`, clamped to `[0, 1]`.

### 8.2 Word-linear boundary

Wrap each word in a span (you will want the same spans for a later highlight
mode). On each animation frame:

```text
wordIndex = min(floor(t * word_count), word_count - 1)
boundaryY = offsetTop of that span (relative to the article)
```

Auto-scroll so `boundaryY` sits around **60%** down the viewport. Duration
comes from WPM; pixel speed is derived from layout, not a constant.

Use `requestAnimationFrame`, not a CSS transition on `top`. Pause freezes
elapsed. Restart resets `t` to 0, scrolls to top, and (if a session was
already POSTed) starts a new session on the next Start.

Lock the WPM slider after Start. Changing WPM mid-run jumps the duration and
the boundary.

### 8.3 Fade

Keep every line in the DOM. Mask; do not `display: none` or shrink the
article as the boundary moves.

```css
.article {
  mask-image: linear-gradient(
    to bottom,
    #000 0,
    #000 var(--boundary),
    transparent calc(var(--boundary) + var(--fade))
  );
}
```

`--fade` ≈ `2.5 * line-height` (user-adjustable 2–4 lines). Below the fade,
cover with the page background so upcoming text is hinted, not readable. A
1px hairline at `--boundary` is the "current reading boundary" from the
mockup. Leave the fade zone *under* that line — do not park the boundary on
the last visible pixel.

### 8.4 Controls

| Control | Behaviour |
|---|---|
| WPM 100–500 | Slider + presets Slow 150 / Normal 200 / Fast 250 / IELTS 300 |
| Start | POST session, start rAF, lock WPM |
| Pause | Freeze elapsed |
| Restart | Confirm if progress > 10%; new session on next Start |
| Progress | percent · elapsed / duration |
| Strict mode | Ignore wheel/touch/keys that scroll backward; auto-scroll still moves forward |

### 8.5 Optional audio

Do **not** sync the player to the fade. Lecture speech is ~140–180 WPM; paced
reading is 200–300. They fight each other.

Show a stopped player, or a "Listen after reading" action on the results
card. `audio_url` is fetched without `Authorization`, with `Range` support
like listening.

### 8.6 Results

When `t` hits 1 (or the learner taps Done): PATCH the session with
`reading_ms`, `paused_ms`, `progress`. Show:

- Reading time (excluding pauses)
- Target WPM vs server `actual_wpm`
- Word count
- Restart at +50 WPM, or Listen if `has_audio`

---

## 9. Suggested routes

- `/reading` — catalogue (authenticated list + public)
- `/reading/new` — title, source, textarea, optional audio upload after create
- `/reading/[id]` — practice screen; results overlay on the same page

Suggested create flow: `POST` JSON with the body, then `PATCH` multipart
`audio_file` if the user attached a clip.

---

## 10. Later

- **Highlight / guided mode** — same WPM clock, highlight the current 4–8
  word phrase instead of fading. `mode: "highlight"` is already accepted.
- **Comprehension questions** after the boundary hits the end.
- **"Open this listening transcript as reading"** — concatenate published
  segment text into a new `ReadingExercise.body`.
- **Passage 1/2/3 time budgets** (15/20/25 min) are a *second* clock that
  includes questions, not a 15-minute fade. Do not stretch paced reading
  over the full IELTS reading slot.
