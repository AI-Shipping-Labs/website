# Member Plans API

Use the AI Shipping Labs member API to read and update your own sprint plans from local tools and agents.

Base URL:

```text
https://aishippinglabs.com/member-api/v1
```

Authentication:

```text
Authorization: Token <asl_member_...>
```

The key belongs to the signed-in member who created it. Plan endpoints only return or update that member's own plans.

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


## V1 Limitations

Version 1 cannot create plans, delete plans, share plans, edit narrative fields, or access cohort teammates' plans. It supports list plans, get one owned plan, download owned plan Markdown, and update progress on existing owned plan items.
