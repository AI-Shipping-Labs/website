---
subject: "{% if course_name %}Your access to {{ course_name }} has ended{% else %}Your course access has ended{% endif %}"
---

Hi {{ user_name }},

You're no longer enrolled in {% if course_name %}{{ course_name }}{% else %}your Maven course{% endif %}, so your access to the course{% if membership_ended != False %} and to AI Shipping Labs membership{% endif %} has ended.

Your account is still here. If you'd like it deleted, open {{ account_deletion_url }} and click "Request account deletion" under Privacy and data. We'll delete it within a month. If you can't sign in, email {{ privacy_email }}.
