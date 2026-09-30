---
subject: "{{ name }} left {{ course }}"
---

{{ name }} ({{ email }}) unenrolled from {{ course }}{% if cohort %}, cohort {{ cohort }}{% endif %}. {{ cause_sentence }} Now {{ enrolled_count }} enrolled.

Studio: {{ studio_user_url }}
