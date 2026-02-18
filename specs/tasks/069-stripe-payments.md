# 069 - Stripe Payments and Subscription Lifecycle

**Status:** pending
**Tags:** `payments`, `integration`
**GitHub Issue:** [#69](https://github.com/AI-Shipping-Labs/website/issues/69)
**Specs:** 02
**Depends on:** [068-membership-tiers](068-membership-tiers.md)
**Blocks:** [070-account-page](070-account-page.md), [082-community-slack](082-community-slack.md)

## Scope

- Stripe Checkout integration: redirect user to Stripe with the tier's price_id
- Webhook endpoint `POST /api/webhooks/payments` with signature validation
- Handle webhook events: checkout.session.completed, customer.subscription.updated, customer.subscription.deleted, invoice.payment_failed
- On purchase: set user tier, store stripe_customer_id and subscription_id, set billing_period_end
- Upgrade flow: prorate via Stripe, update tier on webhook
- Downgrade flow: schedule plan change at billing_period_end, set pending_tier_slug
- Cancellation: cancel at period end, revert to free on webhook
- Re-subscribe: same as purchase flow
- MoR consideration (Paddle/Polar) for VAT handling

## Acceptance Criteria

- [ ] `[HUMAN]` Clicking "Join" on a paid tier redirects to Stripe Checkout with the correct price_id (monthly or yearly based on toggle)
- [ ] `POST /api/webhooks/payments` validates Stripe webhook signature; returns 400 on invalid signature
- [ ] On `checkout.session.completed`: user.tier_slug updated to purchased tier, stripe_customer_id and subscription_id saved, billing_period_end set
- [ ] On `customer.subscription.updated` (plan change): user.tier_slug updated to new tier
- [ ] On `customer.subscription.deleted`: user.tier_slug set to "free"
- [ ] On `invoice.payment_failed`: email sent to user with payment update link; tier NOT revoked
- [ ] Downgrade schedules plan change at period end: user.pending_tier_slug set, user.tier_slug unchanged until billing_period_end
- [ ] Cancellation sets subscription to cancel at period end; user keeps access until billing_period_end
- [ ] `[HUMAN]` Re-subscribing after cancellation follows the same purchase flow and restores tier
- [ ] Webhook handler is idempotent: processing the same event twice does not corrupt data
