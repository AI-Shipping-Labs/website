---
subject: "A {{ series_name }} session has been cancelled"
---

Hi {{ user_name }},

A session in the **{{ series_name }}** series has been cancelled:

{{ occurrences_list }}

A calendar update is attached to this email. Open it to remove the cancelled session from your calendar.

The rest of the series is unaffected. You can review the remaining sessions on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

The AI Shipping Labs Team
