# Member API — Book Club

Supporting reference for the Book Club family of the member API (not a
standalone skill). Load the catalog [`SKILL.md`](SKILL.md) first for shared
auth, key setup, the safe-surface rules, and error handling. This file covers a
member acting on their own Book Club activity: reading state, their own chapter
notes, and their reader profile.

Base URL and auth are the same as every family:

```text
https://aishippinglabs.com/member-api/v1
Authorization: Token <asl_member_...>
```

Boundary: every endpoint here is the key owner's own data. You can read your own
note, never another member's note, and never the group feed of other members'
notes (that is a rendered web surface, not an API resource).

## Endpoints

### Reading state for a book

```text
GET /member-api/v1/books/{slug}/reading
```

Returns the caller's per-chapter read state for the book: each chapter with its
number, title, whether the caller has read it, and the `read_at` timestamp.
Use this to see progress or to find which chapters remain.

### Mark a chapter read / unread

```text
PUT    /member-api/v1/books/{slug}/chapters/{number}/read
DELETE /member-api/v1/books/{slug}/chapters/{number}/read
```

`PUT` marks the chapter read (idempotent); `DELETE` marks it unread. Chapter
`number` starts at 0.

### The caller's own chapter note

```text
GET    /member-api/v1/books/{slug}/chapters/{number}/note
PUT    /member-api/v1/books/{slug}/chapters/{number}/note
DELETE /member-api/v1/books/{slug}/chapters/{number}/note
```

- `GET` returns the caller's own note for the chapter, or `404` if none exists
  yet.
- `PUT` upserts the caller's single note for the chapter (`update_or_create` on
  owner + chapter). `body` is required. The body is the note text in markdown —
  a fenced ` ```mermaid ` block renders as a diagram on the web, other fenced
  blocks render as code.
- `DELETE` removes the caller's note.

Upsert payload:

```json
{ "body": "The KV cache is the whole game." }
```

### Reader profile

```text
GET /member-api/v1/books/{slug}/reader-profile   (caller's reader profile)
PUT /member-api/v1/books/reader-profile           (set the caller's visibility)
```

`GET` returns the caller's reader profile. `PUT /books/reader-profile` sets the
caller's reader-profile visibility (whether the member is named on public
reading surfaces).

## Typical Workflows

### Sync reading progress from a local checklist

1. `GET /books/{slug}/reading` to see current per-chapter state.
2. For each chapter the member finished, `PUT .../chapters/{number}/read`.
3. `GET /books/{slug}/reading` again and confirm the read flags.

### Post or update a chapter takeaway

1. `GET /books/{slug}/chapters/{number}/note` to see if a note already exists.
2. `PUT .../note` with the `body`. Prefer editing (upsert) over delete+recreate.
3. Keep it the member's own words; the note is visible to the reading group.

## Rules For Agents

- Fetch before writing so you know the current state.
- `body` is required on note upsert; an empty body is a validation error.
- Chapter `number` is 0-based.
- Only ever touch the key owner's own data — never another member's note.
- `401` = missing, malformed, or revoked key; `404` = unknown/draft book, unknown chapter, or
  no note yet; `422` = payload validation error.

## Contributions

Invite improvements through PRs against `skills/ai-shipping-labs-member-api/` in the GitHub repository.
