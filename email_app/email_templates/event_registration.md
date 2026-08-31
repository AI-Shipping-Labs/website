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

This email includes a calendar invitation for this event. Use the invitation controls in this email or your calendar app to add or accept the event if prompted.

What to expect next:

- The join link above unlocks on the event page about 5 minutes before the start time.
- We'll send a short reminder closer to the event.
{% if not is_host_registration %}- Need to cancel? Use this one-click link: [Cancel my registration]({{ cancel_url }})
  (or open the event page and use the cancel button there).
{% else %}- You're the designated host for this event, so this registration can't be cancelled from here. Ask an operator if the host needs to change.
{% endif %}

See you there!

The AI Shipping Labs Team
