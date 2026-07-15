---
subject: "A {{ series_name }} session has been cancelled"
---

Hi {{ user_name }},

A session in the **{{ series_name }}** series has been cancelled:

{{ occurrences_list }}

This email includes a calendar cancellation update for the session above. Supported calendar apps can use it to remove or mark the session as cancelled. If prompted, apply the update using the invitation controls in this email or your calendar app.

The rest of the series is unaffected. You can review the remaining sessions on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

The AI Shipping Labs Team
