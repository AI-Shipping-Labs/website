# 070 - Account Page

**Status:** pending
**Tags:** `payments`, `auth`, `frontend`
**GitHub Issue:** [#70](https://github.com/AI-Shipping-Labs/website/issues/70)
**Specs:** 02, 03
**Depends on:** [069-stripe-payments](069-stripe-payments.md)
**Blocks:** —

## Scope

- `/account` page for authenticated users
- Show current tier name, billing period end date
- Upgrade button: links to Stripe Checkout for higher tier
- Downgrade button: shows tier selection, calls API to schedule downgrade
- Cancel button: calls API to cancel subscription at period end
- Show pending downgrade/cancellation status if scheduled
- Email preferences toggle (subscribe/unsubscribe from newsletters)

## Acceptance Criteria

- [ ] `GET /account` for logged-in user shows: tier name, tier level badge, billing_period_end date (formatted)
- [ ] Free users see "Upgrade" button linking to /pricing; no downgrade or cancel buttons
- [ ] Paid users see "Upgrade" button (if not already Premium), "Downgrade" button (if not already Basic), and "Cancel" button
- [ ] Clicking "Upgrade" redirects to Stripe Checkout for the higher tier
- [ ] Clicking "Downgrade" shows tier selection and calls API to schedule downgrade at period end
- [ ] Clicking "Cancel" calls API to cancel subscription at period end; confirmation prompt before action
- [ ] If pending_tier_slug is set, page shows: "Your plan will change to {tier} on {billing_period_end}"
- [ ] If subscription is cancelled, page shows: "Your {tier} access ends on {billing_period_end}"
- [ ] Email preferences section with subscribe/unsubscribe toggle for newsletters
- [ ] `GET /account` while logged out returns redirect to login page
