---
subject: "Week {{ week_number }} is ready for {{ sprint_name }}"
---

Hi {{ user_name }},

Week {{ week_number }} of **{{ sprint_name }}** is ready.

Focus: **{{ week_theme }}**

You have **{{ unfinished_count }} {{ unfinished_label }}** for this week.

{% if needs_previous_week_note %}
Before you move on, write your Week {{ previous_week_number }} note so your progress stays easy to review.
{% endif %}

[Open Week {{ week_number }} in your sprint plan]({{ plan_url }})

The AI Shipping Labs Team
