# 02 - Payments

## Overview

Stripe handles payments. A Merchant of Record (Paddle or Polar) handles VAT so we don't file it ourselves.

## Flow

### Purchase

1. User clicks "Join" on a tier from `/pricing` or from any upgrade CTA
2. Redirect to Stripe Checkout (or Paddle/Polar checkout) with the tier's `price_id`
3. On successful payment, MoR/Stripe sends a webhook to `POST /api/webhooks/payments`
4. Webhook handler:
   a. Validates webhook signature
   b. Looks up user by email (or creates a new user if not registered)
   c. Sets `user.tier_slug` to the purchased tier
   d. Sets `user.subscription_id` to the MoR subscription ID
   e. Sets `user.billing_period_end` to the subscription's current period end date
   f. Triggers onboarding actions (Slack invite if Main+, see spec 09)

### Upgrade

1. User clicks "Upgrade" from their account page `/account`
2. Redirect to MoR checkout with the new tier's `price_id` and existing `customer_id`
3. MoR prorates: charges the price difference for the remaining billing period
4. Webhook fires → update `user.tier_slug` to the new tier
5. If upgrading to Main+ and user didn't have community access before → trigger Slack invite

### Downgrade

1. User clicks "Downgrade" from `/account`, selects a lower tier
2. API call to MoR to schedule a plan change at the end of the current billing period
3. `user.tier_slug` stays the same until `billing_period_end`
4. When `billing_period_end` is reached, MoR sends a webhook → update `user.tier_slug` to the lower tier
5. If downgrading from Main+ to Basic/Free → schedule Slack deactivation at `billing_period_end` (see spec 09)

### Cancellation

1. User clicks "Cancel" from `/account`
2. API call to MoR to cancel subscription at the end of billing period
3. User keeps access until `billing_period_end`
4. At `billing_period_end`, MoR sends webhook → set `user.tier_slug = "free"`
5. If was Main+ → deactivate Slack access (see spec 09)

### Re-subscribe

1. Former paid user clicks "Join" again from `/pricing`
2. Same flow as Purchase
3. If tier is Main+ → re-activate Slack access (see spec 09)

## Data Model

```
User (payment-related fields):
  stripe_customer_id: string | null
  subscription_id: string | null
  tier_slug: FK -> Tier
  billing_period_end: datetime | null    # null for free users
  pending_tier_slug: FK -> Tier | null   # set when downgrade is scheduled
```

## Webhook Events to Handle

| Event | Action |
|---|---|
| `checkout.session.completed` | Create/update user, set tier, trigger onboarding |
| `customer.subscription.updated` | Update tier if plan changed, update `billing_period_end` |
| `customer.subscription.deleted` | Set `tier_slug = "free"`, deactivate Slack if applicable |
| `invoice.payment_failed` | Send email warning, no immediate tier change |

## Requirements

- R-PAY-1: Implement `POST /api/webhooks/payments` endpoint that validates signatures and handles the four events above.
- R-PAY-2: Store `stripe_customer_id` and `subscription_id` on the user record after first purchase.
- R-PAY-3: The `/account` page shows current tier, billing period end date, and buttons for Upgrade/Downgrade/Cancel.
- R-PAY-4: On upgrade, prorate the charge for the remaining billing period (handled by MoR, backend just processes the webhook).
- R-PAY-5: On downgrade or cancellation, keep current tier access until `billing_period_end`. Do not revoke access immediately.
- R-PAY-6: On `invoice.payment_failed`, send an email to the user with a link to update payment method. Do not revoke access until subscription is actually deleted by MoR.
- R-PAY-7: Use Paddle or Polar as MoR so VAT is collected and remitted by them. We never file VAT ourselves.
