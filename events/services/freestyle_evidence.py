"""Helpers for guest-facing freestyle social proof blocks."""

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from events.services.time_windows import past_recording_events_queryset


def _matches_freestyle(value):
    return "freestyle" in str(value or "").lower()


def _workshop_for_subject(subject):
    try:
        workshop = subject.workshop
    except (AttributeError, ObjectDoesNotExist):
        workshop = None
    if workshop is not None:
        return workshop

    event = getattr(subject, "event", None)
    if event is None:
        return None
    try:
        return event.workshop
    except ObjectDoesNotExist:
        return None


def is_freestyle_subject(subject):
    """Return True when an event/workshop should show freestyle evidence."""
    if subject is None:
        return False

    fields = [
        getattr(subject, "title", ""),
        getattr(subject, "slug", ""),
        *(getattr(subject, "tags", []) or []),
    ]
    workshop = _workshop_for_subject(subject)
    if workshop is not None and workshop is not subject:
        fields.extend([workshop.title, workshop.slug, *(workshop.tags or [])])
    return any(_matches_freestyle(value) for value in fields)


def build_freestyle_evidence(subject, *, limit=3):
    """Return up to ``limit`` past freestyle recording/workshop links."""
    if not is_freestyle_subject(subject):
        return []

    subject_event = getattr(subject, "event", None)
    subject_event_id = getattr(subject_event, "id", None) or getattr(subject, "id", None)
    subject_workshop = _workshop_for_subject(subject)
    subject_workshop_id = getattr(subject_workshop, "id", None)

    evidence = []
    for candidate in (
        past_recording_events_queryset(now=timezone.now())
        .select_related("workshop")
        .order_by("-start_datetime")
    ):
        if candidate.pk == subject_event_id or not is_freestyle_subject(candidate):
            continue

        kind_label = "Event recording"
        title = candidate.title
        url = candidate.get_absolute_url()
        required_level = candidate.required_level

        try:
            workshop = candidate.workshop
        except ObjectDoesNotExist:
            workshop = None

        if workshop is not None:
            if workshop.pk == subject_workshop_id:
                continue
            if workshop.status == "published":
                kind_label = "Workshop writeup"
                title = workshop.title
                url = workshop.get_absolute_url()
                required_level = workshop.recording_required_level

        evidence.append(
            {
                "title": title,
                "url": url,
                "kind_label": kind_label,
                "date_label": candidate.start_datetime.strftime("%b %d, %Y"),
                "required_level": required_level,
            }
        )
        if len(evidence) == limit:
            break

    return evidence
