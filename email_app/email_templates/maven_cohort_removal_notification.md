---
subject: "Maven cohort removal — review needed"
---

A student was removed from a Maven cohort.

{% if user_known %}
- Name: {{ removed_user_name }}
- Email: {{ removed_user_email }}
- User ID: {{ removed_user_id }}
- Studio profile: {{ studio_user_url }}
- Cohort: {{ cohort }}
- Course: {{ course }}

No automatic change was made. Their tier override, access, and Slack
membership are all unchanged.

You may want to suspend their tier override / subscription. Their access is
unchanged until you act — the decision is yours.
{% else %}
- Email: {{ removed_user_email }}
- Cohort: {{ cohort }}
- Course: {{ course }}

This email did not match any AI Shipping Labs account, so there was nothing
to change. No action was taken.
{% endif %}
