---
subject: "{% if course_name %}You're enrolled in {{ course_name }} — welcome to the AI Shipping Labs community{% else %}Welcome to the AI Shipping Labs community{% endif %}"
---

Hi {{ user_name }},

{% if course_name %}Thanks for enrolling in {{ course_name }}.{% else %}Thanks for enrolling.{% endif %}

We created an AI Shipping Labs account for you. Sign in with Google, GitHub, or
Slack — or set a password.

[Sign in]({{ sign_in_url }}) · [Set a password]({{ password_reset_url }})

All the course interaction happens in Slack. That's where the cohort is — ask
questions, share what you're building, and compare notes{% if course_channel %} in {{ course_channel }}{% endif %}.

[Join the Slack community]({{ slack_join_url }})

Your enrollment also comes with a Main membership: community sprints,
live events, a personalized onboarding plan, topic voting, and all member
content at Basic and Main level.

To get the personalized plan, [fill in your onboarding form]({{ onboarding_url }}) —
it takes a few minutes and tells us about your background and goals.

---

Why you're getting this email: we created (or activated) your AI Shipping Labs
account and gave you community access {% if course_name %}because you enrolled in {{ course_name }}{% else %}because of the course you just enrolled in{% endif %}.

If you want to hear from us — community news, new workshops, events, what other
members are building — [verify your email]({{ newsletter_opt_in_url }}). One
click, and you can unsubscribe any time.

If you'd rather not get the course emails either, you can
[turn off course emails]({{ opt_out_url }}) separately. You can also simply
reply to this email to ask us to remove or disable your account.
