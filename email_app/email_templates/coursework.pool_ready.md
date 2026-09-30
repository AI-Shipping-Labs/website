---
subject: "Review {{ review_count }} project{{ review_count|pluralize }} for {{ project_title }}"
---

Hi {{ user_name }},

Your project **{{ project_title }}** is now in a review group. Review {{ review_count }} peer project{{ review_count|pluralize }}{% if due_date_display %} by {{ due_date_display }}{% endif %}.

{% for review in reviews %}
[Review project {{ review.number }}]({{ review.url }})
{% endfor %}

[See all your reviews]({{ review_list_url }})

Your own score is ready once your group's reviews are in. Delivering your reviews is part of passing the project.

The AI Shipping Labs Team
