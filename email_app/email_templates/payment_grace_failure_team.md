---
subject: "[Payments] Member payment failed"
---

Member: {{ user_email }}

Base tier: {{ base_tier }}

Effective tier: {{ effective_tier }}

Stripe customer: `{{ stripe_customer_id }}`

Stripe subscription: `{{ stripe_subscription_id }}`

Stripe invoice: `{{ stripe_invoice_id }}`

Failure time: {{ failure_time }}

Interval: {{ interval }}

[Open member]({{ studio_member_url }}) · [Open payment report]({{ studio_report_url }})
