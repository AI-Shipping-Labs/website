# Member API

Use the AI Shipping Labs member API to work with your own sprint plans, Book Club activity, and event registration from local tools and agents.

Base URL:

```text
https://aishippinglabs.com/member-api/v1
```

Authentication:

```text
Authorization: Token <asl_member_...>
```

The key belongs to the signed-in member who created it. Every valid key has the same capabilities and acts only as that member. It cannot access Studio or staff APIs.

## Create A Key

1. Sign in to AI Shipping Labs.
2. Open `https://aishippinglabs.com/account/#api-keys`.
3. Create a named key for your local tool.
4. Copy the key immediately. The plaintext value is shown once.

Store the key in a local secret store or an environment variable:

```bash
export AI_SHIPPING_LABS_MEMBER_API_KEY="<asl_member_...>"
```

Revoke an old key from the same `API keys` section on the account page. Revoked keys stop working immediately.

## OpenAPI Spec

Download the machine-readable member API spec:

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/openapi.json
```

The interactive docs are available at `https://aishippinglabs.com/member-api/docs` for signed-in members.

## List My Plans

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans
```

Example response:

```json
{
  "plans": [
    {
      "id": 12,
      "sprint": {
        "slug": "may-2026",
        "name": "May 2026"
      },
      "member": {
        "display_name": "Alice"
      },
      "title": "Ship an eval toolkit",
      "visibility": "private",
      "progress": {
        "checkpoints_done": 1,
        "checkpoints_total": 4
      },
      "shared_at": null,
      "created_at": "2026-05-01T10:00:00+00:00",
      "updated_at": "2026-05-01T10:00:00+00:00"
    }
  ]
}
```

## Get One Plan

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans/12
```

The detail response includes the plan summary, focus, accountability, weeks, checkpoints, week notes, resources, deliverables, and next steps that are safe for the member owner to see.

Example response (abridged):

```json
{
  "id": 12,
  "sprint": {"slug": "may-2026", "name": "May 2026"},
  "member": {"display_name": "Alice"},
  "title": "Ship an eval harness",
  "visibility": "private",
  "progress": {"checkpoints_done": 1, "checkpoints_total": 3},
  "goal": "A working evaluation harness",
  "summary": {
    "current_situation": "",
    "goal": "A reusable eval harness",
    "main_gap": "",
    "weekly_hours": "6h",
    "why_this_plan": ""
  },
  "focus": {"main": "Evaluation", "supporting": ["Tracing", "CI"]},
  "accountability": "Weekly demo in #plan-sprints",
  "weeks": [
    {
      "id": 34,
      "plan_id": 12,
      "week_number": 1,
      "theme": "Discovery",
      "position": 0,
      "note": {"id": 5, "week_id": 34, "body": "Kicked off", "created_at": "2026-05-01T10:00:00+00:00", "updated_at": "2026-05-01T10:00:00+00:00"},
      "checkpoints": [
        {"id": 56, "week_id": 34, "description": "Draft the eval rubric", "position": 0, "done_at": "2026-05-02T09:00:00+00:00"}
      ]
    }
  ],
  "resources": [{"id": 7, "title": "Tracing docs", "url": "https://example.com/tracing", "note": "", "position": 0}],
  "deliverables": [{"id": 9, "description": "A working eval harness", "position": 0, "done_at": null}],
  "next_steps": [{"id": 11, "kind": "pre_sprint", "description": "Read the tracing docs", "position": 0, "done_at": null}]
}
```

## Download Plan Markdown

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -o plan-12.md \
  https://aishippinglabs.com/member-api/v1/plans/12/markdown
```

## Update Progress

Use `PATCH /plans/{plan_id}/progress` to mark existing checkpoints, deliverables, and next steps done or not done. The endpoint only toggles progress. It does not create, delete, reorder, rename, move, or edit descriptions.

```bash
curl -sS -X PATCH \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "checkpoints": [{"id": 123, "done": true}],
    "deliverables": [{"id": 456, "done": false}],
    "next_steps": [{"id": 789, "done": true}]
  }' \
  https://aishippinglabs.com/member-api/v1/plans/12/progress
