# Writing Practice — Frontend Implementation Guide

How to build the writing client. Every payload below was captured from a real
response, not written from memory — but `backend/schema.yml` is the authority,
and `/api/docs/` renders it interactively.

**Base URL:** `/api/v1/writing/` · **Schema:** `/api/schema/` · **Swagger:** `/api/docs/`

Listening practice is a separate domain with its own contract:
[frontend-implementation.md](frontend-implementation.md). Read that one for
auth, token refresh and the shared error envelope — they are identical here and
are not repeated in full.

---

## 1. What this domain is

A learner opens a workbook **PDF**, reads it page by page, types answers or
prose beside it, and comes back later. **Nothing is graded.**

```
Anyone with an account                 The same person, later
──────────────────────                 ──────────────────────
upload a workbook PDF                  open it → notebook (auto-created)
   ↓                                      ↓
page_count read from the file          render page n  |  type beside it
   ↓                                      ↓
(optional) creators publish            PUT /pages/n/  ← debounced autosave
   ↓                                      ↓
public catalogue                       resume at last_page, mark complete
```

If you are porting patterns over from the listening client, unlearn these four:

| Listening | Writing |
|---|---|
| Audio is the exercise; PDF is an optional handout | **The PDF is the exercise** |
| Unit of work is a transcript segment | Unit of work is a **PDF page** |
| `POST` an attempt, get a score back | `PUT` a page, get the saved page back |
| Transcript hidden until submit/reveal | The PDF is fully visible from the start |

There is no `status`, no polling, no `processing` state, no score, no diff, no
reveal, no attempt history. Do not build UI for any of them.

---

## 2. The one rule that matters

**A notebook is private to the person who wrote it. Nobody else can read it —
not the workbook's author, not an admin.**

Publishing a workbook shares the PDF. It never shares anyone's writing. Two
consequences for the client:

- There is no "see submissions" screen for a workbook's owner, and no API that
  could back one. Don't design toward it.
- The notebook has no id in any URL. `/writing/exercises/{id}/notebook/`
  resolves to *your* notebook because of your token. You cannot address
  someone else's, so there is no id to leak.

---

## 3. Two objects, and the difference between them

**Workbook** (`WritingExercise`) — the PDF, its title, its page count. Shared,
possibly published, owned by whoever uploaded it.

**Notebook** (`WritingNotebook`) — your answers to that workbook. One per person
per workbook, created the first time you open it. Holds a **page row per page
you have written on**, plus `last_page` (where to resume) and `completed_at`.

Pages are **lazy**. This distinction shows up in the UI more than you'd expect:

| State | In the API | Show it as |
|---|---|---|
| Never opened this page | no row in `pages` | "not started" |
| Wrote, then deleted the text | row with `"body": ""` | "started, now empty" |
| Has text | row with a body | "started" |

`pages_started` counts only the third case. So `pages_started / page_count` is
your progress bar, and a page you cleared correctly stops counting.

---

## 4. Roles

Same JWT and same three roles as listening, with one deliberate difference:

| Action | Who |
|---|---|
| Upload a workbook | **any authenticated user**, including `student` |
| Read your own workbook | you |
| Read someone else's workbook | only if `is_published` |
| Edit / delete a workbook | owner or admin |
| **Publish / unpublish** | owner **and** `creator` role |
| Keep a notebook | any authenticated user |
| Read a notebook | **only its author** |

Uploading is not creator-gated on purpose: "I bought this workbook, let me work
through it" is the driving use case. A student's workbook is simply private —
they get a 403 with `PERMISSION_DENIED` if they try to publish it.

Gate the "Publish" button on `user.role !== 'student' && workbook.owner.id === user.id`.

---

## 5. Workbooks

### 5.1 Upload

`POST /writing/exercises/` — **multipart**, `pdf_file` required.

| Field | Required | Notes |
|---|---|---|
| `title` | yes | |
| `pdf_file` | yes | PDF only, up to **60 MB** (`MAX_WRITING_PDF_FILE_SIZE_MB`) |
| `description` | no | |
| `source` | no | Free text: book, publisher, unit |
| `language` | no | Defaults `"en"` |

