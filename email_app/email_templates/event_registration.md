---
subject: "You're registered: {{ event_title }}"
---

Hi {{ user_name }},

You're registered for **{{ event_title }}**. We're looking forward to seeing you there.

When: {{ event_datetime }}

{{ timezone_help }}

Join link: {{ join_url }}

Add to your calendar:
[Google Calendar]({{ google_calendar_url }}) · [Outlook.com]({{ outlook_calendar_url }}) · [Microsoft 365]({{ office365_calendar_url }})

Or use the attached `.ics` file for Apple Calendar and other clients.

{% if is_host_registration %}
Host management links (for hosts only; do not forward them to attendees):

- Edit event details: {{ edit_url }}
- Manage registrations and notify attendees: {{ manage_url }}
- Create or set up the Zoom meeting: {{ create_zoom_url }}
{% if zoom_join_url %}- Host Zoom join link: {{ zoom_join_url }}
{% endif %}- Open the event in Studio: {{ studio_url }}

{% endif %}
What to expect next:

- The join link above unlocks on the event page about 15 minutes before the start time.
- We'll send a short reminder closer to the event.
- Need to cancel? Use this one-click link: [Cancel my registration]({{ cancel_url }})
  (or open the event page and use the cancel button there).

See you there!

The AI Shipping Labs Team
