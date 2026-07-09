# 2026-07-09 Zoom issue intake

Source: Zoom recording shared on 2026-07-09, duration 43:26.

Transcript source: generated locally during intake and intentionally not stored in the repo.

Notes:

- Zoom did not expose a native transcript, so the recording was downloaded and transcribed locally with the `~/git/zoom-calls` workflow.
- The local transcript is noisy in several places, but the product themes and requested follow-ups are clear enough for raw GitHub issue intake.
- The raw transcript and recording passcode are intentionally not stored here.
- Raw GitHub issues should stay labeled `needs grooming` until a PM pass turns them into agent-ready specs.

## Issue inventory

| # | Issue candidate | Transcript refs | Labels |
|---|---|---|---|
| 1 | Track GA4 signup funnel, manual GTM setup, and production-only analytics | 07:01-15:31, 20:27-20:49 | integration, enhancement, P1, human |
| 2 | Turn the workshops page into a visitor-facing landing page | 00:30-00:51, 02:52-03:18 | content, frontend, enhancement, P1 |
| 3 | Add paid/free filtering to the workshop catalog | 02:14-02:52 | content, frontend, enhancement, P2 |
| 4 | Add skill-level metadata and filtering to workshops | 02:25-02:52 | content, frontend, enhancement, P2 |
| 5 | Add tools/technology metadata and filtering to workshops | 02:25-02:52 | content, frontend, enhancement, P2 |
| 6 | Add topic/theme grouping or faceted browsing to workshops | 02:25-02:52 | content, frontend, enhancement, P2 |
| 7 | Add a landing-to-catalog "browse all workshops" flow | 02:52-03:18 | content, frontend, enhancement, P2 |
| 8 | Improve workshop cards so metadata and labels are clearer | 03:18-04:22 | content, frontend, enhancement, P2 |
| 9 | Rework workshop card images/banners that duplicate text or clash with theme | 04:22-05:33 | content, frontend, enhancement, P2 |
| 10 | Rewrite unclear workshop titles for public visitors | 05:03-06:05 | content, frontend, enhancement, P2 |
| 11 | Add or audit meta descriptions for workshop/content pages | 03:49-04:22 | content, seo, enhancement, P2 |
| 12 | Make signup analytics show actionable source and pre-signup activity | 08:32-10:01 | admin, frontend, enhancement, P1 |
| 13 | Separate newsletter-only signups from full account registrations in reporting | 10:23-10:53 | admin, frontend, enhancement, P1 |
| 14 | Add related content recommendations to improve low-engagement pages | 20:49-22:16 | content, frontend, enhancement, P2 |
| 15 | Restore visibility for the community launch landing page | 23:09-25:38 | community, content, frontend, enhancement, P1 |
| 16 | Build a community landing page around benefits and activities | 25:12-25:38, 31:19-33:07 | community, content, frontend, enhancement, P1 |
| 17 | Support standalone marketing pages/content blocks outside the event-recording taxonomy | 26:27-27:37 | content, admin, frontend, enhancement, P2 |
| 18 | Restore an activities-by-tier view for membership benefits | 29:54-31:46 | community, payments, frontend, enhancement, P1 |
| 19 | Make community sprint/activity entries clickable and self-explanatory | 33:07-34:53 | community, frontend, enhancement, P1 |
| 20 | Reorganize community navigation around membership, activities, events, and sprints | 34:53-37:46 | community, frontend, enhancement, P1 |
| 21 | Clarify the product taxonomy for events, past recordings, workshops, resources, and community activities | 23:09-28:32, 35:23-37:46 | content, events, community, enhancement, P1 |
| 22 | Decide where past event recordings belong in navigation | 23:09-24:44, 35:23-37:17 | events, content, frontend, enhancement, P2 |
| 23 | Run an AI UX review pass over public navigation and landing surfaces | 38:26-38:54 | frontend, content, enhancement, P2 |
| 24 | Turn interview-derived segments into a content plan for mini-courses and workshops | 38:54-41:36 | content, courses, enhancement, P2 |

## Created GitHub issues

- #1164: Track GA4 signup funnel, manual GTM setup, and production-only analytics
- #1165: Turn the workshops page into a visitor-facing landing page
- #1166: Add paid/free filtering to the workshop catalog
- #1167: Add skill-level metadata and filtering to workshops
- #1168: Add tools and technology metadata/filtering to workshops
- #1169: Add topic/theme grouping or faceted browsing to workshops
- #1170: Add a landing-to-catalog browse-all-workshops flow
- #1171: Improve workshop cards so metadata and labels are clearer
- #1172: Rework workshop card images and banners that duplicate text or clash with theme
- #1173: Rewrite unclear workshop titles for public visitors
- #1174: Add or audit meta descriptions for workshop and content pages
- #1175: Make signup analytics show actionable source and pre-signup activity
- #1176: Separate newsletter-only signups from full account registrations in reporting
- #1177: Add related content recommendations to improve low-engagement pages
- #1178: Restore visibility for the community launch landing page
- #1179: Build a community landing page around benefits and activities
- #1180: Support standalone marketing pages outside the event-recording taxonomy
- #1181: Restore an activities-by-tier view for membership benefits
- #1182: Make community sprint and activity entries clickable and self-explanatory
- #1183: Reorganize community navigation around membership, activities, events, and sprints
- #1184: Clarify product taxonomy for events, past recordings, workshops, resources, and community activities
- #1185: Decide where past event recordings belong in navigation
- #1186: Run an AI UX review pass over public navigation and landing surfaces
- #1187: Turn interview-derived segments into a content plan for mini-courses and workshops

## GA issue next steps captured in #1

The GA discussion specifically called for a follow-up issue that includes next steps. That issue should cover:

1. Verify the intended GA/GTM sign-up funnel design: source page, sign-up/OAuth click, OAuth return, completed registration, and newsletter-only versus full account registration.
2. Check whether the existing `aslab_aid` and `gtag_pending_event` implementation covers OAuth return attribution end to end, including Google OAuth redirects.
3. Add missing site-side event/state handoff if the pre-OAuth session/source is not connected to the completed sign-up event.
4. Add or document required GTM/GA4 tags, triggers, outbound/OAuth handling, and operator-facing report dimensions/events.
5. Make the logged-in versus anonymous distinction explicit in GA reporting.
6. Confirm production-only analytics behavior so local/dev/staging does not pollute the production GA property.
7. Update `_docs/integrations/analytics.md` after the implementation/configuration decision.
8. Add regression coverage for OAuth sign-up completion and disabled GA in non-production/test settings.
