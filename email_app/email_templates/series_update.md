---
subject: "Your {{ series_name }} calendar invite has been updated"
---

Hi {{ user_name }},

There's been a change to the **{{ series_name }}** series, so we've refreshed your calendar invite. Your upcoming session{{ registered_count_plural }}:

{{ occurrences_list }}

An updated calendar invite is attached to this email. Open it to apply the change — your calendar will update the existing entries rather than create duplicates.

Manage the series any time on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

See you there!

The AI Shipping Labs Team
