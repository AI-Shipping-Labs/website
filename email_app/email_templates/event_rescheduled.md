---
subject: "Rescheduled: {{ event_title }}"
---

Hi {{ user_name }},

The schedule for **{{ event_title }}** has changed. Please update your calendar.

Previous time: {{ old_event_datetime }}

New time: {{ new_event_datetime }}

{{ timezone_help }}

Join link: {{ join_url }}

This email includes an updated calendar invitation. The update uses the same calendar identity, so supported calendar apps can apply the new time to the existing event. If prompted, review or accept the update using the invitation controls in this email or your calendar app.

{% if is_host_registration %}
Host management links (for hosts only; do not forward them to attendees):

- Edit event details: {{ edit_url }}
- Manage registrations and notify attendees: {{ manage_url }}
- Create or set up the Zoom meeting: {{ create_zoom_url }}
{% if zoom_join_url %}- Host Zoom join link: {{ zoom_join_url }}
{% endif %}- Open host controls: {{ studio_url }}

{% endif %}
{% if not is_host_registration %}Can no longer make it? Use this one-click link: [Cancel my registration]({{ cancel_url }})
{% else %}Host delivery stays active while you are assigned to this event. Ask an operator to reassign the host if needed.
{% endif %}

Thanks for your flexibility — see you at the new time.

The AI Shipping Labs Team
