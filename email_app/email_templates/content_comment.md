---
subject: "New {{ verb }} on {{ content_title }}"
---

Hi {{ user_name }},

{% if comment_excerpt %}{{ commenter_name }} wrote on **{{ content_title }}** ({{ parent_title }}):

> {{ comment_excerpt }}
{% else %}{{ commenter_name }} posted a new {{ verb }} on **{{ content_title }}** ({{ parent_title }}).
{% endif %}

[Open the discussion]({{ discussion_url }})

The AI Shipping Labs Team