**Do not send `page_count`.** It is read from the PDF server-side and silently
ignored if you send it — every page-range check downstream trusts that number,
so it is never taken from the request.

```json
{ "id": 1, "title": "Grammar in Use", "description": "Units 1-12",
  "source": "Cambridge",
  "owner": { "id": 1, "full_name": "Tyler Reese" },
  "pdf_url": "http://localhost:8000/media/writing-pdf/1/7749afab.pdf",
  "page_count": 4, "language": "en",
  "is_published": false, "published_at": null,
  "has_notebook": false, "pages_started": 0, "last_page": null,
  "created_at": "2026-08-27T03:01:31.708108Z",
  "updated_at": "2026-08-27T03:01:31.708116Z" }
```

Upload is **synchronous** — there is no transcription-style wait. When the 201
lands, the workbook is fully usable. Show a normal upload progress bar and go
straight to the workbook.

Three ways it can fail, all on the `pdf_file` field or as a flat code:

- `INVALID_PDF_FILE` — not a PDF (checked by header bytes, not just extension)
- `PDF_FILE_TOO_LARGE` — over the limit
- `UNREADABLE_PDF_FILE` — real PDF header, but corrupt or password-protected.
  Worth its own message: "This PDF is password-protected or damaged, so we
  can't read its pages."

### 5.2 List

`GET /writing/exercises/` — your own workbooks in any state, plus everyone's
published ones. Admins see all. Paginated (`?page`, `?page_size`, max 100).

```json
{ "count": 1, "next": null, "previous": null,
  "results": [
    { "id": 1, "title": "Grammar in Use", "source": "Cambridge",
      "owner": { "id": 1, "full_name": "Tyler Reese" },
      "page_count": 4, "language": "en", "is_published": false,
      "has_notebook": true, "pages_started": 2, "last_page": 3,
      "created_at": "2026-08-27T03:01:31.708108Z" } ] }
```

`has_notebook` / `pages_started` / `last_page` are **yours**, not aggregates —
each row tells you where *you* are in that workbook. `last_page` is `null` when
you have no notebook yet. That is everything a "Continue" button needs, so the
list needs no follow-up request per row.

**List rows deliberately carry no `pdf_url`.** Fetch the detail view when you
actually open a workbook.

Filters: `?mine=true` (your uploads only), `?is_published=`, `?owner=`,
`?language=`, `?search=` (title, description, source),
`?ordering=` one of `created_at`, `-created_at`, `title`, `-title`, `page_count`.

A reasonable "My workbooks / Browse" split is `?mine=true` against
`?is_published=true`.

### 5.3 Detail

`GET /writing/exercises/{id}/` — adds `description`, `published_at` and the one
field that matters: **`pdf_url`**.

Someone else's unpublished workbook returns **404, not 403** — a 403 would
confirm it exists. Render "not found", never "permission denied".

### 5.4 Edit, replace, delete

`PATCH /writing/exercises/{id}/` (multipart) — owner or admin. `title`,
`description`, `source`, `language`, `pdf_file`.

**Replacing `pdf_file` is refused with 409 once the workbook is published, or
once any learner has started a notebook against it.**

```json
{ "code": "WRITING_EXERCISE_HAS_NOTEBOOKS",
  "detail": "Learners have started this workbook, so its PDF can no longer be replaced. Upload the new edition as a separate workbook.",
  "extra": { "reasons": ["notebooks_exist"] } }
```

`extra.reasons` is `["workbook_is_published"]` or `["notebooks_exist"]`. The
first is fixable in the UI — offer "Unpublish and replace". The second is not:
page numbers are the only link between a notebook and the PDF, so a
re-paginated replacement would silently misfile everything already written.
Steer the user to uploading a new workbook.

Metadata edits are always allowed, notebooks or not. So keep the title field
live and disable only the file input.

`DELETE /writing/exercises/{id}/` — owner or admin, **cascades to every
learner's notebook and pages**. This destroys other people's writing. Confirm
with the count if you have it, and offer unpublish as the alternative.

### 5.5 Publish

`POST /writing/exercises/{id}/publish/` and `.../unpublish/`, no body.

```json
{ "id": 1, "is_published": true, "published_at": "2026-08-27T03:01:31.738921Z" }
```

