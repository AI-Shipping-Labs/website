"""Middleware for capturing UTM campaign visits.

Captures `utm_*` query params, persists first-touch in a long-lived cookie,
last-touch in the session, assigns a stable anonymous_id cookie, and writes
one CampaignVisit row per UTM-bearing request.
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from django.conf import settings

from analytics.bots import is_bot

logger = logging.getLogger(__name__)


# --- Constants -----------------------------------------------------------

UTM_PARAMS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')

FIRST_TOUCH_COOKIE = 'aslab_ft'
ANON_ID_COOKIE = 'aslab_aid'
SESSION_LAST_TOUCH = 'aslab_lt'

COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90 days
COOKIE_SAMESITE = 'Lax'

# Paths excluded from tracking. Match by `startswith`.
SKIP_PATH_PREFIXES = (
    '/admin/',
    '/static/',
    '/media/',
    '/api/webhooks/',
    '/healthz',
    '/favicon.ico',
    '/sitemap.xml',
    '/robots.txt',
)

# Field max-lengths come from the model. Kept here to avoid a model import in
# the hot path.
_MAX_LENS = {
    'utm_source': 100,
    'utm_medium': 100,
    'utm_campaign': 200,
    'utm_content': 200,
    'utm_term': 200,
}
_PATH_MAX = 500
_REFERRER_MAX = 500
_USER_AGENT_MAX = 500


def _cookie_secure():
    return not settings.DEBUG


def _cookie_domain():
    """Return the domain to use for analytics cookies.

    Reuses `SESSION_COOKIE_DOMAIN` so analytics cookies share scope with
    Django's session cookie. None / empty means default (current host only).
    """
    domain = (
        getattr(settings, 'ANALYTICS_COOKIE_DOMAIN', None)
        or getattr(settings, 'SESSION_COOKIE_DOMAIN', None)
    )
    return domain or None


# --- Helpers -------------------------------------------------------------

def _client_ip(request):
    """Return the client IP, preferring X-Forwarded-For first hop."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''


def _hash_ip(ip):
    """Return SHA-256(ip + IP_HASH_SALT). Empty if salt not configured."""
    salt = getattr(settings, 'IP_HASH_SALT', '') or ''
    if not salt:
        return ''
    if not ip:
        return ''
    return hashlib.sha256((ip + salt).encode('utf-8')).hexdigest()


def _normalize_utm_value(raw, max_len):
    """Lowercase, strip leading/trailing whitespace, and truncate."""
    if raw is None:
        return ''
    value = str(raw).strip().lower()
    if len(value) > max_len:
        value = value[:max_len]
    return value


def _extract_utms(request):
    """Pull utm_* params from request.GET and normalize each value."""
    utms = {}
    for key in UTM_PARAMS:
        utms[key] = _normalize_utm_value(request.GET.get(key, ''), _MAX_LENS[key])
    return utms


def _is_valid_uuid(value):
    if not value:
        return False
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _should_skip(request):
    """Fast-path filter: drop requests we never want to track."""
    if request.method not in ('GET', 'HEAD'):
        return True
    path = request.path or ''
    for prefix in SKIP_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    if is_bot(request.META.get('HTTP_USER_AGENT', '')):
        return True
    return False


def _enqueue_visit(**kwargs):
    """Enqueue record_visit via jobs.async_task. Falls back to inline call.

    When `Q_CLUSTER['sync'] = True` (test mode, set via env `Q_SYNC=true`),
    we call the task inline so the visit row exists before the response is
    returned — assertions in TestCase can run immediately afterwards.
    If for some reason the queue layer is unavailable, fall back to a direct
    synchronous call so we never lose attribution data.
    """
    q_config = getattr(settings, 'Q_CLUSTER', {}) or {}
    if q_config.get('sync'):
        from analytics.tasks import record_visit
        try:
            record_visit(**kwargs)
        except Exception:  # pragma: no cover — defensive
            logger.exception('Inline record_visit failed')
        return

    try:
        from jobs.tasks import async_task
        async_task('analytics.tasks.record_visit', **kwargs)
    except Exception:  # pragma: no cover — defensive only
        logger.exception('Failed to enqueue record_visit; running inline')
        from analytics.tasks import record_visit
        record_visit(**kwargs)


# --- Middleware ----------------------------------------------------------

class CampaignTrackingMiddleware:
    """Capture UTM params, persist first/last touch, log a CampaignVisit row."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Fast-path skip: bots, non-GET, system paths.
        if _should_skip(request):
            return self.get_response(request)

        # 1. Ensure anonymous_id (cookie) — set on response if missing/invalid.
        existing_aid = request.COOKIES.get(ANON_ID_COOKIE, '')
        if _is_valid_uuid(existing_aid):
            anon_id = existing_aid
            set_anon_cookie = False
        else:
            anon_id = str(uuid.uuid4())
            set_anon_cookie = True

        # 2. Extract UTMs.
        utms = _extract_utms(request)
        has_any_utm = any(utms.values())

        first_touch_payload = None  # JSON to set on response if first-touch missing
        if has_any_utm:
            now_iso = datetime.now(timezone.utc).isoformat()
            current_touch = {
                'source': utms['utm_source'],
                'medium': utms['utm_medium'],
                'campaign': utms['utm_campaign'],
                'content': utms['utm_content'],
                'term': utms['utm_term'],
                'ts': now_iso,
            }

            # 3. First-touch: only set if not already present (sticky).
            if not request.COOKIES.get(FIRST_TOUCH_COOKIE):
                first_touch_payload = current_touch

            # 4. Last-touch: always overwrite session value.
            try:
                request.session[SESSION_LAST_TOUCH] = current_touch
            except Exception:  # pragma: no cover — defensive
                logger.exception('Failed to set last-touch on session')

            # 5. Enqueue visit row write.
            user_id = None
            try:
                if request.user.is_authenticated:
                    user_id = request.user.pk
            except AttributeError:
                # AuthenticationMiddleware not active; treat as anonymous.
                pass

            _enqueue_visit(
                utm_source=utms['utm_source'],
                utm_medium=utms['utm_medium'],
                utm_campaign=utms['utm_campaign'],
                utm_content=utms['utm_content'],
                utm_term=utms['utm_term'],
                path=(request.path or '')[:_PATH_MAX],
                referrer=(request.META.get('HTTP_REFERER', '') or '')[:_REFERRER_MAX],
                user_agent=(request.META.get('HTTP_USER_AGENT', '') or '')[:_USER_AGENT_MAX],
                ip_hash=_hash_ip(_client_ip(request)),
                anonymous_id=anon_id,
                user_id=user_id,
            )

        # 6. Get the response.
        response = self.get_response(request)

        # 7. Set cookies on the response.
        cookie_kwargs = {
            'max_age': COOKIE_MAX_AGE,
            'samesite': COOKIE_SAMESITE,
            'httponly': True,
            'secure': _cookie_secure(),
            'domain': _cookie_domain(),
        }

        if set_anon_cookie:
            response.set_cookie(ANON_ID_COOKIE, anon_id, **cookie_kwargs)

        if first_touch_payload is not None:
            response.set_cookie(
                FIRST_TOUCH_COOKIE,
                json.dumps(first_touch_payload),
                **cookie_kwargs,
            )

        return response
