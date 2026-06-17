---
subject: "Rescheduled: {{ event_title }}"
---

Hi {{ user_name }},

The schedule for **{{ event_title }}** has changed. Please update your calendar.

Previous time: {{ old_event_datetime }}

New time: {{ new_event_datetime }}

{{ timezone_help }}

Join link: {{ join_url }}

We've attached an updated `.ics` file so Apple Calendar, Outlook, and other clients can overwrite the original entry automatically.

{% if is_host_registration %}
Host management links (for hosts only; do not forward them to attendees):

- Edit event details: {{ edit_url }}
- Manage registrations and notify attendees: {{ manage_url }}
- Create or set up the Zoom meeting: {{ create_zoom_url }}
{% if zoom_join_url %}- Host Zoom join link: {{ zoom_join_url }}
{% endif %}- Open the event in Studio: {{ studio_url }}

{% endif %}
Can no longer make it? Use this one-click link: [Cancel my registration]({{ cancel_url }})

Thanks for your flexibility — see you at the new time.

The AI Shipping Labs Team
