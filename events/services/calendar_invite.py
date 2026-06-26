"""Calendar invite (.ics) generation for events.

Three surfaces share the same per-event payload builder with explicit
audience-specific URL rules:

- ``generate_ics(event, audience='attendee')`` builds a single-event
  ``VCALENDAR`` with ``METHOD:REQUEST`` — used for attendee ``.ics``
  attachments and the per-event download at
  ``/events/<slug>/calendar.ics`` (issue #484). Attendee community
  events use the id-canonical ``/events/<id>/<slug>/join`` redirect
  (issue #1082) in DESCRIPTION, URL, and LOCATION so raw Zoom links stay
  hidden behind the gated join flow.
- ``generate_series_ics(events, method)`` builds a multi-event
  ``VCALENDAR`` WITH a ``METHOD`` property — used for the series
  subscriber invite (issue #869) so a whole series lands in the
  recipient's calendar from one email, and to UPDATE/CANCEL it when
  occurrences change. The multi-VEVENT sibling of ``generate_ics``.
- ``generate_feed_ics(events_qs)`` builds a multi-event ``VCALENDAR``
  with NO ``METHOD`` property — used for the subscribable platform-wide
  feed at ``/events/calendar.ics`` (issue #578). It explicitly uses the
  ``public_feed`` audience, keeping public detail URLs and anonymous-safe
  gated descriptions because feed subscribers are unauthenticated.

Host-only invites explicitly use the ``host`` audience: they share the
same UID/SEQUENCE as attendee invites but keep non-attendee public detail
URLs rather than switching to the attendee join flow.
"""

from datetime import timedelta

from django.utils import timezone
from icalendar import Calendar, vCalAddress, vText
from icalendar import Event as ICalEvent

from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    get_sender_for_kind,
)
from integrations.config import site_base_url

# Cap the description we paste into VEVENT DESCRIPTION fields so a giant
# event body cannot bloat the feed beyond mail-client and subscription-
# client URL/body size limits. Mirrors the rule in
# ``events/services/calendar_links.py`` (issue #577).
MAX_DESCRIPTION_CHARS = 2000

AUDIENCE_ATTENDEE = 'attendee'
AUDIENCE_PUBLIC_FEED = 'public_feed'
AUDIENCE_HOST = 'host'
VALID_AUDIENCES = {
    AUDIENCE_ATTENDEE,
    AUDIENCE_PUBLIC_FEED,
    AUDIENCE_HOST,
}


def _event_urls(event):
    """Return absolute public detail and attendee join URLs for ``event``."""
    site_url = site_base_url()
    absolute_url = getattr(event, 'get_absolute_url', lambda: '')()
    if absolute_url:
        detail_url = f'{site_url}{absolute_url}'
    else:
        # Defensive fallback for stub events (e.g. a SimpleNamespace
        # without ``id``) used in some integration tests.
        detail_url = f'{site_url}/events/{event.slug}'
    # Issue #1082: id-canonical join URL via ``Event.get_join_url``. The
    # same defensive fallback for stub events keeps the slug-only shape
    # (still a live alias route) when the helper or id is unavailable.
    join_path = getattr(event, 'get_join_url', lambda: '')()
    if join_path:
        join_url = f'{site_url}{join_path}'
    else:
        join_url = f'{site_url}/events/{event.slug}/join'
    return detail_url, join_url


