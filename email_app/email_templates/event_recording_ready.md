---
subject: "{% if is_available_to_watch %}Recording available to watch: {{ event_title }}{% else %}Recording ready for review: {{ event_title }}{% endif %}"
---

Hi {{ user_name }},

The Zoom recording for **{{ event_title }}** has been uploaded.

- Event time: {{ event_datetime }}
- Current publish state: {{ publish_state }}
{% if is_available_to_watch %}- Watch the recording: [Watch the recording]({{ watch_url }}){% else %}- Studio review page: [Open event in Studio]({{ studio_event_url }}){% endif %}

{{ publish_copy }}

{% if is_available_to_watch %}The recording is live for members with access. If anything needs trimming or the wrong recording went up, edit or unpublish it in Studio: [Open event in Studio]({{ studio_event_url }}).{% else %}Next step: review the recording in Studio, publish it when it is ready for members, and send the attendee follow-up separately when the recap and notes are ready.{% endif %}

Want to email everyone who registered that the recording is available? [Email registrants the recording is available]({{ campaign_prefill_url }}) — this opens a pre-filled draft in Studio for you to review and send. Nothing is sent automatically.

The private S3 recording object has been uploaded and is available through Studio. This email intentionally does not include the private S3 URL.

{% if zoom_recording_url %}Zoom source/review fallback: [Open Zoom recording]({{ zoom_recording_url }}){% endif %}

The AI Shipping Labs Team