```

Send only the collections you want to update. Validation failures roll back the entire request.

## Key Capabilities

Every active member key can use every deployed member endpoint. You do not choose permissions when creating a key and do not need to replace an existing key when a new endpoint family is added. All member endpoints remain owner-only.

## Book Club Reading

The Book Club endpoints track which chapters you have read. Every endpoint is owner-only: there is no request parameter that can target another member, so a key only ever reads or mutates its own reading state. Access mirrors the site — a book gated above your tier returns `403`, and an unknown or draft book (or unknown chapter number) returns `404`.

`GET /books/{slug}/reading` returns your per-chapter state plus totals:

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/books/inference-engineering/reading
```

```json
{
  "book": {"slug": "inference-engineering", "title": "Inference Engineering"},
  "total": 5,
  "done": 2,
  "pct": 40,
  "chapters": [
    {"number": 0, "title": "Inference", "read": true, "read_at": "2026-08-06T10:00:00+00:00"},
    {"number": 1, "title": "Prerequisites", "read": false, "read_at": null}
  ]
}
```

`PUT /books/{slug}/chapters/{number}/read` marks a chapter read; `DELETE` on the same path marks it unread. Both are idempotent — repeating the call is a no-op — and return the updated `done` / `total`:

```bash
curl -sS -X PUT \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/books/inference-engineering/chapters/0/read
# {"read": true, "read_at": "2026-08-06T10:00:00+00:00", "done": 3, "total": 5}
```

### Your own chapter note

Each member may keep one note per chapter. The note endpoint is owner-only: it only ever reads or mutates your own note — never another member's, and never the group feed of other members' notes (that is a rendered web surface). Posting comments on a note also stays on the shared web endpoints and is not part of the member API.

`PUT /books/{slug}/chapters/{number}/note` upserts your note; `body` is required and `diagram` (mermaid source) is optional. `DELETE` on the same path clears it (idempotent). `GET` returns your own note or `404` if you have not written one:

```bash
curl -sS -X PUT \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "The KV cache is the whole game."}' \
  https://aishippinglabs.com/member-api/v1/books/inference-engineering/chapters/0/note
# {"book": {...}, "chapter": {"number": 0, "title": "Inference"},
#  "body": "The KV cache is the whole game.", "diagram": "",
#  "comment_content_id": "…", "created_at": "…", "updated_at": "…"}
```

## Editing Plan Content

Reshape your own plan structure and narrative through the plan endpoints. Every write is owner-only (you can only touch your own plan) and atomic (a validation error changes nothing). Item ordering follows `position` (ascending), so set `position` on create or `PATCH` to reorder. A bulk reorder endpoint that accepts an ordered id list is deferred; the position-per-`PATCH` approach covers the build-and-iterate loop.

The recommended loop for an agent: `GET` the plan detail, decide the changes, apply them with the calls below, then `GET` again and repeat until the plan matches what the member wants.

Response bodies: a `POST` create returns `201` with the created row (for example a checkpoint: `{"id": 56, "week_id": 34, "description": "Draft the eval rubric", "position": 0, "done_at": null}`); a `PATCH` returns `200` with the updated row; the plan-level `PATCH /plans/{plan_id}` returns `200` with the full plan detail; a `DELETE` returns `200` with a small confirmation body such as `{"deleted": true, "id": 56}` (the week note delete returns `{"deleted": true, "week_id": 34}`).

### Edit Narrative Fields

`PATCH /plans/{plan_id}` updates plan-level fields. Only the keys you send change; the response is the full plan detail. `visibility` accepts `private` or `cohort`; `public` is reserved and rejected with `422`.

```bash
curl -sS -X PATCH \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Ship an eval harness",
    "goal": "A working evaluation harness",
    "summary": {"goal": "A reusable eval harness", "weekly_hours": "6h"},
    "focus": {"main": "Evaluation", "supporting": ["Tracing", "CI"]},
    "accountability": "Weekly demo in #plan-sprints",
    "visibility": "cohort"
  }' \
  https://aishippinglabs.com/member-api/v1/plans/12
```

### Weeks

```bash
# Create a week (week_number is required, positive, unique per plan)
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"week_number": 1, "theme": "Discovery", "position": 0}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks

# Update a week
curl -sS -X PATCH \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"theme": "Build", "position": 1}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34

# Delete a week (cascades its checkpoints and week note)
curl -sS -X DELETE \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34
```

