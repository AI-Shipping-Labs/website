---
subject: "{% if partner_count == 1 %}Your accountability partner for {{ sprint_name }}{% else %}Your accountability partners for {{ sprint_name }}{% endif %}"
---

Hi {{ member_name }},

Your accountability {% if partner_count == 1 %}partner is{% else %}partners are{% endif %} ready for **{{ sprint_name }}**:

{% for partner in partners %}
- **{{ partner.name }}** ({{ partner.email }}){% if partner.slack_identity %} — Slack: {{ partner.slack_identity }}{% endif %}{% if partner.slack_profile_url %} — [Open Slack profile]({{ partner.slack_profile_url }}){% else %} — Slack profile link unavailable; use the name/email above to connect.{% endif %}
{% endfor %}

[Open the sprint board]({{ board_url }})

Use the board to see who is shipping alongside you and keep each other moving.

The AI Shipping Labs Team
