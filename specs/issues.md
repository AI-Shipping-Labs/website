# Issue Backlog

Issues to create on GitHub as we progress through milestones. Issue #1 (M1: scaffold + reproduce site) is already created.

## M2: Auth + Tiers + Payments

### User registration and login

**Spec refs:** 01, 03

**Tasks:**
- Create User model extending AbstractUser with tier FK, stripe_customer_id, subscription_id, billing_period_end
- Create Tier model and seed 4 tiers (Free/Basic/Main/Premium) via data migration
- Build registration page (`/register`) — email + password
- Build login page (`/login`) and logout
- Build account page (`/account`) showing current tier, subscription status
- Add login/register links to header (when anonymous) and account/logout (when authenticated)

**Acceptance criteria:**
- [ ] User can register with email + password
- [ ] User can log in and log out
- [ ] After registration, user is assigned Free tier by default
- [ ] Account page shows current tier info
- [ ] Header shows appropriate links based on auth state
- [ ] `[HUMAN]` Registration email flow works end-to-end (if email verification is added)
- [ ] `[HUMAN]` Login page renders correctly and is user-friendly
- [ ] All tests pass, coverage 85%+

### Stripe checkout and webhooks

**Spec refs:** 02

**Tasks:**
- Build pricing page (`/pricing`) with Stripe checkout buttons
- Implement Stripe Checkout session creation (redirect to Stripe)
- Implement webhook handler for: checkout.session.completed, customer.subscription.updated, customer.subscription.deleted, invoice.payment_failed
- Build checkout success page (`/checkout/success`)
- Update user tier on successful payment

**Acceptance criteria:**
- [ ] Pricing page shows all tiers with correct prices
- [ ] Clicking "Subscribe" redirects to Stripe Checkout
- [ ] `[HUMAN]` Complete a test payment in Stripe test mode and verify tier updates
- [ ] `[HUMAN]` Verify Stripe webhook fires and updates user tier in database
- [ ] Webhook validates Stripe signature
- [ ] Checkout success page displays correctly
- [ ] All tests pass, coverage 85%+

### Access control middleware

**Spec refs:** 03

**Tasks:**
- Add `required_level` field to all content models (Article, Recording, Project, Tutorial, CuratedLink)
- Create access control middleware or decorator: check `user.tier.level >= content.required_level`
- Gated content shows teaser + upgrade CTA (never 404)
- Add visibility dropdown to Django admin for all content types

**Acceptance criteria:**
- [ ] Content with `required_level > 0` shows teaser to anonymous/insufficient-tier users
- [ ] Content with `required_level > 0` shows full content to users with sufficient tier
- [ ] Gated pages never return 404 — always show teaser + CTA
- [ ] Admin has visibility dropdown for all content types
- [ ] `[HUMAN]` Log in as different tier users and verify correct content gating
- [ ] All tests pass, coverage 85%+

## M3-M9 (to be detailed later)

Issues for milestones 3-9 will be created as we approach those milestones. See `PROCESS.md` for milestone overview and `specs/` for full requirements.
