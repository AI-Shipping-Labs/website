"""Member-safe event serializers for ``/member-api/v1/events``."""

from datetime import UTC

from django.db.models import Exists, OuterRef

from content.access import get_required_tier_label
from events.models import EventRegistration, SeriesOccurrenceOptOut, SeriesRegistration
from events.services.display_time import should_display_event_location
from integrations.services.banner_generator.resolve import effective_banner_url

HAS_EVENT_REGISTRATION_ATTR = "_member_has_event_registration"
HAS_SERIES_REGISTRATION_ATTR = "_member_has_series_registration"
HAS_OCCURRENCE_OPT_OUT_ATTR = "_member_has_occurrence_opt_out"


def annotate_member_registration_state(queryset, user):
    """Attach request-user registration booleans with ``Exists`` subqueries.

    Call this on the user-scoped queryset before slicing a list page, and on
    the single-row queryset used by detail/register serialization, so list and
    detail cannot drift back to per-event ``.exists()`` lookups.
    """
    return queryset.annotate(
        **{
            HAS_EVENT_REGISTRATION_ATTR: Exists(
                EventRegistration.objects.filter(event_id=OuterRef("pk"), user=user),
            ),
            HAS_SERIES_REGISTRATION_ATTR: Exists(
                SeriesRegistration.objects.filter(
                    series_id=OuterRef("event_series_id"),
                    user=user,
                ),
            ),
            HAS_OCCURRENCE_OPT_OUT_ATTR: Exists(
                SeriesOccurrenceOptOut.objects.filter(
                    event_id=OuterRef("pk"),
                    user=user,
                ),
            ),
        }
    )


def _annotated_bool(event, attr_name):
    if not hasattr(event, attr_name):
        raise AttributeError(
            f"Event is missing {attr_name}; call "
            "annotate_member_registration_state() before serialize."
        )
    return bool(getattr(event, attr_name))


def _utc_iso(value):
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _series_data(event):
    series = event.event_series
    if series is None:
        return None
    return {
        "id": series.id,
        "slug": series.slug,
        "name": series.name,
        "url": series.get_absolute_url(),
    }


def _registration_source(event):
    if _annotated_bool(event, HAS_EVENT_REGISTRATION_ATTR):
        return "event"
    if event.event_series_id is None:
        return "none"
    if not _annotated_bool(event, HAS_SERIES_REGISTRATION_ATTR):
        return "none"
    if _annotated_bool(event, HAS_OCCURRENCE_OPT_OUT_ATTR):
        return "none"
    return "series"


def _time_status(event):
    if event.is_upcoming:
        return "upcoming"
    if event.is_past:
        return "past"
    return "draft"


def _registration_state(event, *, has_access):
    source = _registration_source(event)
    available = (
        not event.is_external
        and has_access
        and event.is_upcoming
        and source == "none"
    )
    targets = []
    if available:
        targets = ["series", "event"] if event.event_series_id else ["event"]
    return {
        "is_registered": source != "none",
        "registration_source": source,
        "registration_available": available,
        "registration_targets": targets,
        "member_endpoint_url": f"/member-api/v1/events/{event.id}",
    }


def serialize_event_summary(event, user, *, has_access=True):
    data = {
        "id": event.id,
        "slug": event.slug,
        "title": event.title,
        "url": event.get_absolute_url(),
        "kind": event.kind,
        "start_datetime": _utc_iso(event.start_datetime),
        "end_datetime": _utc_iso(event.end_datetime),
        "effective_end_datetime": _utc_iso(event.effective_end_datetime),
        "timezone": event.timezone,
        "location": event.location if should_display_event_location(event) else "",
        "tags": list(event.tags or []),
        "status": event.status,
        "time_status": _time_status(event),
        "required_level": event.required_level,
        "required_tier_label": get_required_tier_label(event.required_level),
        "series": _series_data(event),
        "attendee_count": event.attendee_count,
        "external_registration_url": event.zoom_join_url if event.is_external else None,
    }
    data.update(_registration_state(event, has_access=has_access))
    return data


def _serialize_instructor(instructor):
    return {
        "instructor_id": instructor.instructor_id,
        "name": instructor.name,
        "bio": instructor.bio,
        "bio_html": instructor.bio_html,
        "photo_url": instructor.photo_url,
        "links": list(instructor.links or []),
    }


def _serialize_host(host):
    return {
        "slug": host.slug,
        "name": host.name,
        "title": host.title,
        "bio": host.bio,
        "bio_html": host.bio_html,
        "photo_url": host.display_photo_url,
    }


def serialize_event_detail(event, user, *, has_access=True):
    data = serialize_event_summary(event, user, has_access=has_access)
    data.update({
        "description": event.description,
        "description_html": event.description_html,
        "instructors": [
            _serialize_instructor(instructor)
            for instructor in event.ordered_instructors
        ],
        "hosts": [_serialize_host(host) for host in event.ordered_hosts],
        "banner_url": effective_banner_url(event),
        "join_url": (
            event.get_join_url()
            if data["is_registered"] and event.is_upcoming and event.can_show_zoom_link()
            else None
        ),
    })
    return data
