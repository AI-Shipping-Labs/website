---
subject: "{% if changed_occurrence %}Updated time for {{ event_title }} ({{ series_name }}){% else %}Your {{ series_name }} calendar invite has been updated{% endif %}"
---

Hi {{ user_name }},

{% if changed_occurrence %}The time for **{{ event_title }}** in the **{{ series_name }}** series has changed, so we've refreshed your calendar invite for the whole series. Your upcoming session{{ registered_count_plural }}:{% else %}There's been a change to the **{{ series_name }}** series, so we've refreshed your calendar invite. Your upcoming session{{ registered_count_plural }}:{% endif %}

{{ occurrences_list }}

An updated calendar invite is attached to this email. Open it to apply the change — your calendar will update the existing entries rather than create duplicates.

Manage the series any time on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

See you there!

The AI Shipping Labs Team
