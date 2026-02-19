---
subject: "Your membership has been cancelled"
---

Hi {{ user_name }},

Your **{{ tier_name }}** membership has been cancelled.

{% if access_until %}You will continue to have access until **{{ access_until }}**.{% endif %}

If you change your mind, you can re-subscribe at any time:

[Re-subscribe]({{ site_url }}/pricing/)

We hope to see you again!

The AI Shipping Labs Team
