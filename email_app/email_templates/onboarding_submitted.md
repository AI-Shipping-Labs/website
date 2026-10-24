---
subject: "You've completed onboarding"
---

Hi {{ user_name }},

Thanks for completing onboarding! Alexey will use your answers to prepare your sprint plan.

Your plan will be ready in 1-2 business days, and you will get an email when it is ready.
{% if booking_url %}

In the meantime, you can book a 1:1 call with Alexey to kick things off:

[Book a call with Alexey]({{ booking_url }})
{% endif %}

The AI Shipping Labs Team
