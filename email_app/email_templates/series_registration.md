---
subject: "You're registered for the {{ series_name }} series"
---

Hi {{ user_name }},

You're registered for the **{{ series_name }}** series. We've signed you up for {{ registered_count }} upcoming session{{ registered_count_plural }}:

{{ occurrences_list }}

A calendar invite covering the whole series is attached to this email — open it to add every session to your calendar at once.

What to expect next:

- Each session appears under Upcoming Events on your dashboard with its own join link, which unlocks about 15 minutes before the start time.
- We'll send a short reminder closer to each session.
- If a session moves or is added, we'll send you an updated calendar invite so your calendar stays in sync.
- New sessions added to this series later will be added to your calendar automatically.
- Manage the series any time on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

See you there!

The AI Shipping Labs Team
