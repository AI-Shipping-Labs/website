from django.urls import path

from events.views.api import (
    cancel_registration_action,
    register_for_event,
    series_registration,
    unregister_from_event,
)
from events.views.host_management import (
    host_event_create_zoom,
    host_event_manage,
    host_event_notify,
    host_event_update,
)
from events.views.pages import (
    cancel_registration_page,
    event_calendar_ics,
    event_detail,
    event_detail_no_slug_redirect,
    event_feedback_submit,
    event_join_redirect,
    event_series_no_slug_redirect,
    event_series_public,
    events_calendar,
    events_calendar_feed,
    events_list,
)
from events.views.recording import event_recording_stream

urlpatterns = [
    path('events', events_list, name='events_list'),
    path(
        'events/<int:event_id>/host/manage',
        host_event_manage,
        name='event_host_manage',
    ),
    path(
        'events/<int:event_id>/host/update',
        host_event_update,
        name='event_host_update',
    ),
    path(
        'events/<int:event_id>/host/create-zoom',
        host_event_create_zoom,
        name='event_host_create_zoom',
    ),
    path(
        'events/<int:event_id>/host/notify',
        host_event_notify,
        name='event_host_notify',
    ),
    # Issue #578: ``events/calendar.ics`` is registered BEFORE the
    # ``events/<slug>`` join/calendar.ics/cancel routes below so the
    # literal ``calendar.ics`` isn't swallowed by the slug converter.
    # Same pattern as ``events/series/<id>/<slug>``.
    path(
        'events/calendar.ics',
        events_calendar_feed,
        name='events_calendar_feed',
    ),
    path('events/calendar', events_calendar, name='events_calendar'),
    path(
        'events/calendar/<int:year>/<int:month>',
        events_calendar,
        name='events_calendar_month',
    ),
    # Issue #1035: canonical event-series detail. ``series_id`` is the
    # lookup key; ``slug`` is cosmetic, mirroring event detail URLs.
    path(
        'events/series/<int:series_id>/<slug:slug>',
        event_series_public,
        name='event_series_public',
    ),
    path(
        'events/series/<int:series_id>',
        event_series_no_slug_redirect,
        name='event_series_no_slug',
    ),
    # Issue #1082: id-canonical join route, mirroring the #673 detail URL
    # and the #679 ``feedback`` verb. Registered BEFORE the canonical
    # ``events/<int:event_id>/<slug:slug>`` detail route below so the
    # literal ``join`` segment is not swallowed by the slug converter.
    # ``event_id`` is the lookup key; ``slug`` is cosmetic and a mismatch
    # 301s to the canonical join URL. This is the name every
    # ``reverse('event_join', ...)`` caller and ``Event.get_join_url``
    # mint going forward.
    path(
        'events/<int:event_id>/<slug:slug>/join',
        event_join_redirect,
        name='event_join',
    ),
    # Issue #1082: legacy slug-only join route kept as a permanent alias so
    # existing calendar ``.ics`` entries already in attendees' clients and
    # stale registration emails that point at ``/events/<slug>/join`` keep
    # resolving and never 404. Registered BEFORE the canonical detail route
    # for the same swallowing reason as the id+slug join route above.
    path(
        'events/<slug:slug>/join',
        event_join_redirect,
        name='event_join_legacy',
    ),
    # Issue #673: slug-keyed sibling routes for .ics download and
    # cancel-registration intentionally stay on ``events/<slug>/<verb>``
    # (deferred follow-up to id-canonicalize). They must be registered
    # BEFORE the new ``events/<int:event_id>/<slug:slug>`` canonical
    # route below — a 3-segment URL like ``/events/foo/calendar.ics``
    # should always reach the verb route, not be interpreted as an
    # event-detail attempt.
    path(
        'events/<slug:slug>/calendar.ics',
        event_calendar_ics,
        name='event_calendar_ics',
    ),
    path(
        'events/<slug:slug>/cancel-registration',
        cancel_registration_page,
        name='event_cancel_registration',
    ),
    # Issue #679: post-event feedback submission. Registered BEFORE the
    # canonical ``events/<int:event_id>/<slug:slug>`` route below so the
    # literal ``feedback`` segment is not swallowed by the slug
    # converter. Same pattern as ``events/<slug>/join``.
    path(
        'events/<int:event_id>/<slug:slug>/feedback',
        event_feedback_submit,
        name='event_feedback_submit',
    ),
    # Issue #1134: access-controlled recording serving endpoint. The URL
    # MUST end in ``.mp4`` so ``detect_video_source`` classifies the
    # serving-endpoint URL as ``self_hosted`` and the player renders a
    # ``<video>``. The view enforces ``can_access`` then 302-redirects to a
    # short-lived presigned S3 URL — the presigned URL never appears in HTML.
    path(
        'events/<int:event_id>/<slug:slug>/recording.mp4',
        event_recording_stream,
        name='event_recording_stream',
    ),
    # Issue #673: canonical event detail. ``event_id`` is the lookup key;
    # ``slug`` is purely cosmetic. The view verifies the slug matches the
    # stored value and 301s to the canonical form on a mismatch.
    path(
        'events/<int:event_id>/<slug:slug>',
        event_detail,
        name='event_detail',
    ),
    # Issue #673: id-only route redirects to the canonical id+slug URL.
    # ``/events/<id>/`` (with the trailing slash) is normalised first
    # by the site-wide ``RemoveTrailingSlashMiddleware`` (a 301 to
    # ``/events/<id>``), then this view 301s on to the canonical
    # id+slug URL — two cheap permanent redirects rather than a custom
    # trailing-slash variant.
    path(
        'events/<int:event_id>',
        event_detail_no_slug_redirect,
        name='event_detail_no_slug',
    ),
    # API endpoints for registration
    # Issue #857: series registration. Registered BEFORE the per-event
    # ``api/events/<slug>/register`` route so the literal ``series``
    # segment is never swallowed by the slug converter. POST registers
    # for the whole series (fan-out); DELETE drops the standing flag and
    # all future occurrences.
    path(
        'api/events/series/<slug:series_slug>/register',
        series_registration,
        name='event_series_register',
    ),
    path(
        'api/events/<slug:slug>/register',
        register_for_event,
        name='event_register',
    ),
    path(
        'api/events/<slug:slug>/unregister',
        unregister_from_event,
        name='event_unregister',
    ),
    path(
        'api/events/<slug:slug>/cancel-registration',
        cancel_registration_action,
        name='event_cancel_registration_action',
    ),
]
