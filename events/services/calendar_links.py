"""One-click "Add to Calendar" deep-link URL builders for events.

This complements the ``.ics`` attachment (see
``events/services/calendar_invite.py``) with provider-specific deep links so
recipients can add an event to Google Calendar, Outlook.com, or Microsoft
365 with a single click.

The ``.ics`` attachment remains the universal fallback for clients that
neither vendor's deep-link covers (Apple Calendar, Thunderbird, etc.).
"""

import datetime
from datetime import timedelta
from urllib.parse import quote

from integrations.config import site_base_url

GOOGLE_CALENDAR_BASE = 'https://calendar.google.com/calendar/render'
OUTLOOK_LIVE_BASE = 'https://outlook.live.com/calendar/0/deeplink/compose'
OUTLOOK_OFFICE_BASE = 'https://outlook.office.com/calendar/0/deeplink/compose'

# Cap the description we paste into deep-link URLs so the resulting URL stays
# well below typical mail-client URL length limits (Outlook Web ~2KB, Gmail
# ~8KB). 2000 chars leaves headroom for the join-line suffix and base URL.
MAX_DETAILS_DESCRIPTION_CHARS = 2000


def _to_utc(dt):
    """Return ``dt`` converted to UTC. Assumes ``dt`` is timezone-aware."""
    return dt.astimezone(datetime.timezone.utc)


def _format_google_dt(dt):
    """Format a UTC datetime as ``YYYYMMDDTHHMMSSZ`` for Google Calendar."""
    return _to_utc(dt).strftime('%Y%m%dT%H%M%SZ')


def _format_iso_utc(dt):
    """Format a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ`` for Outlook."""
    return _to_utc(dt).strftime('%Y-%m-%dT%H:%M:%SZ')


def _build_details(event, join_url):
    """Build the description body for the deep-link URLs.

    Always includes a "Join: <url>" line so the join URL is visible in
    Google's description field even on viewers that hide ``location``.
    Truncates ``event.description`` at ``MAX_DETAILS_DESCRIPTION_CHARS``
    to keep the resulting URL under mail-client size limits.
    """
    description = (event.description or '').strip()
    if len(description) > MAX_DETAILS_DESCRIPTION_CHARS:
        description = description[:MAX_DETAILS_DESCRIPTION_CHARS]

    if description:
        return f'{description}\n\nJoin: {join_url}'
    return f'Join: {join_url}'


def _qs(pairs):
    """Build a query string from an ordered list of (key, value) pairs.

    Each value is URL-encoded with ``safe=''`` so reserved characters
    (``&``, ``=``, ``?``, ``/``) get percent-escaped. Keeping insertion
    order makes the URLs deterministic for tests.
    """
    return '&'.join(f'{k}={quote(str(v), safe="")}' for k, v in pairs)


def build_calendar_links(event):
    """Return one-click "Add to Calendar" URLs for ``event``.

    Args:
        event: Event model instance with ``title``, ``slug``,
            ``start_datetime``, ``end_datetime``, and ``description``.

    Returns:
        dict with keys ``google``, ``outlook``, ``office365`` mapping to
        deep-link URLs. ``end_datetime`` defaults to ``start_datetime + 1
        hour`` to match ``generate_ics`` so all calendar entries agree on
        the duration.
    """
    site_url = site_base_url()
    join_url = f'{site_url}/events/{event.slug}/join'

    end_dt = event.end_datetime or (event.start_datetime + timedelta(hours=1))

    title = event.title
    details = _build_details(event, join_url)

    google_dates = (
        f'{_format_google_dt(event.start_datetime)}/'
        f'{_format_google_dt(end_dt)}'
    )

    google_url = f'{GOOGLE_CALENDAR_BASE}?' + _qs([
        ('action', 'TEMPLATE'),
        ('text', title),
        ('dates', google_dates),
        ('details', details),
        ('location', join_url),
    ])

    outlook_params = [
        ('path', '/calendar/action/compose'),
        ('rru', 'addevent'),
        ('subject', title),
        ('startdt', _format_iso_utc(event.start_datetime)),
        ('enddt', _format_iso_utc(end_dt)),
        ('body', details),
        ('location', join_url),
    ]

    outlook_url = f'{OUTLOOK_LIVE_BASE}?' + _qs(outlook_params)
    office365_url = f'{OUTLOOK_OFFICE_BASE}?' + _qs(outlook_params)

    return {
        'google': google_url,
        'outlook': outlook_url,
        'office365': office365_url,
    }
