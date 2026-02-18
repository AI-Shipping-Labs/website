# 067 - User Registration, Login, and Authentication

**Status:** pending
**Tags:** `auth`, `frontend`
**GitHub Issue:** [#67](https://github.com/AI-Shipping-Labs/website/issues/67)
**Specs:** 01 (user model), 02 (payment fields)
**Depends on:** [068-membership-tiers](068-membership-tiers.md)
**Blocks:** [069-stripe-payments](069-stripe-payments.md), [070-account-page](070-account-page.md), [085-email-ses](085-email-ses.md)

## Scope

- User model with email as primary identifier
- OAuth login via Google and GitHub (django-allauth or similar) — only auth method for now
- Login, logout
- OAuth users auto-verified (email_verified = true) since provider already verified the email
- User profile fields: email, email_verified, unsubscribed, email_preferences
- Payment-related fields on user: stripe_customer_id, subscription_id, tier_slug (FK), billing_period_end, pending_tier_slug
- Community-related fields: slack_user_id
- Admin can view/edit users in Django admin

### Deferred → [094-email-password-auth](094-email-password-auth.md)

- Email + password registration
- Password reset flow
- Email verification flow (send verification link, confirm)

## Acceptance Criteria

- [ ] `[HUMAN]` Clicking "Sign in with Google" completes OAuth flow and redirects to homepage as logged-in user
- [ ] `[HUMAN]` Clicking "Sign in with GitHub" completes OAuth flow and redirects to homepage as logged-in user
- [ ] First-time OAuth login creates a new User with email from provider and email_verified = true
- [ ] Repeat OAuth login with same email logs in the existing user (no duplicate)
- [ ] User.tier_slug defaults to "free" on creation and FK resolves to the Free tier
- [ ] User model includes all fields: email, email_verified, unsubscribed, email_preferences (JSON), stripe_customer_id, subscription_id, tier_slug (FK), billing_period_end, pending_tier_slug, slack_user_id
- [ ] Clicking "Log out" ends the session and redirects to homepage
- [ ] Visiting a protected page while logged out redirects to login page
- [ ] Django admin at /admin shows user list with columns: email, tier, email_verified, date joined
- [ ] `[HUMAN]` Login page renders with site header/footer and Tailwind styling, with Google and GitHub buttons
