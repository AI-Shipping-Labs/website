---
subject: "Maven cohort removal — what was changed"
---

A student was removed from a Maven cohort.

{% if user_known %}
- Name: {{ removed_user_name }}
- Email: {{ removed_user_email }}
- User ID: {{ removed_user_id }}
- Studio profile: {{ studio_user_url }}
- Cohort: {{ cohort }}
- Course: {{ course }}

What was done automatically:

- Course access: {{ course_access_result }}
- Cohort enrollment: {{ cohort_enrollment_result }}
- Event series: {{ series_registration_result }}
- Tags: {{ tags_result }}
- Tier override: {{ override_result }}
- Student email: {{ student_email_result }}
- Slack: {{ slack_result }}

A paid Stripe subscription is never changed by this flow.
{% else %}
- Email: {{ removed_user_email }}
- Cohort: {{ cohort }}
- Course: {{ course }}

This email did not match any AI Shipping Labs account, so there was nothing
to change. No action was taken.
{% endif %}
