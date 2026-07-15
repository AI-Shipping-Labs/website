---
subject: "{% if changed_occurrence %}Updated time for {{ event_title }} ({{ series_name }}){% else %}Your {{ series_name }} calendar invite has been updated{% endif %}"
---

Hi {{ user_name }},

{% if changed_occurrence %}The time for **{{ event_title }}** in the **{{ series_name }}** series has changed, so this email includes an updated calendar invitation for the whole series. Your upcoming session{{ registered_count_plural }}:{% else %}There's been a change to the **{{ series_name }}** series, so this email includes an updated calendar invitation. Your upcoming session{{ registered_count_plural }}:{% endif %}

{{ occurrences_list }}

The update uses the same calendar identity for each session, so supported calendar apps can apply the new details to the existing occurrence. If prompted, review or accept the update using the invitation controls in this email or your calendar app.

Manage the series any time on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

See you there!

The AI Shipping Labs Team
