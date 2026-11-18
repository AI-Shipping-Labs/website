# Peer Reviews

Use these endpoints to list assigned project peer reviews, inspect the
submission and evaluation criteria, save drafts, and submit or update reviews.

## List Assigned Peer Reviews

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  "https://aishippinglabs.com/member-api/v1/peer-reviews?status=pending"
```

Optional query parameters:

- `course`: filter by course slug
- `project`: filter by project attempt slug
- `status`: `all` (default), `pending`, or `completed`
- `page`: 1-based page number (`page_size` is 20)

## Inspect Submission and Criteria

```bash
curl -sS \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  https://aishippinglabs.com/member-api/v1/peer-reviews/42
```

Returns the submission (`project_url`, `commit_id`, `description`,
`learning_in_public_links`, `time_spent`, `author_label`), current review state,
and `criteria` (each criterion has `id`, `title`, `description`, `type`
(`scale` or `checklist`), and `options` with `index`, `points`, `text`).

## Submit or Update a Peer Review

```bash
curl -sS -X PUT \
  -H "Authorization: Token $AI_SHIPPING_LABS_MEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "criteria_scores": {
      "readme": 2,
      "extras": [0, 1]
    },
    "feedback": "Clear README and solid test coverage."
  }' \
  https://aishippinglabs.com/member-api/v1/peer-reviews/42
```

- For `scale` criteria, pass the selected option `index` (integer).
- For `checklist` criteria, pass a list of selected option `index` integers.
- Pass `"draft": true` with `PATCH` to save partial progress before submitting.
- While the project's review window is open, `PUT` or `PATCH` can update an
  already-submitted review.
- Staff users may also authenticate with their admin API key against
  `/member-api/v1/*`.