There is no publish checklist — a workbook is publishable the moment it has a
PDF, which is always. Publishing twice is `409 EXERCISE_ALREADY_PUBLISHED`
(just refresh state; it's already live). Unpublishing is idempotent and keeps
every notebook intact.

---

## 6. The notebook — this is the product

### 6.1 Open it

`GET /writing/exercises/{id}/notebook/`

**This GET creates the notebook if it doesn't exist.** That is deliberate —
opening a workbook is what starting one means, and there is no "enrol" step to
build. It does mean a prefetch on hover will create notebooks, so don't
speculatively fetch this route.

```json
{ "id": 1, "exercise_id": 1, "title": "Grammar in Use",
  "pdf_url": "http://localhost:8000/media/writing-pdf/1/7749afab.pdf",
  "page_count": 4, "pages_started": 2, "last_page": 3, "completed_at": null,
  "pages": [
    { "page_number": 1, "body": "1. however\n2. nevertheless",
      "word_count": 4, "updated_at": "2026-08-27T03:01:31.718894Z" },
    { "page_number": 3, "body": "The results were inconclusive.",
      "word_count": 4, "updated_at": "2026-08-27T03:01:31.722517Z" } ],
  "created_at": "…", "updated_at": "…" }
```

One request gives you the PDF URL, every page of text, the progress numbers and
the resume point. **Load this once when the workbook opens and hold it in local
state** — `pages` is the whole document, so you never need to fetch a page you
already have. Open the PDF at `last_page`.

`pages` is ordered by `page_number` and sparse. Index it into a map keyed by
page number rather than by array position:

```js
const byPage = new Map(notebook.pages.map(p => [p.page_number, p]));
const bodyFor = n => byPage.get(n)?.body ?? "";
```

### 6.2 Save a page — the autosave contract

`PUT /writing/exercises/{id}/pages/{n}/`

```json
{ "body": "1. however\n2. nevertheless" }
```
→
```json
{ "page_number": 3, "body": "The results were inconclusive.",
  "word_count": 4, "updated_at": "2026-08-27T03:01:31.722517Z" }
```

The contract, and why each part matters:

- **It upserts.** Firing it fifty times on page 3 leaves one row, not a
  history. This is the whole difference between a notebook and an attempt log.
- **It is idempotent.** There is no attempt id, nothing to reconcile, and a
  retry after a network blip is always safe.
- **An empty `body` is a real save**, not a validation error — it clears the
  page. Do not skip the request when the textarea empties, or the old text
  survives on the server.
- **It moves `last_page` to `n`** and bumps the notebook's `updated_at`, every
  time, even when the bookmark hasn't moved. That is what floats the workbook
  to the top of "continue writing".
- **Max 20 000 characters** (`MAX_WRITING_PAGE_LENGTH`). Over that is a field
  error on `body`. Show a counter as they approach it.

Debounce **500–1000 ms** after the last keystroke. Also flush immediately on
page change, on blur, and on `visibilitychange` → `hidden`.

**Guard against out-of-order responses.** Two saves in flight can land in
either order, and the later response would then overwrite your "Saved at"
state with a stale timestamp. Keep a per-page sequence number and ignore any
response that isn't the newest:

```js
const seq = useRef(0);
async function save(page, body) {
  const mine = ++seq.current;
  const res = await api.put(`/writing/exercises/${id}/pages/${page}/`, { body });
  if (mine === seq.current) setSavedAt(res.updated_at);   // stale replies dropped
}
```

Never write the response's `body` back into the textarea. The learner may have
typed more since the request left; echoing the server's copy makes characters
disappear. Take only `updated_at` and `word_count` from the response.

**Status indicator.** Three states, driven locally: `dirty` → "Unsaved", request
in flight → "Saving…", newest response landed → "Saved" (render `updated_at`
relatively). On failure show "Not saved — retrying" and keep the text; it is
still in the textarea, and the next debounce will carry it.

### 6.3 Read one page

`GET /writing/exercises/{id}/pages/{n}/` — mostly unnecessary, since the
notebook payload already has every page. Useful for a hard refresh of a single
page, or a second tab.

A page never written to returns **200 with an empty body**, not 404:

```json
{ "page_number": 2, "body": "", "word_count": 0, "updated_at": null }
```

`updated_at: null` is your "never saved" signal.

### 6.4 Page numbers out of range

Both `GET` and `PUT` reject a page outside the workbook with **400**:

```json
{ "code": "PAGE_OUT_OF_RANGE",
  "detail": "This workbook has 4 pages, so page 99 does not exist.",
  "extra": { "page_count": 4 } }
```

Pages are **1-indexed**. pdf.js is too, so no conversion — but if you keep an
array of page state, remember index 0 is page 1. Clamp navigation to
`1..page_count` client-side; treat this error as a bug signal, not a flow.

### 6.5 Completion

`POST /writing/exercises/{id}/notebook/complete/` marks it done;
`DELETE` on the same URL un-marks it. Both return the full notebook payload.

Completion is **the learner's own claim**. Nothing infers it from how many
pages have text, because nothing here is graded — a workbook with three pages
thoughtfully skipped can be finished. Re-completing keeps the original
timestamp, so the "Completed on …" date is stable.

Offer "Mark as complete" as a plain button at the end of the workbook, not a
gate. Show `completed_at` as a badge, and let it be undone.

### 6.6 Continue writing

`GET /writing/notebooks/` — your notebooks, **most recently touched first**,
paginated. No page bodies.

```json
{ "count": 1, "next": null, "previous": null,
  "results": [
    { "id": 1, "exercise_id": 1, "title": "Grammar in Use", "source": "Cambridge",
      "page_count": 4, "pages_started": 2, "last_page": 3,
      "completed_at": null, "updated_at": "2026-08-27T03:01:31.722875Z" } ] }
```

This is the home-screen rail. Each row deep-links to
`/workbooks/{exercise_id}?page={last_page}`.

---

## 7. The public catalogue

`GET /writing/public/exercises/` and `/writing/public/exercises/{id}/` —
published workbooks, no account needed. Same row and detail shapes, same
filters, ordering and `search`.

For an anonymous caller the progress fields are constants: `has_notebook: false`,
`pages_started: 0`, `last_page: null`. Render "Sign in to start writing" rather
than a progress bar.

**Don't send an `Authorization` header to `public/` routes.** They ignore
authentication entirely, and a signed-in user hitting them sees the anonymous
view of their own workbook — including the false progress above. Use the
authenticated list for anyone with a token.

There is deliberately no public notebook route. Publishing shares the PDF;
writing something down requires an account. An anonymous visitor can browse and
read the PDF, and that is the whole preview.

---

## 8. Rendering the PDF

Use `react-pdf` (or pdf.js directly). Two things trip people up:

**`pdf_url` is an unauthenticated media URL.** Fetch it **without** the
`Authorization` header — same rule as listening's audio. In production it is an
absolute S3/CDN URL; in development it is same-origin `/media/…`. Never assume
one or the other, just use the string you were given.

```jsx
<Document file={notebook.pdf_url} onLoadSuccess={({ numPages }) => setNumPages(numPages)}>
  <Page pageNumber={page} width={paneWidth} />
</Document>
```

**Trust the server's `page_count`, not pdf.js's `numPages`.** They should agree
— both count the same file. If they don't, the server's number is what every
page-range check uses, so a page pdf.js shows but the API rejects would be a
dead end. Clamp navigation to `page_count`.

Ship the pdf.js worker as a local asset rather than a CDN URL, and load the
document once — re-creating `<Document>` on every page change refetches the
whole PDF.

---

## 9. The workbook screen

Two panes, and the PDF drives navigation:

```
┌───────────────────────────┬──────────────────────────┐
│  PDF page 3 of 4          │  Page 3          Saved ✓ │
│  [zoom −] [+] [fit]       │ ┌──────────────────────┐ │
│                           │ │                      │ │
│      (rendered page,      │ │  textarea bound to   │ │
│       own scroll)         │ │  the current page    │ │
│                           │ │                      │ │
│                           │ └──────────────────────┘ │
│  [◀ prev]  3 / 4  [next ▶]│  2 of 4 pages started    │
└───────────────────────────┴──────────────────────────┘
```

- The two panes scroll **independently**. A long answer must not scroll the PDF
  away, and vice versa.
- Changing page: flush the current page if dirty, then swap the textarea to
  `bodyFor(next)`. Never let a pending save land against the new page number —
  key the debounce by page and cancel it on navigation.
- Keep the page controls pinned. Same reasoning as the listening player: a
  control that scrolls out of reach defeats the workflow.
- Progress is `pages_started / page_count`. **No score chrome** — no
  percentages, no ticks, no right/wrong colours. Nothing here is graded, and
  borrowing the dictation UI implies otherwise.
- Below tablet width, make the two panes a **tab toggle** (Sheet | Notes)
  rather than a cramped split.
- `word_count` per page is free from the API — a quiet "412 words" under the
  textarea is genuinely useful for essay-style workbooks.

Keep an unsent draft in `localStorage` keyed by workbook and page, cleared on a
confirmed save. It costs little and covers the closed-tab case.

---

## 10. Errors

Same envelope as the rest of the API. Field validation keeps a key per field;
everything else is flat with a stable `code`. Branch on `code`, never on
`detail`.

| Code | Status | Suggested UX |
|---|---|---|
| `VALIDATION_ERROR` | 400 | Inline field errors (`pdf_file`, `title`, `body`) |
| `INVALID_PDF_FILE` | 400 | "That file isn't a PDF" on the upload field |
| `PDF_FILE_TOO_LARGE` | 400 | "Workbooks can be up to 60 MB" |
| `UNREADABLE_PDF_FILE` | 400 | "Password-protected or damaged — we can't read its pages" |
| `PAGE_OUT_OF_RANGE` | 400 | Clamp to `extra.page_count`; treat as a client bug |
| `AUTHENTICATION_FAILED` / `TOKEN_INVALID` | 401 | Refresh once, then log out |
| `PERMISSION_DENIED` | 403 | Publish attempted as a student, or not your workbook |
| `NOT_FOUND` | 404 | Not-found page — **never** "permission denied" |
| `WRITING_EXERCISE_HAS_NOTEBOOKS` | 409 | See `extra.reasons` (§5.4) |
| `EXERCISE_ALREADY_PUBLISHED` | 409 | Refresh state; it's already live |

**404 vs 403 is meaningful.** A workbook you may not see returns 404, because a
403 would confirm it exists.

---

## 11. Gotchas

- **`GET .../notebook/` and `GET .../pages/{n}/` create the notebook.** A read
  with a side effect. Don't prefetch them on hover or in a route preloader.
- **Don't skip the save when the textarea empties.** Blank is a legitimate
  value; skipping leaves the old text on the server.
- **Don't echo the response `body` back into the textarea.** See §6.2 — the
  learner has kept typing.
- **`last_page` is `null`, not `1`, before a notebook exists.** Open the PDF at
  `notebook.last_page ?? 1`.
- **A page row with `"body": ""` is not "not started".** It is absent-vs-empty,
  and `pages_started` already gets it right — use that number rather than
  `pages.length`.
- **No polling anywhere.** There is no async pipeline in this domain. If you
  find yourself writing a `setInterval` against a writing endpoint, something
  has been ported over from listening by mistake.
- **`page_count` cannot change once notebooks exist**, which is exactly why
  page numbers are a safe key for local state and `localStorage` drafts.
- **Publishing shares the PDF, never the writing.** There is no API that
  returns another person's pages, so don't design a screen that needs one.

---

## 12. Suggested build order

1. **Workbook list** — `GET /writing/exercises/`, with `?mine=true` /
   `?is_published=true` tabs. Rows already carry progress, so the "Continue"
   button works from day one.
2. **Upload** — multipart `POST`, with the three PDF error cases handled.
3. **The workbook screen** — detail `GET` for `pdf_url`, notebook `GET` for the
   pages, `react-pdf` on the left, textarea on the right, page navigation.
   Read-only at this stage.
4. **Autosave** — debounced `PUT`, sequence guard, the three-state indicator.
   This is the step worth spending the time on.
5. **Continue-writing rail** — `GET /writing/notebooks/` on the home screen.
6. **Completion** — the button and the badge.
7. **Publish** — creator-only controls, and the replace-refused 409 handling.
8. **Public catalogue** — `public/exercises/` for signed-out browsing.
