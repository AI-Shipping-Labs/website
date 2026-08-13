---
subject: "New Maven enrollment: {{ enrolled_user_email }} ({{ course }})"
---

A Maven enrollment occurrence completed its entitlement step.

## Member

- Name: {{ enrolled_user_name }}
- Email: {{ enrolled_user_email }}
- User ID: {{ enrolled_user_id }}
- Account: {{ account_state }}
- Studio member: [open member]({{ studio_user_url }})

## Enrollment

- Course: {{ course }}
- Cohort: {{ cohort }}
- Configured Maven tier: {{ tier_name }} (`{{ tier_slug }}`)
- Actual retained entitlement expiry: {{ entitlement_expiry }}
- Studio occurrence: [open occurrence]({{ studio_occurrence_url }})
