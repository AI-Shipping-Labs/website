# Logged-in dashboard audit captures

Locator: `sources/raw/logged-in-dashboard-desktop-1280.png` and `sources/raw/logged-in-dashboard-mobile-393.png`
Captured: 2026-08-11
Authority: primary local render

## Retrieval summary

Clean full-page dark-theme captures of the authenticated `/` route for a local active Main member with course/workshop progress, sprint participation, and visible community modules.

## Important claims

- [FACT logged-in-dashboard-render] The desktop render is 1280 x 1,988 pixels.
- [FACT logged-in-dashboard-render] The mobile render is 393 x 4,069 pixels.
- [FACT logged-in-dashboard-render] The desktop view presents Continue learning, Upcoming events, six Quick actions, Slack, Sprints and cohorts, Recent content, and Active polls as multiple bordered modules.
- [FACT logged-in-dashboard-render] On mobile, the same modules become a single long stack, and the six Quick actions consume six consecutive cards.
- [INFERENCE logged-in-dashboard-render] The current design provides many destinations but weak guidance about the member's most important next action.

## Limitations

- [FACT logged-in-dashboard-render] The captures use the local `audit-active@test.com` Main-member state and do not represent every Free, new-member, Premium, or fully-onboarded branch.
- [FACT logged-in-dashboard-render] Dynamic event, sprint, content, and poll rows reflect local data on 2026-08-11.

## Related pages

- [FACT logged-in-dashboard-render] [Overview](../overview.md)
- [FACT logged-in-dashboard-render] [Synthesis](../synthesis.md)
- [FACT logged-in-dashboard-render] [Logged-in concept](../notes/logged-in-dashboard-concept.md)
