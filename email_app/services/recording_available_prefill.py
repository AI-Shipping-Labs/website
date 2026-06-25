"""Pre-fill helpers for the "recording available" campaign draft (issue #1076).

Both Studio entry points (the host recording-ready email CTA and the Studio
event-page button) deep-link to ``/studio/campaigns/new?event=<id>&template=
recording_available``. ``campaign_create`` then pre-fills the subject and body
from the configurable templates, substituting the linked workshop write-up when
``event.workshop`` exists and a short generic fallback line otherwise.

These are PRE-FILL DEFAULTS ONLY: the operator reviews/edits the draft and
presses send in Studio. No send ever happens from the pre-fill flow.
"""

from integrations.config import get_config, site_base_url

# The template token that selects the recording-available pre-fill.
RECORDING_AVAILABLE_TEMPLATE = 'recording_available'

DEFAULT_SUBJECT_TEMPLATE = 'The recording for {event_title} is available'

DEFAULT_BODY_TEMPLATE = (
    'Hi {{{{ first_name }}}},\n\n'
    'The recording for {event_title} is now available.\n\n'
    '{workshop_writeup}\n\n'
    'Watch it here: {recording_url}\n\n'
    'The AI Shipping Labs Team'
)

# Mirrors the #680 transactional follow-up fallback wording so the generic
# copy stays consistent across the two surfaces.
_FALLBACK_WRITEUP_TEMPLATE = (
    'Thanks for joining us at {event_title}. The recording is now '
    'available below.'
)


def _recording_url_for(event):
    """Absolute URL where members watch the event recording."""
    path = event.get_recording_url() or ''
    if not path:
        return ''
    if path.startswith('http://') or path.startswith('https://'):
        return path
    return f'{site_base_url()}{path}'


def _workshop_writeup_for(event):
    """Return the markdown write-up to embed, or a generic fallback line.

    Uses the linked ``Workshop.description`` markdown (the campaign body is
    markdown, so the raw source is embedded rather than the rendered HTML).
    Falls back to a single generic line when the event has no linked workshop
    or the write-up is blank — never an empty paragraph.
    """
    workshop = getattr(event, 'workshop', None)
    if workshop is not None:
        writeup = (workshop.description or '').strip()
        if writeup:
            return writeup
    return _FALLBACK_WRITEUP_TEMPLATE.format(event_title=event.title)


def build_recording_available_prefill(event):
    """Return ``{'subject': ..., 'body': ...}`` for the campaign draft form."""
    subject_template = get_config(
        'RECORDING_AVAILABLE_SUBJECT_TEMPLATE',
        DEFAULT_SUBJECT_TEMPLATE,
    )
    body_template = get_config(
        'RECORDING_AVAILABLE_BODY_TEMPLATE',
        DEFAULT_BODY_TEMPLATE,
    )

    context = {
        'event_title': event.title,
        'recording_url': _recording_url_for(event),
        'workshop_writeup': _workshop_writeup_for(event),
    }

    try:
        subject = subject_template.format(**context)
    except (KeyError, IndexError, ValueError):
        subject = DEFAULT_SUBJECT_TEMPLATE.format(event_title=event.title)
    try:
        body = body_template.format(**context)
    except (KeyError, IndexError, ValueError):
        body = DEFAULT_BODY_TEMPLATE.format(**context)

    return {'subject': subject, 'body': body}
