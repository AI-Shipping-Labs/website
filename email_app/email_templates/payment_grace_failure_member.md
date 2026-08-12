---
subject: "Payment failed — please retry your AI Shipping Labs payment"
---

Hi {{ user_name }},

Your recent AI Shipping Labs membership payment failed. Please retry the payment or update your payment method.

{% if recovery_url %}[Retry or update payment]({{ recovery_url }}){% else %}Reply to this email and we will help you update the payment.{% endif %}

The AI Shipping Labs Team
