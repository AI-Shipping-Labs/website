---
subject: "New paid signup: {{ paid_user_email }} ({{ tier_name }})"
---

New paid signup on AI Shipping Labs.

## Member

- Email: {{ paid_user_email }}
- First name: {{ paid_user_first_name }}
- Was new user: {{ was_new_user_label }}
- Signup timestamp (UTC): {{ signup_timestamp }}

## Plan

- Tier: {{ tier_name }} ({{ tier_slug }})
- Previous tier: {{ previous_tier_slug }}
{% if interval_label %}- Interval: {{ interval_label }}
{% endif %}- Amount charged: {{ amount_label }}

## Stripe

{% if stripe_customer_url %}- Customer: [{{ stripe_customer_id }}]({{ stripe_customer_url }})
{% else %}- Customer: {{ stripe_customer_id }}
{% endif %}{% if stripe_payment_url %}- Payment: [{{ stripe_payment_intent_id }}]({{ stripe_payment_url }})
{% else %}- Payment: {{ stripe_payment_intent_id }}
{% endif %}{% if stripe_subscription_url %}- Subscription: [{{ stripe_subscription_id }}]({{ stripe_subscription_url }})
{% else %}- Subscription: {{ stripe_subscription_id }}
{% endif %}- Checkout session id: {{ stripe_session_id }}

## Attribution

- UTM source (first touch): {{ first_touch_utm_source }}
- UTM campaign (first touch): {{ first_touch_utm_campaign }}

## Recent activity

{% for line in recent_activity_lines %}- {{ line }}
{% endfor %}
View the full activity timeline on the [Studio user page]({{ studio_user_url }}).
