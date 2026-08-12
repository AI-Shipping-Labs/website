---
subject: "Payment needed to keep your paid membership"
---

Hi {{ user_name }},

We still could not confirm your membership payment. If payment does not recover by **{{ deadline_utc }}**, your paid base membership will change to Free.

{% if override_continues %}Your separate courtesy access remains in place through its own expiry.{% endif %}

{% if recovery_url %}[Retry or update payment]({{ recovery_url }}){% else %}Reply to this email and we will help you update the payment.{% endif %}

The AI Shipping Labs Team
