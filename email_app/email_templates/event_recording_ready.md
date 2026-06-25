---
subject: "Recording ready for review: {{ event_title }}"
---

Hi {{ user_name }},

The Zoom recording for **{{ event_title }}** has been uploaded.

- Event time: {{ event_datetime }}
- Current publish state: {{ publish_state }}
- Studio review page: [Open event in Studio]({{ studio_event_url }})

{{ publish_copy }}

Next step: review the recording in Studio, publish it when it is ready for members, and send the attendee follow-up separately when the recap and notes are ready.

Want to email everyone who registered that the recording is available? [Email registrants the recording is available]({{ campaign_prefill_url }}) — this opens a pre-filled draft in Studio for you to review and send. Nothing is sent automatically.

The private S3 recording object has been uploaded and is available through Studio. This email intentionally does not include the private S3 URL.

{% if zoom_recording_url %}Zoom source/review fallback: [Open Zoom recording]({{ zoom_recording_url }}){% endif %}

The AI Shipping Labs Team
