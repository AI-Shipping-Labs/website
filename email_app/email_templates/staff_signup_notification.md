---
subject: "New paid signup: {{ paid_user_email }} ({{ tier_name }})"
---

New paid signup on AI Shipping Labs.

- User email: {{ paid_user_email }}
- First name: {{ paid_user_first_name }}
- Tier: {{ tier_slug }} ({{ tier_name }})
- Previous tier: {{ previous_tier_slug }}
- Was new user: {{ was_new_user_label }}
- Amount paid: {{ amount_label }}
- Stripe customer: [{{ stripe_customer_id }}]({{ stripe_customer_url }})
- Stripe session id: {{ stripe_session_id }}
- UTM source (first touch): {{ first_touch_utm_source }}
- UTM campaign (first touch): {{ first_touch_utm_campaign }}
- Signup timestamp (UTC): {{ signup_timestamp }}
- Studio user page: [{{ studio_user_url }}]({{ studio_user_url }})
