# Event claim widgets (author guide)

Event widgets let an author drop a one-click claim button into synced
markdown content (workshops, articles, course units). A claim records an
event server-side and fires any matching outbound webhook subscription —
for example, the v0 credit campaign that emails a code. This is the
content-author surface of the `triggers` subsystem (issue #1070).

## Embedding a widget

Widgets are embedded with a fenced `eventwidget` shortcode, the same fence
style as a mermaid diagram. It is expanded at MARKDOWN RENDER TIME (not a
Django `{% %}` template tag), so it works inside any synced `.md` content
file:

```eventwidget
slug: v0-claim
```

The `slug:` value is the widget's slug from Studio (`Operations ->
Event widgets`). On render the shortcode is replaced by a placeholder
`<div class="event-widget" data-event-widget="v0-claim"></div>`; the
per-user state (claim button / sign-in CTA / already-claimed / paused) is
hydrated client-side at request time. The raw shortcode text never appears
on the published page.

Notes:

- The placeholder is user-agnostic and cached. Authors never see per-user
  copy in the markdown — all the copy (button label, body, sign-in CTA,
  claimed label) is configured on the widget row in Studio.
- An inactive or unknown slug renders nothing: no error, no leaked raw
  shortcode. Deactivating a widget in Studio makes every embed of it go
  blank instantly, with no content re-sync.
- The shortcode is dropped from email renders (an inbox can't run the
  hydration JS).

## Single-line note

Only the fenced form above is supported. Keep the `slug:` key on its own
line inside the fence.

## Available widgets and parameters

The list of live widgets is managed in Studio (`Operations -> Event
widgets`). Each widget row exposes:

| Field | Meaning |
| --- | --- |
| `slug` | The value used in the shortcode `slug:` line. |
| `event_name` | The event name passed to `emit_event` on claim (also the dedup key). |
| `min_level` | Minimum access level required to claim (server-enforced; default 5 = registered/any signed-in member). |
| `claim_label` | Button label in the claimable state. |
| `claim_body` | Supporting copy shown above the button. |
| `signin_cta` | Label shown to anonymous visitors (links to login). |
| `claimed_label` | Label shown once the user has claimed. |
| `exhausted_label` | Reserved for Phase 2 (waitlist-on-exhaustion). |
| `is_active` | Inactive widgets render nothing. |

The Studio widget screen shows the exact shortcode to copy for each
widget.

## How a claim is enforced (for reference)

The widget never talks to the external handler directly — Django is always
the trust boundary:

1. The browser GETs `/widgets/<slug>/state` to learn which state to show.
2. The claim POSTs `/widgets/<slug>/claim` (authenticated + CSRF). The
   server re-checks `min_level`, dedups on `(user, event_name)`, records an
   `EventEmission`, and dispatches matching subscriptions.
3. A duplicate claim is a no-op that returns the already-claimed state.

Claim POSTs are additionally limited per member and direct peer IP. A burst
over the limit returns `429` with a friendly wait-and-retry state; it never
records or dispatches an extra emission.

The global `TRIGGERS_ENABLED` flag (Studio settings, `Event triggers`
group) is the master switch. When off, claims short-circuit to a friendly
"claims are paused" state and nothing is recorded.

See `_docs/integrations/triggers.md` for the operator/integration side.
