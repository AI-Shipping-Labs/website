---
subject: "Recap ready: {{ event_title }}"
---

Hi {{ user_name }},

The recap for {{ event_title }} is ready.

[Read the event recap]({{ recap_url }})

{% if recording_url %}[Watch the recording]({{ recording_url }}){% endif %}

Event page: [{{ event_title }}]({{ event_url }})

The AI Shipping Labs Team
