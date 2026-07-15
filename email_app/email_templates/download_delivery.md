---
subject: "Your download: {{ resource_title }}"
---

# Your download is ready

You requested **{{ resource_title }}** from AI Shipping Labs.

{% if verification_required %}Confirm your email address to receive the resource. This creates a passwordless Free account.{% else %}Use the secure link below to receive the resource.{% endif %}

{% if newsletter_opt_in %}You also asked to subscribe to the AI Shipping Labs newsletter. Clicking the button below confirms that subscription and gets the download. If you did not make this request, ignore this email; you will not be subscribed.{% else %}This request does not subscribe you to marketing.{% endif %}

[{% if newsletter_opt_in %}Confirm subscription and get the download{% else %}Get the download{% endif %}]({{ delivery_url }})

This link is scoped to this resource and expires in {{ expires_hours }} hours. If you did not request it, you can ignore this email.
