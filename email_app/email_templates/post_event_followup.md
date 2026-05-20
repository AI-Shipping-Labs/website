---
subject: "Recap and recording: {{ event_title }}"
---

Hi {{ user_name }},

{{ event_summary }}

Watch the recording: [{{ event_title }}]({{ event_url }})

Direct video link: {{ recording_url }}

{% if notes_placeholder %}Workshop notes are still being put together — we'll send them separately when ready.{% endif %}

{% if feedback_url %}If you have a minute, we'd love your feedback on the session: [Leave feedback]({{ feedback_url }}).{% endif %}

Thanks for joining us,

The AI Shipping Labs Team