def build_vevent(event, audience=AUDIENCE_ATTENDEE, attendee_email=None,
                 method='REQUEST'):
    """Build a single ``VEVENT`` component for ``event``.

    Shared between attendee invites, host invites, and the public feed.
    All audiences keep the same UID, DTSTART/DTEND, SEQUENCE, organizer,
    and summary shape so calendar clients update one entry by UID. URL,
    LOCATION, and gated DESCRIPTION behavior vary by audience:
    attendee community events use ``/events/<id>/<slug>/join``; public feed
    events use public detail URLs and anonymous-safe gated stubs; host
    invites use the non-attendee public detail URL.

    Args:
        event: ``events.models.Event`` instance.
        audience: One of ``attendee``, ``public_feed``, or ``host``.
        attendee_email: Optional recipient email for emailed attendee invites.
        method: iCalendar method. ``CANCEL`` adds ``STATUS:CANCELLED``.

    Returns:
        ``icalendar.Event`` ready to be added to a calendar.
    """
    if audience not in VALID_AUDIENCES:
        raise ValueError(f'Unknown calendar invite audience: {audience}')

    vevent = ICalEvent()

    # SUMMARY — prefix external events with ``[Hosted on X]`` so a
    # subscriber knows where the session actually happens.
    # ``is_external`` is a model property, but tests sometimes stub
    # ``event`` as a SimpleNamespace (see
    # ``integrations.tests.test_runtime_integration_config``); fall
    # back to deriving it from ``external_host`` so those stubs still
    # work without having to add the property.
    is_external = getattr(event, 'is_external', None)
    if is_external is None:
        is_external = bool(getattr(event, 'external_host', '') or '')
    external_host = getattr(event, 'external_host', '') or ''
    # Issue #726 / #1072: the ``[Members only]`` SUMMARY prefix on
    # tier-gated events (``required_level > 0``) is PUBLIC-FEED-ONLY. It
    # exists solely for discovery on the anonymous, unauthenticated feed
    # at ``/events/calendar.ics`` so a subscriber who never registered can
    # tell at a glance which sessions need a paid tier. It is deliberately
    # NOT added to attendee or host invites (registration email + per-event
    # ``.ics`` download, host invite, series invite/cancel, reschedule
    # invite): those recipients already registered or host the event and
    # know the tier, so the prefix is noise there (issue #1072).
    #
    # The ``[Hosted on X]`` prefix for external events is a separate sibling
    # prefix and still applies to ALL audiences. When both apply on the
    # public feed the documented order is ``[Members only] [Hosted on X]
    # <title>``; on attendee/host invites it is ``[Hosted on X] <title>``.
    required_level = getattr(event, 'required_level', 0) or 0
    is_gated = required_level > 0
    summary = event.title
    if is_external:
        summary = f'[Hosted on {external_host}] {summary}'
    if is_gated and audience == AUDIENCE_PUBLIC_FEED:
        summary = f'[Members only] {summary}'
    vevent.add('summary', summary)

    vevent.add('dtstart', event.start_datetime)

    # Issue #712: ``Event.effective_end_datetime`` is the single source
    # of truth for "when did this event end?" — ``end_datetime`` when
    # set, otherwise ``start + 1h``. Falls back to an inline expression
    # for stub events (e.g. SimpleNamespace) that don't expose the
    # property; integration tests under ``integrations.tests`` rely on
    # this.
    effective_end = getattr(event, 'effective_end_datetime', None)
    if effective_end is None:
        effective_end = event.end_datetime or (
            event.start_datetime + timedelta(hours=1)
        )
    vevent.add('dtend', effective_end)

    vevent.add('dtstamp', timezone.now())
    vevent.add('sequence', event.ics_sequence)
    if method == 'CANCEL':
        vevent.add('status', 'CANCELLED')

    # Stable UID per event. Per RFC 5545 the UID must be globally
    # stable across iCal client reloads — DO NOT swap this for
    # SITE_BASE_URL. If the apex domain changes between dev/prod, a
    # calendar that received the dev invite would treat the prod invite
    # as a different event instead of an update.
    vevent.add('uid', f'event-{event.slug}@aishippinglabs.com')

    detail_url, join_url = _event_urls(event)
    if audience == AUDIENCE_ATTENDEE:
        community_url = join_url
    else:
        community_url = detail_url
    description_url = detail_url if is_external else community_url

    # DESCRIPTION — plain text body plus a final ``Join:`` line so the
    # URL is visible in clients that hide ``URL`` / ``LOCATION``.
    #
    # Issue #726: for tier-gated events we REPLACE the body with a
    # short stub in the public feed only (title + "members-only"
    # sentence + detail URL). The anonymous feed is cacheable by
    # third-party services and we treat its contents as public; gated
    # event bodies stay behind the auth gate on the detail page.
    if audience == AUDIENCE_PUBLIC_FEED and is_gated:
        body = (
            f'{event.title}\n\n'
            'This is a members-only event. Membership required to '
            'register and attend.\n'
            f'Details: {detail_url}'
        )
    else:
        description = (event.description or '').strip()
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS]
        if description:
            body = f'{description}\n\nJoin: {description_url}'
        else:
            body = f'Join: {description_url}'
    vevent.add('description', body)

    # Attendee community invites point at the gated/tracked join redirect.
    # Public feed and host invites keep the public detail URL because they
    # are non-attendee surfaces. External events keep existing public
    # detail/external-host behavior unless a real attendee path exists.
    if is_external:
        vevent.add('url', detail_url)
    else:
        vevent.add('url', community_url)

    # LOCATION mirrors the partner platform for external events (the
    # actual surface the user joins on) and the audience-specific URL for
    # community events (parity with ``URL`` for clients that hide ``URL``).
    if is_external:
        vevent.add('location', vText(external_host))
    else:
        vevent.add('location', vText(community_url))

    # Organizer — pulled from the transactional sender so the
    # subscribed entry shows the same "AI Shipping Labs" identity as
    # the per-event email invites.
    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
    organizer = vCalAddress(f'mailto:{from_email}')
    organizer.params['cn'] = vText('AI Shipping Labs')
    vevent.add('organizer', organizer)

    if attendee_email:
        attendee = vCalAddress(f'mailto:{attendee_email}')
        attendee.params['cn'] = vText(attendee_email)
        attendee.params['role'] = vText('REQ-PARTICIPANT')
        attendee.params['partstat'] = vText('ACCEPTED')
        attendee.params['rsvp'] = vText('FALSE')
        vevent.add('attendee', attendee)

    return vevent


