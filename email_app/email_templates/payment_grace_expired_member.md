---
subject: "Your AI Shipping Labs account is now Free"
---

Hi {{ user_name }},

Your payment was still unconfirmed, so your base membership is now Free. You are always welcome to continue with your Free account and rejoin a paid tier whenever you are ready.

{% if override_continues %}Your separate courtesy access remains in place through its own expiry.{% endif %}

{% if recovery_url %}[Retry or update payment]({{ recovery_url }}){% else %}Reply to this email and we will help you update the payment.{% endif %}

The AI Shipping Labs Team
