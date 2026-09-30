---
subject: "{% if course_name %}Your {{ course_name }} access has ended{% else %}Your course access has ended{% endif %}"
---

Hi {{ user_name }},

{% if course_name %}You have been removed from {{ course_name }} on Maven.{% else %}You have been removed from your Maven course cohort.{% endif %} Your course access and your membership access on AI Shipping Labs have ended.

Your AI Shipping Labs account still exists, so you can still sign in.

If you want your account and profile deleted completely, sign in, open your account page, scroll to Privacy and data, and click Request account deletion. The direct link is {{ account_deletion_url }}

The team deletes the account and tells you when it is done, within one month at most. If you cannot sign in, email {{ privacy_email }} from this address and ask for your account to be deleted.
