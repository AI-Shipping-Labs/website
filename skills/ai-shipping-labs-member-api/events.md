# Member API — Events

Supporting reference for the Events family of the member API. Load
[`SKILL.md`](SKILL.md) first for shared authentication and safety rules.

## Endpoints

```text
GET  /member-api/v1/events?filter=upcoming
GET  /member-api/v1/events?filter=past&page=2
GET  /member-api/v1/events/{event_id}
POST /member-api/v1/events/{event_id}/register
```

Routes use the immutable integer `event_id`; the response `slug` is metadata
for the canonical website URL.

The list defaults to upcoming sessions and returns 20 per page. It omits
internal events above the member's tier, while externally hosted events remain
discoverable because their host platform controls access.

## Self-registration

Registration always targets the key owner. Never send an email, user ID,
attendee, or guest target.

For a session in an event series, an omitted request field `scope` registers
for the whole series and fans out to eligible upcoming sessions. Use this body
to register only for the selected session:

```json
{"scope": "event"}
```

Here `scope` means registration target (`series` or `event`), not a key
permission. Standalone events always create one event registration.

An external event returns `external_registration_required` with its public
host-platform URL and never creates an in-app registration.

## Privacy boundary

Responses may include the aggregate `attendee_count`, public hosts and
instructors, and the caller's own registration state. They never include a
roster, attendee identities, email addresses, meeting IDs, raw community-event
provider URLs, CRM data, or staff fields. During the live join window, a
registered member may receive only the canonical AI Shipping Labs join route.

## Errors

- `401`: missing, malformed, or revoked member key.
- `403`: the member's tier cannot access an internal event.
- `404`: unknown, draft, or retired event.
- `409`: closed, duplicate, or externally hosted registration.
- `422`: invalid filter, page, registration target, or unsupported body field.
