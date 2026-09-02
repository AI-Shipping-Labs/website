---
name: ai-shipping-labs-event-recaps
description: Create and publish an AI Shipping Labs event recap from a YouTube recording, verify the anonymous recap page and canonical links, then explicitly notify the exact event registrants. Use when an operator provides a recording and asks to add, publish, or announce an event recap; do not use for generic event editing without a recap.
---

# AI Shipping Labs event recaps

Use this workflow for a recording-to-recap operation. Read
`$ai-shipping-labs-events` for event ownership and `$ai-shipping-labs-prod-api`
for production authentication and safe writes. When a YouTube URL is
provided, compose with `$fetch-youtube` and fetch the transcript once.

## 1. Resolve the exact event

- Get the event with `uv run asl events get <slug>` or the production API.
- Confirm the event title, occurrence date, stored recording URL, origin, and
  `editable` flag. Match the supplied YouTube URL to the exact recording.
- If more than one event matches, or the recording is not clearly associated,
  stop and ask for the event slug. Never guess from a similar title.
- Do not expose attendee lists, transcript dumps, or other personal data in
  the recap.

## 2. Fetch and draft the recap

- Read and follow `$fetch-youtube` for the supplied URL. Fetch once with its
  cache-only path workflow, retaining the returned path for the current task:

  ```bash
  uv run --with youtube-transcript-api --with python-dotenv \
    ~/.claude/skills/fetch-youtube/youtube.py <youtube-url-or-id> --path
  ```

  Read that cache file in bounded chunks when needed. Do not run the fetch
  twice, use command substitution or a pipe that puts the full transcript in
  shell history, print the transcript into chat, or copy it to `/tmp`; the
  `~/.cache/youtube_transcripts/<video-id>.txt` file is the working copy.
- Draft factual Markdown from the transcript in
  `.tmp/event-recaps/<event-slug>.md`. Keep the file local and out of the
  content repository unless the event is GitHub-origin. Do not add YAML
  frontmatter, invented claims, unsupported speaker quotes, or attendee PII.
- Prefer a concise title, what was covered, useful takeaways, and recording
  chapters only when the timestamps are supported by the recording.

## 3. Publish through the event’s source of truth

For a Studio/API-origin event, use the API-backed CLI. The file option performs
GET-before, PATCH, and GET-after verification and checks source ownership:

```bash
uv run asl events update <slug> \
  --recap-notes-file .tmp/event-recaps/<event-slug>.md
uv run asl events get <slug>
```

Confirm the read-back has the same event identity, `editable: true`,
`has_recap: true`, a non-empty `recap_url`, and the expected
`recap_notes`. A past, public, published event should also report
`recap_published: true`. Do not PATCH a GitHub-origin event; update its
`recap_file` in `AI-Shipping-Labs/content`, push that source change, and run
the normal content sync, then repeat the event GET verification. Honor a
source-sync conflict instead of overwriting newer content.

## 4. Verify the public result

- Open the anonymous `recap_url`, not merely the event detail page.
- Confirm the recap renders correctly, the event title/date context is right,
  the recording and chapter links work, and the page’s event/recap links point
  to the canonical occurrence URLs.
- Confirm the event detail page’s recap CTA leads to the same dedicated
  `recap_url`. If the event is draft, unpublished, cancelled, not public, or
  not ended, do not announce it; fix the state or report the exact blocker.

## 5. Explicitly notify registrants

Saving the recap, syncing content, publishing an event, and elapsed time never
send this message automatically. Only perform this step when the operator’s
request includes the announcement or explicitly approves it after verification:

```bash
uv run asl events notify-recap-ready <slug>
```

The action is staff-only and targets active registrations for this exact event
occurrence. It sends the direct absolute recap link by transactional email and
creates an in-app notification. Newsletter unsubscribe does not suppress this
registration-related notice; normal invalid-address and provider bounce/complaint
protections still apply. Hosts, series-only members, sibling occurrences, and
unrelated event registrants are not added to the audience.

Read the returned `eligible`, `emailed`, `notified`, `already_sent`,
`skipped_inactive`, and `failed` counts. A partial failure does not justify a
manual resend: rerun the same command to retry only missing channels. If the
result is ambiguous, inspect the delivery/readiness state before retrying.

## Completion report

Report the event title and slug, canonical `recap_url`, verification result,
notification counts, and any failed or skipped channels. Do not include the
full transcript, attendee email addresses, message bodies, or provider IDs.
