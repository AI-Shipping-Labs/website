"""Worker-side render context for the post-event follow-up email.

Issue #1613: the follow-up delivery persists no render data; the worker
builds the whole context from the saved ``Event`` at delivery time, so
event, recording (including the raw S3 URL), recap and feedback links
never sit in a durable row. The producer keeps only the cheap
has-recording gate so an unqualified event never queues a delivery.
"""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse

from integrations.config import site_base_url

FALLBACK_SUMMARY_TEMPLATE = (
    "Thanks for joining us at {event_title}. The recording is now "
    "available below."
)


class PostEventMailContextError(ValueError):
    """Raised when the saved event cannot yield a sendable context."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def recording_url_for(event):
    """Return the follow-up recording link exactly as the flow always has."""

    return event.recording_s3_url or event.recording_url


def feedback_url_for(event):
    """Return the public feedback submit URL or ``None`` when unavailable.

    The CTA is wired conditionally on issue #679 having shipped: the
    ``events.EventFeedback`` model must be importable and the submit
    route must resolve. When either is unmet the template's
    ``{% if feedback_url %}`` block stays empty.
    """

    try:
        from events.models import EventFeedback  # noqa: F401
    except ImportError:
        return None

    try:
        path = reverse("event_feedback_submit", kwargs={"slug": event.slug})
    except NoReverseMatch:
        return None

    return f"{site_base_url()}{path}"


def build_followup_context(event):
    """Return the full in-memory render context from the saved event."""

    recording_url = recording_url_for(event)
    if not recording_url:
        raise PostEventMailContextError("no_recording_url")

    site_url = site_base_url()
    context = {
        "event_title": event.title,
        "event_summary": event.post_event_summary or FALLBACK_SUMMARY_TEMPLATE.format(
            event_title=event.title,
        ),
        "recording_url": recording_url,
        "event_url": f"{site_url}{event.get_absolute_url()}",
    }

    # Issue #1458: link the real recap when one is published. Only fall back
    # to the "notes are still being put together" line when there is nothing
    # to link.
    if event.recap_is_published:
        context["recap_url"] = f"{site_url}{event.get_recap_url()}"
    else:
        context["notes_placeholder"] = True

    feedback_url = feedback_url_for(event)
    if feedback_url:
        context["feedback_url"] = feedback_url
    return context
