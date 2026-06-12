---
subject: "You're hosting: {{ event_title }}"
---

Hi {{ user_name }},

This is your host copy for **{{ event_title }}** on {{ event_datetime }}. We've attached a calendar invite so the event lands in your calendar automatically — open the attached `.ics` to add (or update) it.

As the host, here are your management links (these are for you only — do not forward them to attendees):

- Edit event details: {{ edit_url }}
- Manage registrations and notify attendees: {{ manage_url }}
- Create or set up the Zoom meeting: {{ create_zoom_url }}
{% if zoom_join_url %}- Host Zoom join link: {{ zoom_join_url }}
{% endif %}- Open the event in Studio: {{ studio_url }}

If the time changes, re-save the event in Studio and we'll send you an updated invite so your calendar entry moves rather than duplicating.

See you there!

The AI Shipping Labs Team
