---
subject: "Welcome to {{ tier_name }}!"
---

Hi {{ user_name }},

Welcome to **AI Shipping Labs**! You now have access to the **{{ tier_name }}** tier.

Here's what you can do next:

- Browse our [tutorials and resources]({{ site_url }}/tutorials/)
- Check out upcoming [events]({{ site_url }}/events/)
{% if slack_invite_url %}- Join the community on Slack: [Accept Invite]({{ slack_invite_url }}){% endif %}

If you have any questions, just reply to this email.

Happy building!
The AI Shipping Labs Team
