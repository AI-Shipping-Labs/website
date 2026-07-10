---
subject: "Your {{ sprint_name }} sprint recap"
---

Hi {{ user_name }},

{{ progress_sentence }}

[Open my plan]({{ plan_url }})

{% if has_feedback %}
{{ feedback_copy }}

[{{ feedback_cta_label }}]({{ feedback_url }})
{% endif %}

{% if has_next_action %}
{{ next_action_copy }}

[{{ next_action_label }}]({{ next_action_url }})
{% endif %}

The AI Shipping Labs Team
