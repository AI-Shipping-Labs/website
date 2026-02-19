---
subject: "Reminder: {{ event_title }} is coming up"
---

Hi {{ user_name }},

Just a reminder that **{{ event_title }}** is starting soon.

**When:** {{ event_datetime }}
{% if event_url %}**Join:** [{{ event_url }}]({{ event_url }}){% endif %}

We look forward to seeing you there!

The AI Shipping Labs Team
