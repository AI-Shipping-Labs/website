---
subject: "Maven removal: {% if user_known %}{{ removed_user_name }}{% else %}{{ removed_user_email }}{% endif %}{% if course and cohort %} ({{ course }}, cohort {{ cohort }}){% elif course %} ({{ course }}){% elif cohort %} (cohort {{ cohort }}){% endif %}"
---

{% if user_known %}{{ removed_user_name }}{% if removed_user_name != removed_user_email %} ({{ removed_user_email }}){% endif %}{% else %}{{ removed_user_email }}{% endif %} was removed from {% if cohort %}cohort {{ cohort }}{% else %}a cohort{% endif %}{% if course %} of {{ course }}{% endif %} on Maven.{% if not user_known %} No account uses this email, so there was nothing to do.{% endif %}
{% if user_known %}
{% for line in summary_lines %}- {{ line }}
{% endfor %}
Studio: {{ studio_user_url }}
{% endif %}
