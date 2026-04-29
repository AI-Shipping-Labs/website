---
subject: "Welcome to AI Shipping Labs"
---

Hi {{ user_name }},

Welcome to **AI Shipping Labs**.

{% if is_course_db_import %}
We created an account for you because of your DataTalks course history{% if course_slug_list %}: {{ course_slug_list }}{% endif %}. This gives you no-cost Main access as a continuity bridge for alumni.
{% else %}
We created an account for you because you were already connected with us through {{ source_label }}{% if import_tags %} ({{ import_tags }}){% endif %}.
{% endif %}

You can get started in either of these ways:

- [Set your password]({{ password_reset_url }})
- [Sign in to AI Shipping Labs]({{ sign_in_url }})

If this was unexpected, use the unsubscribe link below or reply to this email to ask for account deletion.
