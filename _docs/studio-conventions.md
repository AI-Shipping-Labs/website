# Studio Display Conventions

## Pills vs Plain Text

Pills are rounded background chips with coloured text. Use them for short,
enumerated state values where the operator's eye should land on the value
first. Use plain text for free-form data such as email, name, IDs, and dates.

A field renders as a pill when all of the following are true:

1. It belongs to a closed enumeration, such as Free / Basic / Main / Premium; Active / Inactive / Staff; Subscribed / Unsubscribed; Member / Not in Slack / Never checked; Override / From Stripe / Default.
2. The set of values is small enough to colour-code distinctly.
3. The value is a state, not an identifier.

Provenance and qualifier badges, such as Override, From Stripe, Default, and
Trial, render as separate small pills next to the primary value pill. They do
not render as parenthesised suffixes inside the value.

Example screenshot description: in a Studio membership row, the tier appears as
a coloured `Premium` pill. If a temporary override is active, an amber
`Override` pill with the shield icon sits beside it. The row never reads
`Premium (override)`.

## Canonical Pill Colours

| Field | Value | Tailwind classes |
|---|---|---|
| Tier | Free | `bg-muted text-muted-foreground` |
| Tier | Basic | `bg-blue-500/20 text-blue-300` |
| Tier | Main | `bg-accent/20 text-accent` |
| Tier | Premium | `bg-amber-500/20 text-amber-300` |
| Tier source | Override | `bg-amber-500/20 text-amber-300 border border-amber-500/30` plus the shield icon |
| Tier source | From Stripe | `bg-muted text-muted-foreground` |
| Tier source | Default | `bg-muted text-muted-foreground` |
| User status | Active | `bg-green-500/15 text-green-300` |
| User status | Staff | `bg-blue-500/15 text-blue-300` |
| User status | Inactive | `bg-red-500/15 text-red-300` |

Newsletter and Slack pills keep their existing colours.
