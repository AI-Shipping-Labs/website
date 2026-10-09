# Skill: ai-shipping-labs-zoom-resync

# Sync Rescheduled Zoom Meetings to the Platform

Use when the user reschedules Zoom meetings and wants the platform updated to match.

## Prerequisites Skills

- `ai-shipping-labs-prod-api` — auth, safe-write protocol, `asl` CLI
- `ai-shipping-labs-events` — event CRUD, series management

## Steps

1. List event series (`asl event-series list`) and identify the target series.
2. Get current platform state (`asl event-series get <id>`) — record each event's `start_datetime`, `end_datetime`, `slug`.
3. Get current Zoom state:

```bash
uv run python -c "
from integrations.services.zoom import get_access_token
import requests
token = get_access_token()
resp = requests.get('https://api.zoom.us/v2/users/me/meetings',
    headers={'Authorization': f'Bearer {token}'},
    params={'type': 'upcoming', 'page_size': 50})
for m in resp.json().get('meetings', []):
    print(m.get('id'), '|', m.get('topic'), '|', m.get('start_time'))
"
```

4. Match Zoom meetings to platform events by meeting ID (from `zoom_join_url`) or topic name. Diff `start_datetime` vs `start_time`.
5. For each mismatch, update the event (safe-write: GET before, PATCH, GET after):

```bash
uv run asl events update <slug> --start-datetime "<new_start>" --end-datetime "<new_end>"
```

6. Prepend a move note to each rescheduled event's description:

```bash
uv run asl events update <slug> --description "Note: This session was moved from <orig_day> <orig_date> to <new_day> <new_date> due to <reason>.

<original_description>"
```

7. Re-fetch each changed event and confirm `start_datetime` matches Zoom and the description contains the move note.

## Notes

- Only Studio/API-origin events are editable (`editable: true`).
- Zoom returns UTC times (ISO 8601 with `Z`); convert to platform format (`+00:00`).
- Unmatched Zoom meetings or platform events are reported, never auto-created or deleted.
- Run API calls sequentially; never fan out against production.
