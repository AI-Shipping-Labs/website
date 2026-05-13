"""Signal handlers that snapshot UTM attribution at signup time.

Wired in `analytics.apps.AnalyticsConfig.ready`. The single chokepoint is
the `post_save` signal on the `User` model with `created=True` — every
code path that creates a user (email/password registration, newsletter
subscribe, Stripe webhook, allauth social signup, admin createsuperuser,
management commands) goes through `User.save`, so this handler always
fires exactly once per new user.

Reading the request:
- The capture middleware in #193 sets the active request on a thread-local
  in `analytics.request_context`. This handler reads from that thread-local
  to access UTM cookies and the session. When no request is bound (Stripe
  webhook, management command, admin user creation), we fall back to
  `signup_path='unknown'` (or `stripe_checkout` if the Stripe flag is set)
  and skip backfill silently.

Reading first/last touch:
- First-touch: a long-lived cookie `aslab_ft` containing JSON with the keys
  `source, medium, campaign, content, term, ts`.
- Last-touch: a session key `aslab_lt` with the same shape, written on
  every UTM-bearing request so it always reflects the most recent touch.
- If neither is present, the row is created with empty strings and null
  timestamps. We do NOT copy first-touch into last-touch — leaving
  last-touch empty lets dashboards distinguish "returned via UTM" from
  "returned direct".

Backfill:
- If we have an `anonymous_id` cookie, run a single SQL UPDATE on
  `CampaignVisit` rows with that anonymous_id and `user_id IS NULL` to
  attribute the user's prior anonymous browsing to them. The
  `user_id__isnull=True` filter is critical — it prevents this user from
  "stealing" visits already linked to a different user (e.g. shared
  device).

Cross-device limitation: a user who landed via UTM on phone but signed up
on desktop will get an empty attribution row. This is an accepted v1
limitation — we do not try to cross-device match.
"""

import json
import logging
from datetime import datetime

from django.db import DatabaseError, transaction

from analytics.middleware import (
    ANON_ID_COOKIE,
    FIRST_TOUCH_COOKIE,
    SESSION_LAST_TOUCH,
)
from analytics.models import CampaignVisit, UserAttribution
from analytics.request_context import (
    consume_stripe_user_creation,
    get_current_request,
)
from integrations.models import UtmCampaign

logger = logging.getLogger(__name__)


def _resolve_campaign(slug):
    """Look up a UtmCampaign by slug. Returns None if absent or empty."""
    if not slug:
        return None
    return UtmCampaign.objects.filter(slug=slug).first()


def _read_first_touch(request):
    """Parse the `aslab_ft` cookie. Returns dict or None."""
    if request is None:
        return None
    raw = request.COOKIES.get(FIRST_TOUCH_COOKIE, '')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        logger.warning('Could not parse %s cookie: %r', FIRST_TOUCH_COOKIE, raw)
        return None


def _read_last_touch(request):
    """Read the `aslab_lt` session value. Returns dict or None."""
    if request is None:
        return None
    try:
        return request.session.get(SESSION_LAST_TOUCH)
    except AttributeError:
        return None


def _read_anonymous_id(request):
    """Return the `aslab_aid` cookie value, or empty string."""
    if request is None:
        return ''
    return request.COOKIES.get(ANON_ID_COOKIE, '') or ''


def _resolve_signup_path(request, stripe_flag):
    """Resolve the `signup_path` based on flags and request path."""
    if stripe_flag:
        return 'stripe_checkout'
    if request is None:
        return 'unknown'
    path = request.path or ''
    if path.startswith('/api/register'):
        return 'email_password'
    if path.startswith('/api/subscribe'):
        return 'newsletter'
    return 'unknown'


def _truncate(value, max_len=255):
    """Trim to the model field max length, defensive against odd cookie data."""
    if value is None:
        return ''
    return str(value)[:max_len]


