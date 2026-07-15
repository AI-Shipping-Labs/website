---
subject: "You're registered for the {{ series_name }} series"
---

Hi {{ user_name }},

You're registered for the **{{ series_name }}** series. We've signed you up for {{ registered_count }} upcoming session{{ registered_count_plural }}:

{{ occurrences_list }}

{{ timezone_help }}

This email includes a calendar invitation for the sessions above. Use the invitation controls in this email or your calendar app to add or accept the sessions if prompted.

What to expect next:

- Each session appears under Upcoming Events on your dashboard with its own join link, which unlocks about 5 minutes before the start time.
- We'll send a short reminder closer to each session.
- If a session moves or a new session is added, we'll send a calendar update. If prompted, review or accept it using the invitation controls in your email or calendar app.
- Manage the series any time on the series page: {{ series_url }}

{% if partial_note %}{{ partial_note }}{% endif %}

See you there!

The AI Shipping Labs Team
