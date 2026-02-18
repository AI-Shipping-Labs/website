# 086 - Newsletter Signup and Lead Magnets

**Status:** pending
**Tags:** `email`, `frontend`
**GitHub Issue:** [#86](https://github.com/AI-Shipping-Labs/website/issues/86)
**Specs:** 10 (newsletter + lead magnet sections)
**Depends on:** [085-email-ses](085-email-ses.md)
**Blocks:** —

## Scope

- `POST /api/subscribe` endpoint: creates free user or is idempotent for existing, sends verification email
- Verification: `GET /api/verify-email?token={jwt}` sets email_verified = true (JWT with user_id, 24h expiry)
- Unsubscribe: `GET /api/unsubscribe?token={jwt}` sets unsubscribed = true (JWT, no expiry)
- Subscribe form on: site footer, `/subscribe` page, article CTAs
- Lead magnet flow: subscribe with redirect_to param, verification email includes download link, on verify redirect to file download
- Every email includes unsubscribe link in footer
- `/account` page toggle for re-subscribing

## Acceptance Criteria

- [ ] `POST /api/subscribe`: if new email, creates user with tier=free and sends verification email; if existing email, returns 200 with same message (no information leak)
- [ ] `GET /api/verify-email?token={jwt}`: validates JWT (user_id, 24h expiry), sets email_verified = true
- [ ] `GET /api/unsubscribe?token={jwt}`: validates JWT (no expiry), sets unsubscribed = true
- [ ] Subscribe form appears in: site footer, `/subscribe` dedicated page, article CTA sections
- [ ] Lead magnet flow: `POST /api/subscribe` with redirect_to param → verification email includes download link → on verify, redirects to file download
- [ ] `/account` page includes toggle to re-subscribe if previously unsubscribed
- [ ] Every outgoing email includes unsubscribe link in footer