def create_user_attribution(sender, instance, created, **kwargs):
    """post_save handler on `User`. Snapshots UTMs on creation only.

    Tolerant of every failure mode (missing request, missing session, bad
    cookie JSON) — never raises, because raising would break user creation
    in every signup path.
    """
    if not created:
        return

    request = get_current_request()
    stripe_flag = consume_stripe_user_creation()

    first_touch = _read_first_touch(request) or {}
    last_touch = _read_last_touch(request) or {}
    anon_id = _read_anonymous_id(request)
    signup_path = _resolve_signup_path(request, stripe_flag)

    first_campaign_slug = _truncate(first_touch.get('campaign', ''))
    last_campaign_slug = _truncate(last_touch.get('campaign', ''))

    try:
        with transaction.atomic():
            attribution = UserAttribution.objects.create(
                user=instance,
                first_touch_utm_source=_truncate(first_touch.get('source', '')),
                first_touch_utm_medium=_truncate(first_touch.get('medium', '')),
                first_touch_utm_campaign=first_campaign_slug,
                first_touch_utm_content=_truncate(first_touch.get('content', '')),
                first_touch_utm_term=_truncate(first_touch.get('term', '')),
                first_touch_campaign=_resolve_campaign(first_campaign_slug),
                first_touch_ts=_parse_iso_ts(first_touch.get('ts')),
                last_touch_utm_source=_truncate(last_touch.get('source', '')),
                last_touch_utm_medium=_truncate(last_touch.get('medium', '')),
                last_touch_utm_campaign=last_campaign_slug,
                last_touch_utm_content=_truncate(last_touch.get('content', '')),
                last_touch_utm_term=_truncate(last_touch.get('term', '')),
                last_touch_campaign=_resolve_campaign(last_campaign_slug),
                last_touch_ts=_parse_iso_ts(last_touch.get('ts')),
                signup_path=signup_path,
                anonymous_id=_truncate(anon_id, max_len=64),
            )
    except DatabaseError:
        # Never break user creation. Log and move on.
        logger.exception(
            'Failed to create UserAttribution for user_id=%s', instance.pk
        )
        return

    # Backfill prior anonymous CampaignVisit rows. Only touches rows with
    # user_id NULL so we never steal visits attributed to another user.
    if anon_id:
        try:
            CampaignVisit.objects.filter(
                anonymous_id=anon_id,
                user_id__isnull=True,
            ).update(user_id=instance.pk)
        except DatabaseError:
            logger.exception(
                'Failed to backfill CampaignVisit for user_id=%s anon_id=%s',
                instance.pk, anon_id,
            )

    return attribution


def _parse_iso_ts(value):
    """Parse an ISO 8601 timestamp string into a datetime, or None."""
    if not value:
        return None
    try:
        # `datetime.fromisoformat` handles offsets including +00:00 in 3.11+.
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def update_signup_path_for_social_signup(sender, request, user, **kwargs):
    """allauth `user_signed_up` handler — refines signup_path for OAuth flows.

    `post_save` fires before this signal during a social signup, so the
    `UserAttribution` row already exists with `signup_path='unknown'`. Here
    we look at the social provider attached to the signup and rewrite the
    field to one of the OAuth values.
    """
    sociallogin = kwargs.get('sociallogin')
    if sociallogin is None:
        # Plain (non-social) signup via allauth — leave the row alone.
        return

    provider = getattr(sociallogin.account, 'provider', '') or ''
    provider_to_path = {
        'slack': 'slack_oauth',
        'slack_openid_connect': 'slack_oauth',
        'google': 'google_oauth',
        'github': 'github_oauth',
    }
    new_path = provider_to_path.get(provider)
    if not new_path:
        return

    try:
        UserAttribution.objects.filter(user_id=user.pk).update(
            signup_path=new_path,
        )
    except DatabaseError:
        logger.exception(
            'Failed to update signup_path for social user_id=%s provider=%s',
            user.pk, provider,
        )


__all__ = [
    'create_user_attribution',
    'update_signup_path_for_social_signup',
]
