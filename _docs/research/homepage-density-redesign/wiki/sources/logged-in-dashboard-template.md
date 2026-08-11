# Current logged-in dashboard implementation

Locator: `/home/alexey/git/ai-shipping-labs/templates/content/dashboard.html` and `/home/alexey/git/ai-shipping-labs/content/views/home.py`
Captured: 2026-08-11
Authority: primary repository source

## Retrieval summary

The authenticated dashboard template and view define learning progress, events, six tier-aware quick actions, onboarding and plan guidance, Slack, sprint opportunities, recent content, and polls.

## Important claims

- [FACT logged-in-dashboard-template] Continue learning and Upcoming events form the first peer two-column grid.
- [FACT logged-in-dashboard-template] Quick actions contains six descriptive cards before plan/Slack/sprint guidance.
- [FACT logged-in-dashboard-template] Recent content can show five rows and Active polls up to two cards.
- [FACT logged-in-dashboard-template] Book Club is absent from authenticated dashboard context and rendering even when a current book exists.
- [INFERENCE logged-in-dashboard-template] A Book Club tile added to Quick actions alone would increase destination density without supporting continuity.

## Limitations

- [FACT logged-in-dashboard-template] Many modules are conditional, so the exact dashboard composition varies by tier, onboarding, plan, Slack, registration, and content state.
- [OPEN] The implementation consequences of adding Book Club context have not yet been specified or approved.

## Related pages

- [FACT logged-in-dashboard-template] [Overview](../overview.md)
- [FACT logged-in-dashboard-template] [Synthesis](../synthesis.md)