def generate_ics(event, method='REQUEST', audience=AUDIENCE_ATTENDEE,
                 attendee_email=None):
    """Generate a single-event ``.ics`` calendar file.

    Used for the per-event invite attachment in registration emails and
    for the per-event download at ``/events/<slug>/calendar.ics``.

    Args:
        event: ``Event`` model instance.
        method: iCalendar method (``REQUEST`` for new/update,
            ``CANCEL`` for cancellation).
        audience: ``attendee`` by default. Use ``host`` for host-only
            invites that should not switch to the attendee join flow.
        attendee_email: Optional recipient email to stamp as ``ATTENDEE``.

    Returns:
        bytes: The ``.ics`` file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events//EN')
    cal.add('version', '2.0')
    cal.add('method', method)
    cal.add_component(
        build_vevent(
            event,
            audience=audience,
            attendee_email=attendee_email,
            method=method,
        ),
    )
    return cal.to_ical()


def generate_series_ics(events, method='REQUEST', audience=AUDIENCE_ATTENDEE,
                        attendee_email=None):
    """Generate a multi-event ``.ics`` invite covering several occurrences.

    Used by the series subscriber invite (issue #869): when a member
    registers for a whole series (or the series changes) we send ONE
    ``.ics`` containing a ``VEVENT`` per upcoming occurrence so the
    entire series lands in the recipient's calendar from a single email.

    Unlike ``generate_feed_ics`` (the subscribable platform feed, which
    deliberately omits ``METHOD`` so clients do not treat each fetch as a
    republish), this carries a ``METHOD`` property (``REQUEST`` for a
    new/updated invite, ``CANCEL`` to remove). It is the multi-VEVENT
    sibling of ``generate_ics``.

    Each ``VEVENT`` reuses ``build_vevent(event)``, so every occurrence
    keeps its stable per-event UID (``event-<slug>@aishippinglabs.com``)
    and its own ``ics_sequence``. Calendar clients de-dupe by UID, so an
    occurrence received via both the per-event invite and this series
    invite merges into one entry, and a bumped SEQUENCE on a later send
    UPDATEs that entry rather than duplicating it.

    The series invite intentionally does NOT introduce a series-level
    UID — the per-event UID + SEQUENCE stay the single source of truth.

    Args:
        events: Iterable of ``Event`` rows to include (caller owns the
            inclusion query — accessibility, upcoming, etc.).
        method: iCalendar method (``REQUEST`` for new/update,
            ``CANCEL`` for cancellation).
        audience: ``attendee`` by default for subscriber invites.
        attendee_email: Optional recipient email to stamp as ``ATTENDEE``
            on every ``VEVENT``.

    Returns:
        bytes: The ``.ics`` file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events//EN')
    cal.add('version', '2.0')
    cal.add('method', method)
    for event in events:
        cal.add_component(
            build_vevent(
                event,
                audience=audience,
                attendee_email=attendee_email,
                method=method,
            ),
        )
    return cal.to_ical()


def generate_feed_ics(events_qs):
    """Generate a multi-event ``VCALENDAR`` feed.

    Used by the platform-wide subscribable feed at
    ``/events/calendar.ics`` (issue #578). Subscribed clients
    (Apple Calendar, Google Calendar, Outlook) refresh this URL on
    their own polling schedule, so the calendar carries hints
    (``REFRESH-INTERVAL``, ``X-PUBLISHED-TTL``) about a reasonable
    cadence and intentionally does NOT include a ``METHOD`` property —
    ``METHOD`` is for invites/publishes; a feed with ``METHOD`` causes
    some clients to treat every fetch as a re-publish and prompt the
    user.

    Args:
        events_qs: Iterable of ``Event`` rows to include. Caller is
            responsible for the inclusion query (published, status
            filters, time window, tier filter).

    Returns:
        bytes: The ``.ics`` file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events Feed//EN')
    cal.add('version', '2.0')

    # Display metadata for client UIs (Apple Calendar, Outlook).
    cal.add('x-wr-calname', 'AI Shipping Labs Events')
    cal.add(
        'x-wr-caldesc',
        'All community events, workshops, and meetups from AI Shipping Labs.',
    )
    cal.add('x-wr-timezone', 'UTC')

    # Refresh hints. ``REFRESH-INTERVAL`` is the standard RFC 7986
    # property; ``X-PUBLISHED-TTL`` is the Outlook/Microsoft variant.
    # ``PT1H`` = one hour. We can't actually force Google to refresh
    # faster than its internal 12-24h cycle, but the hint is honored by
    # most other clients.
    cal.add('refresh-interval;value=duration', 'PT1H')
    cal.add('x-published-ttl', 'PT1H')

    for event in events_qs:
        cal.add_component(build_vevent(event, audience=AUDIENCE_PUBLIC_FEED))

    return cal.to_ical()
