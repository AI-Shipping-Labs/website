"""Calendar invite (.ics) generation for events.

Three surfaces share the same per-event payload:

- ``generate_ics(event)`` builds a single-event ``VCALENDAR`` with
  ``METHOD:REQUEST`` — used for the per-event ``.ics`` attachment in
  registration emails and the per-event download at
  ``/events/<slug>/calendar.ics`` (issue #484).
- ``generate_series_ics(events, method)`` builds a multi-event
  ``VCALENDAR`` WITH a ``METHOD`` property — used for the series
  subscriber invite (issue #869) so a whole series lands in the
  recipient's calendar from one email, and to UPDATE/CANCEL it when
  occurrences change. The multi-VEVENT sibling of ``generate_ics``.
- ``generate_feed_ics(events_qs)`` builds a multi-event ``VCALENDAR``
  with NO ``METHOD`` property — used for the subscribable platform-wide
  feed at ``/events/calendar.ics`` (issue #578). Subscribed clients
  treat a ``METHOD`` header as a republish, so we deliberately omit it.

All surfaces consume ``build_vevent(event)`` so the per-event payload
(UID, DTSTART/DTEND, DESCRIPTION truncation, SUMMARY prefix for external
events, URL/LOCATION shape) stays consistent.
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


def build_vevent(event):
    """Build a single ``VEVENT`` component for ``event``.

    Shared between ``generate_ics`` (single-event invite) and
    ``generate_feed_ics`` (platform-wide subscribable feed). Both
    surfaces need the same UID, DTSTART/DTEND, SEQUENCE, organizer,
    description, and URL/LOCATION shape so a calendar that subscribes
    to the feed AND received the invite email from the same event
    sees them as the same entry (de-duped by UID).

    Args:
        event: ``events.models.Event`` instance.

    Returns:
        ``icalendar.Event`` ready to be added to a calendar.
    """
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
    # Issue #726: tier-gated events appear in the public feed with a
    # ``[Members only]`` SUMMARY prefix so a subscriber can tell at a
    # glance which sessions need a paid tier to attend. Both the
    # ``[Members only]`` and ``[Hosted on X]`` prefixes may apply; the
    # documented order is ``[Members only] [Hosted on X] <title>``.
    required_level = getattr(event, 'required_level', 0) or 0
    is_gated = required_level > 0
    summary = event.title
    if is_external:
        summary = f'[Hosted on {external_host}] {summary}'
    if is_gated:
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

    # Stable UID per event. Per RFC 5545 the UID must be globally
    # stable across iCal client reloads — DO NOT swap this for
    # SITE_BASE_URL. If the apex domain changes between dev/prod, a
    # calendar that received the dev invite would treat the prod invite
    # as a different event instead of an update.
    vevent.add('uid', f'event-{event.slug}@aishippinglabs.com')

    site_url = site_base_url()
    # Issue #673: route the canonical detail URL through
    # ``Event.get_absolute_url`` so the .ics ``URL`` field follows the
    # new ``/events/<id>/<slug>`` shape automatically. The ``UID``
    # above intentionally keeps the slug-only form — UIDs are stable
    # globally-unique identifiers and changing them would break
    # already-issued calendar invites.
    absolute_url = getattr(event, 'get_absolute_url', lambda: '')()
    if absolute_url:
        detail_url = f'{site_url}{absolute_url}'
    else:
        # Defensive fallback for stub events (e.g. a SimpleNamespace
        # without ``id``) used in some integration tests.
        detail_url = f'{site_url}/events/{event.slug}'

    # DESCRIPTION — plain text body plus a final ``Join:`` line so the
    # URL is visible in clients that hide ``URL`` / ``LOCATION``.
    #
    # Issue #726: for tier-gated events we REPLACE the body with a
    # short stub (title + "members-only" sentence + detail URL). The
    # anonymous feed is cacheable by third-party services and we
    # treat its contents as public; gated event bodies stay behind
    # the auth gate on the detail page.
    if is_gated:
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
            body = f'{description}\n\nJoin: {detail_url}'
        else:
            body = f'Join: {detail_url}'
    vevent.add('description', body)

    # URL points to the public detail page (announcement landing). The
    # ``/join`` redirect requires login, so it would 302 a subscriber
    # client and many feed consumers refuse to follow redirects.
    vevent.add('url', detail_url)

    # LOCATION mirrors the partner platform for external events (the
    # actual surface the user joins on) and the detail URL for
    # community events (parity with ``URL`` for clients that hide
    # ``URL``).
    if is_external:
        vevent.add('location', vText(external_host))
    else:
        vevent.add('location', vText(detail_url))

    # Organizer — pulled from the transactional sender so the
    # subscribed entry shows the same "AI Shipping Labs" identity as
    # the per-event email invites.
    from_email = get_sender_for_kind(EMAIL_KIND_TRANSACTIONAL)
    organizer = vCalAddress(f'mailto:{from_email}')
    organizer.params['cn'] = vText('AI Shipping Labs')
    vevent.add('organizer', organizer)

    return vevent


def generate_ics(event, method='REQUEST'):
    """Generate a single-event ``.ics`` calendar file.

    Used for the per-event invite attachment in registration emails and
    for the per-event download at ``/events/<slug>/calendar.ics``.

    Args:
        event: ``Event`` model instance.
        method: iCalendar method (``REQUEST`` for new/update,
            ``CANCEL`` for cancellation).

    Returns:
        bytes: The ``.ics`` file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events//EN')
    cal.add('version', '2.0')
    cal.add('method', method)
    cal.add_component(build_vevent(event))
    return cal.to_ical()


def generate_series_ics(events, method='REQUEST'):
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

    Returns:
        bytes: The ``.ics`` file content.
    """
    cal = Calendar()
    cal.add('prodid', '-//AI Shipping Labs//Events//EN')
    cal.add('version', '2.0')
    cal.add('method', method)
    for event in events:
        cal.add_component(build_vevent(event))
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
        cal.add_component(build_vevent(event))

    return cal.to_ical()
