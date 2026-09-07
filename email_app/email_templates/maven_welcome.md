---
subject: "{% if course_name %}You're enrolled in {{ course_name }} — welcome to the AI Shipping Labs community{% else %}Welcome to the AI Shipping Labs community{% endif %}"
---

Hi {{ user_name }},

{% if course_name %}Thanks for enrolling in {{ course_name }}.{% else %}Thanks for enrolling.{% endif %}

As part of the course you're also invited into the AI Shipping Labs community —
our invite-only Slack workspace. That's where the course actually happens: ask
questions, share what you're building, and compare notes with everyone else
taking it{% if course_channel %} in {{ course_channel }}{% endif %}.

There's a lot more in there than the course. Your enrollment comes with a Main
membership, which includes:

- Community sprints — time-boxed build cycles where you commit to shipping one project, with check-ins, deadlines, and an accountability partner
- Live events — building sessions, office hours, mock interviews, and career conversations
- A personalized onboarding plan built around what you want to get out of this
- Topic voting, so you help decide what we cover next
- All member content and downloads at Basic and Main level, including exclusive articles and workshop recordings and materials

To get in:

1. [Set your password]({{ password_reset_url }})
2. [Sign in to AI Shipping Labs]({{ sign_in_url }})
3. [Join the Slack community]({{ slack_join_url }})

The Slack link only works once you're signed in, so do steps 1 and 2 first.

Once you're in, [fill in your onboarding form]({{ onboarding_url }}) — it takes
a few minutes and tells us about your background and goals so we can prepare a
personalized plan for you.

---

Why you're getting this email: we created (or activated) your AI Shipping Labs
account and gave you community access {% if course_name %}because you enrolled in {{ course_name }}{% else %}because of the course you just enrolled in{% endif %}.
We did NOT add you to any marketing newsletter — you'll only hear from us about
the course and the community.

If you'd rather not receive these emails, you can
[turn off emails here]({{ opt_out_url }}). You can also simply reply to this
email to ask us to remove or disable your account.
