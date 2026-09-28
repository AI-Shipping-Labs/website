# Event detail session sheet

Scope: the relationship block and the registration block on `/events/<id>/<slug>`, plus public description copy on the event page and the series page. The rest of the site keeps its current chrome.

## Attack

Claims about the surface as it rendered on Buildcamp office hours, session 2:

1. Inter is the only typeface, and both blocks use the same `text-lg font-semibold` heading, so nothing on the page has a scale jump.
2. "Part of" and "Register for this session" are the same widget: `rounded-lg border border-border bg-card p-6`, stacked with the same margin. A membership line and a decision have equal weight.
3. "Part of" is followed by "Explore the series and programs connected to this session." The sentence repeats the heading.
4. The series destination is a list row: calendar icon, truncated label, chevron. One link is dressed as a navigation widget.
5. The registration row is `sm:justify-between`. The label pins left, the lime button pins right, and the space between them is empty.
6. "Just this session" sits in a second row with `justify-end`, in accent green, so it reads as a second primary action hanging under the button.
7. The scope sentence repeats the series name already shown in the card above, and it sits below both actions.
8. The only accent is used twice: the filled button and the text button. There is no quiet alternative.
9. The public description on this session is an operator note (`Hidden series: ...`), set in the same muted prose as real copy. The course reader already strips that note. The event page did not.

## Direction

A session sheet. The column is a document. Rules separate regions. Type does the hierarchy. One filled accent button is the only loud control.

This direction says no to a second card, no to an explainer under "Part of", no to icon rows for a single link, and no to splitting a label and a button across a box.

## Type

The site face stays Inter. Identity on this surface comes from case, tracking, and size.

| Role | Treatment |
| --- | --- |
| Kicker ("Part of") | `text-xs font-medium uppercase tracking-widest text-muted-foreground` |
| Series link | `text-xl font-medium tracking-tight text-foreground`, wraps, underline on hover |
| Decision title | `text-2xl font-semibold tracking-tight text-foreground`, left aligned, full width |
| Scope and tier notes | `text-sm leading-relaxed text-muted-foreground`, `max-w-xl`, before the actions |
| Primary action | Existing product button, `primary`, size `lg` |
| Alternate action | `text-sm font-medium text-foreground underline underline-offset-4`, no fill, no accent |

## Color

Use the existing dark tokens. Do not add a wash, a tint, or a second accent.

| Token | Value |
| --- | --- |
| background | `hsl(0 0% 4%)` |
| foreground | `hsl(0 0% 98%)` |
| muted-foreground | `hsl(0 0% 72%)` |
| border | `hsl(0 0% 28%)` |
| accent | `hsl(75 100% 50%)` |
| accent-foreground | `hsl(0 0% 4%)` |

Accent fill is allowed on the primary registration button only. The alternate action is foreground.

## Radius, border, shadow, spacing

- Relationship and registration regions: radius 0, no shadow, no background fill.
- Each region opens with `border-t border-border` and `pt-8`, and keeps `mb-12` so it stays in the reader rhythm.
- Product buttons keep `rounded-md`. That radius belongs to controls, not to these regions.
- Actions are a left-aligned column (`flex flex-col items-start gap-3`), including at desktop width.
- Several relationships stack with `divide-y divide-border`. No chevron, no marker icon.

## Components

- The accessible name stays "Part of" on the `h2`, and the nav keeps `aria-label="Related event context"`.
- Registration test ids, button ids, `data-register-scope`, and `data-event-register-button` stay.
- The scope note renders above the buttons.
- Public descriptions pass through `strip_internal_description_notes` before render. A description that is only an operator note renders nothing. A real sentence before that note stays.

## Banned on this surface

- `rounded-lg border bg-card` around "Part of" or registration
- Explainer line under "Part of"
- Lucide marker plus chevron for a relationship link
- `justify-between` or `justify-end` placing the registration actions
- Accent color on "Just this session"
- Rendering `Hidden series:`, `Internal note:`, `Operator note:`, `Staff note:`, `Registration operations:`, or `Registration setup:` as public copy
