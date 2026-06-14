---
subject: "New Slack member: {{ user_email }} — say hi"
---

This person just joined Slack — say hi.

## Member

- Name: {{ user_full_name }}
- Email: {{ user_email }}
- User ID: {{ user_id }}

## Context

- Tier: {{ tier_name }}
- Signup source: {{ signup_source }}

Open their [Studio user page]({{ studio_user_url }}) to greet them.
