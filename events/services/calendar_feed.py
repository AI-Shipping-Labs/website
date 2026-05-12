"""Helpers for the platform-wide subscribable events calendar feed.

Issue #578. The feed itself is built in
``events.services.calendar_invite.generate_feed_ics``; this module owns
the inclusion query (what rows belong in the feed), the subscribe URL
builder (Google/Apple/copy-feed), and the HTTP cache-key helpers.

Kept separate from ``calendar_invite.py`` so the per-event invite
generation stays focused on the per-event payload and this module owns
all the feed-only policy (30-day backfill window, gating exclusions,
URL encoding).
"""

from datetime import timedelta
from urllib.parse import quote

from django.utils import timezone

# 30-day backfill window. Subscribers who add the feed today should see
# recent past sessions whose recordings are worth following up on; older
# history stays out of the feed to keep it small.
FEED_BACKFILL_DAYS = 30


def feed_events_queryset(now=None):
    """Return the queryset of events that belong in the public feed.

    Inclusion rules (issue #578):

    - ``published = True``
    - ``status in ('upcoming', 'completed')`` — never ``draft``,
      never ``cancelled``
    - ``start_datetime >= now - 30 days``
    - ``required_level == 0`` — gated events stay invisible in the
      public feed; member-aware feeds with signed tokens are a
      deliberate follow-up

    Ordering is ``start_datetime`` ascending so the file reads
    chronologically when opened by a human.
    """
    # Lazy import to keep this module importable from places that don't
    # have the events app fully wired up yet (e.g. tests that import
    # only the URL builders below).
    from events.models import Event

    if now is None:
        now = timezone.now()
    window_start = now - timedelta(days=FEED_BACKFILL_DAYS)

    return Event.objects.filter(
        published=True,
        status__in=('upcoming', 'completed'),
        start_datetime__gte=window_start,
        required_level=0,
    ).order_by('start_datetime')


def _host_from_site_url(site_url):
    """Strip scheme from ``site_url`` to get the bare host.

    ``site_base_url()`` returns e.g. ``https://aishippinglabs.com``;
    ``webcal://`` and Google's ``cid`` parameter both want the
    scheme-less host so the client substitutes its own scheme.
    """
    # Cheap and dependency-free: strip the first '://' if present.
    if '://' in site_url:
        return site_url.split('://', 1)[1]
    return site_url


def build_subscribe_urls(site_url=None):
    """Return the dict of subscribe URLs for the platform-wide feed.

    Keys:

    - ``feed_https`` — canonical ``https://HOST/events/calendar.ics``
      URL, useful for the "Copy feed URL" affordance and for
      ``Last-Modified`` / ``ETag`` debugging.
    - ``feed_webcal`` — same URL with the ``webcal://`` scheme; macOS
      and iOS register Apple Calendar as the default handler for this
      scheme.
    - ``google`` — Google Calendar deep-link
      (``https://calendar.google.com/calendar/r?cid=<urlencoded
      webcal:// URL>``). The ``cid`` value MUST be URL-encoded with
      ``safe=''`` so the ``:`` and ``/`` characters survive the round
      trip; Google's UI shows a confirmation prompt before adding.
    - ``apple`` — alias of ``feed_webcal``; named ``apple`` in the
      template so the option text reads cleanly.

    Args:
        site_url: Optional override of the resolved site base URL,
            mainly for tests. When ``None``, reads from
            ``integrations.config.site_base_url()``.
    """
    if site_url is None:
        # Lazy import: pulling integrations at module-import time
        # would force settings to be configured for any consumer.
        from integrations.config import site_base_url
        site_url = site_base_url()

    host = _host_from_site_url(site_url)
    feed_https = f'{site_url}/events/calendar.ics'
    feed_webcal = f'webcal://{host}/events/calendar.ics'
    google = (
        'https://calendar.google.com/calendar/r?cid='
        + quote(feed_webcal, safe='')
    )
    return {
        'feed_https': feed_https,
        'feed_webcal': feed_webcal,
        'google': google,
        'apple': feed_webcal,
    }