### Checkpoints

Checkpoints belong to a week. Create them under a week; move a checkpoint to another week of the same plan by sending a `week_id`.

```bash
# Create (description required; optional initial done)
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description": "Draft the eval rubric", "position": 0, "done": false}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34/checkpoints

# Update, mark done, or move to another week
curl -sS -X PATCH \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description": "Refine the eval rubric", "done": true, "week_id": 35}' \
  https://aishippinglabs.com/member-api/v1/plans/12/checkpoints/56

# Delete
curl -sS -X DELETE \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans/12/checkpoints/56
```

### Deliverables, Next Steps, Resources

Deliverables, next steps, and resources are plan-level collections.

```bash
# Deliverable (description required; optional done)
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description": "A working eval harness"}' \
  https://aishippinglabs.com/member-api/v1/plans/12/deliverables

# Next step (description required; kind is pre_sprint (default) or next_step)
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"description": "Read the tracing docs", "kind": "pre_sprint"}' \
  https://aishippinglabs.com/member-api/v1/plans/12/next-steps

# Resource (title required; url optional but must be valid)
curl -sS -X POST \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Tracing docs", "url": "https://example.com/tracing"}' \
  https://aishippinglabs.com/member-api/v1/plans/12/resources
```

Each collection has a matching `PATCH /plans/{plan_id}/{collection}/{id}` (edit `description` / `position` / `done`, plus `kind` for next steps and `title` / `url` / `note` for resources) and `DELETE /plans/{plan_id}/{collection}/{id}`.

### Week Notes

A week has at most one member-authored note. `PUT` upserts it and records you as the author; `DELETE` removes it.

```bash
curl -sS -X PUT \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"body": "Shipped the harness; blocked on flaky evals."}' \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34/note

curl -sS -X DELETE \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/plans/12/weeks/34/note
```

## Errors

Missing or invalid key:

```json
{
  "error": "Member API key required",
  "code": "member_api_key_required"
}
```

The status code is `401`.

Plan not owned by the key owner, or missing:

```json
{
  "error": "Plan not found",
  "code": "plan_not_found"
}
```

The status code is `404`.

Validation error:

```json
{
  "error": "Progress item done must be boolean",
  "code": "validation_error",
  "details": {
    "field": "checkpoints[0].done"
  }
}
```

The status code is `422`.

## Data Boundaries

The member API is limited to the signed-in key owner's own plans, and this holds for the write surface too: progress updates only ever touch the caller's own plan items.

The API never exposes or lets you modify internal notes, CRM notes, onboarding answers, staff context, or other members' data:

- other members' data — you cannot read, list, or update another member's plan, even a cohort teammate's. Cohort visibility on the website does not grant API access to a teammate's plan.
- internal notes and CRM notes — staff-facing notes attached to a member or plan are never returned by any endpoint.
- onboarding answers — the answers a member gives during signup are never surfaced through the API.
- staff context — reviewer comments, moderation state, and other staff-only fields are stripped from every response.

Writes stay deliberately narrow: the progress endpoint only toggles the done state of checkpoints, deliverables, and next steps that already belong to the caller's plan. It cannot reach any of the records or fields listed above.

## V1 Limitations

Version 1 cannot create plans, delete plans, or share plans, and it cannot read cohort teammates' plans. It can edit narrative fields, weeks, checkpoints, deliverables, next steps, resources, and week notes on your own plans, list plans, get one owned plan, download owned plan Markdown, and update progress on existing owned plan items. A dedicated bulk-reorder endpoint is deferred; reorder by setting `position`.

## Events

Every valid member key can discover accessible sessions, read event details, and register its owner:

```text
GET  /member-api/v1/events?filter=upcoming
GET  /member-api/v1/events/{event_id}
POST /member-api/v1/events/{event_id}/register
```

Event routes use the immutable integer event ID. Registration is self-only: there is no email, user, guest, or attendee target. For a session in a series, omitting the request field `scope` registers for the series by default; send `{"scope":"event"}` for only that session. Here `scope` chooses the registration target, not key permissions.

External events return their public host-platform registration URL and do not create an in-app registration. Event responses include only aggregate `attendee_count`; they never return a roster, attendee identities, email addresses, meeting IDs, or raw community-event provider URLs.
