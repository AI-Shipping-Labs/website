# Guest UX / Conversion Funnel Audit

Date: 2026-07-09
Scope: anonymous visitor -> free signup -> paid upgrade funnel. Evidence: production screenshots of 23 guest-facing pages (1440px desktop + 390px mobile) and code-level trace of gating, registration, checkout, and email flows.

## Jobs to be done for guests

| Job | Served by | State |
|-----|-----------|-------|
| Learn AI engineering practically | Blog, workshops, tutorials, curated links | Well served, fully crawlable |
| Ship my project with structure and accountability | Sprints, plans, Slack (paid value prop) | Nearly invisible to guests until they hit a paywall |
| Get an AI engineering job | Mock interviews, CV events, teardowns | Strong hook, only discoverable via events list |
| Sample before paying | Free events, AI Hero course, newsletter, recordings | Well served |

## Funnel mechanics as implemented

- Access logic: `content/access.py` — anonymous = level 0; `can_access()` at L171-221; CTA copy built at L367-390.
- Paywall partial: `templates/includes/content_gated.html` — teaser (max 200 chars), decorative blur, one CTA button "View Pricing".
- Header (`templates/includes/header.html`): guests get a single "Sign in" button; zero signup/register links, desktop and mobile.
- Homepage tiers section (`templates/home.html` L101-201): Basic/Main/Premium only, no Free card; "Join" buttons are raw Stripe payment links.
- Pricing (`payments/views/pricing.py`): Free card renders an inline register form (good); paid cards are Stripe payment links; toggle defaults to Annual.
- Registration (`accounts/views/auth.py:306-375` + `static/js/accounts/inline-register.js:58-96`): email/password, AJAX, does NOT auto-login; shows success message and requires manual sign-in. Verification email sent but verification is not enforced for login (`website/settings.py:516-524`, `ACCOUNT_EMAIL_VERIFICATION = 'none'`).
- OAuth (Google/GitHub/Slack) on both login and register pages; OAuth users are auto-verified and receive no emails at all.
- Event registration for guests (`events/views/api.py:146-299`): email-only form that auto-creates a free account. Best conversion pattern on the site.
- Guest Stripe payment with unknown email: webhook auto-creates + verifies the account (`payments/services/webhook_handlers.py:95-107`).
- Slack for new Main members: direct channel-add if email already in workspace, otherwise emailed self-serve `/community/slack` link.
- Free-member email lifecycle: verification email only (password signups), then broadcast newsletters. No welcome email, no drip, no nurture.
- Free-member dashboard: all empty states, no onboarding prompt (gated to Basic+ at `content/views/home.py:349-357`), no upgrade CTA on the page.

## Findings

### P0 — direct conversion blockers

1. Signing up makes access worse than staying anonymous. `content/access.py:188-193` allows anonymous users level-0 content but blocks logged-in unverified free users from the same content. Email registration leaves users unverified, so the reward for creating an account is losing access until they verify.
2. No signup CTA in the persistent chrome. `templates/includes/header.html` has only "Sign in" for guests. The register page (with Google/GitHub/Slack) is reachable only via the pricing Free card, gated workshop cards, or the login page. Add a "Join free" header button.
3. Paywall speaks to members, not guests. Anonymous visitors on tier-gated content see "Upgrade to [Tier] to read this article" with a single "View Pricing" button (`content/access.py:367-390`, `templates/includes/content_gated.html:37-48`). No free-account middle step, no email capture. The workshop registered-level gate already shows the right dual CTA ("Sign In" / "Create a free account", `content/views/workshops.py:230-244`) — extend that pattern to all tier-gated surfaces for anonymous users.
4. Registration does not log the user in. Inline success message plus manual sign-in step at the moment of highest intent; also tells users to verify although verification is not enforced. Auto-login on register and route to a concrete next step.

### P1 — free-member activation gap

5. No welcome email for free signups; OAuth signups receive zero emails. No nurture sequence exists (`accounts/signals.py`, `email_app/services/email_classification.py:104-116`). Free members only get broadcast newsletters.
6. New free-member dashboard is a stack of empty states with no getting-started checklist and no upgrade CTA. Onboarding prompt is hidden from Free members. Suggested: welcome email, 3-item dashboard checklist (start AI Hero, register for a free event, read the sprint intro), plan/sprint teaser card routing to `/pricing`.
7. Homepage skips both the free path and the paid story. No Free tier card in the tiers section; sprints (the differentiator) appear nowhere on the homepage; upcoming events (best free sampling hook) are absent — only past recordings are shown. The sprint announcement article sells the community better than the homepage.
8. Lead magnets missing and mis-flowed. `/downloads` is linked in nav but empty ("No downloadable resources yet"). When populated, free downloads say "Enter your email to download for free" but route to the full signup page (`templates/content/downloads_list.html:96-105`) instead of the event-style inline email capture.
9. Social proof anti-sells. Event pages show "1 person is going" / "Be the first to sign up" — suppress below a threshold. Homepage testimonials are explicitly from a prior course, not the community; replace with sprint-member quotes as they accumulate.

### P2 — polish and trust erosion

10. Stale/broken content on trust-sensitive pages: `/activities` lists the May 2026 sprint as ACTIVE in July; `/resources` renders escaped quotes (`\"A Day of an AI Engineer\"`) and truncated card descriptions ("Assignments for", "Curated resource for").
11. Pricing anchors high: Annual default shows EUR 200/500/1000 as the first numbers. Consider monthly default or per-month framing of annual prices.
12. Gated events sell themselves with one line ("We will come up with a topic and implement it." on a Main-gated workshop). Link past freestyle recordings on the page as evidence.
13. Pricing buried one level deep in nav (Community -> Membership). Make Membership/Pricing a top-level nav item.
14. Minor: `/subscribe` stacks two identical newsletter forms; email-verification result page offers only "Sign In" instead of prompting newsletter-only subscribers to set a password inline; mobile pricing opens on the Main card in a horizontal scroll with weak affordance that other tiers exist.

## Patterns that work — keep and replicate

- Event email-only registration with account auto-creation (`events/views/api.py:146-299`).
- AI Hero course landing page: syllabus + testimonials + inline social signup gate.
- Workshop teaser structure: overview visible, locked step list, tiered recording gate, upgrade card.
- Payment-first path safety: webhook auto-creates/verifies accounts for unknown Stripe emails.

## Related

A separate visual/design audit of the same surfaces is tracked in the design audit doc for this date (if present) and its issues.
