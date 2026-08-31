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

{% if not is_host_registration %}Can no longer make it? Use this one-click link: [Cancel my registration]({{ cancel_url }})
{% else %}You're the designated host for this event, so this registration can't be cancelled from here. Ask an operator if the host needs to change.
{% endif %}

Thanks for your flexibility — see you at the new time.

The AI Shipping Labs Team
