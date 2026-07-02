---
subject: "Workshop ready: {{ workshop_title }}"
---

Hi {{ user_name }},

The workshop write-up from **{{ event_title }}** is ready.

## {{ workshop_title }}

{% if workshop_description %}{{ workshop_description }}{% else %}You can now open the workshop page for the recap, materials, tutorial notes, and recording details.{% endif %}

[Open the workshop]({{ workshop_url }})

Event page: [{{ event_title }}]({{ event_url }})

The AI Shipping Labs Team
