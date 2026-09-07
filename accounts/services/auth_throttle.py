"""Shared cross-worker throttle for public auth and mail JSON endpoints.

Issue #1516: login, register, password-reset request, and newsletter
subscribe share one primitive so limits, cache keys, 429 shape, and
logging cannot drift. Counters live in ``caches['django_q']`` (the
process-shared DatabaseCache in production) rather than the per-worker
``default`` LocMemCache.

Under ``settings.TESTING``, consumption is a no-op unless at least one
of the scope's IntegrationSetting / Django-settings / env keys is
configured. LocMem ``django_q`` is process-wide, and unrelated tests
that POST these endpoints would otherwise trip production defaults.
Authoritative throttle tests lower the limits via IntegrationSetting.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.http import JsonResponse

from integrations.config import get_config
from website.request_ip import client_ip_from_request

logger = logging.getLogger(__name__)
User = get_user_model()

CACHE_ALIAS = "django_q"
CACHE_PREFIX = "auth-throttle"
UNKNOWN_IP_SENTINEL = "unknown"

SCOPE_LOGIN = "login"
SCOPE_REGISTER = "register"
SCOPE_RESET_REQUEST = "reset_request"
SCOPE_SUBSCRIBE = "subscribe"
VALID_SCOPES = frozenset(
    {SCOPE_LOGIN, SCOPE_REGISTER, SCOPE_RESET_REQUEST, SCOPE_SUBSCRIBE}
)
MAIL_SCOPES = frozenset(
    {SCOPE_REGISTER, SCOPE_RESET_REQUEST, SCOPE_SUBSCRIBE}
)

THROTTLED_ERROR = "Too many attempts. Please try again later."

DEFAULT_LOGIN_IP_LIMIT = 20
DEFAULT_LOGIN_EMAIL_LIMIT = 10
DEFAULT_LOGIN_WINDOW_SECONDS = 900
DEFAULT_MAIL_IP_LIMIT = 8
DEFAULT_MAIL_EMAIL_LIMIT = 3
DEFAULT_MAIL_WINDOW_SECONDS = 3600

LOGIN_IP_LIMIT_KEY = "AUTH_THROTTLE_LOGIN_IP_LIMIT"
LOGIN_EMAIL_LIMIT_KEY = "AUTH_THROTTLE_LOGIN_EMAIL_LIMIT"
LOGIN_WINDOW_KEY = "AUTH_THROTTLE_LOGIN_WINDOW_SECONDS"
MAIL_IP_LIMIT_KEY = "AUTH_THROTTLE_MAIL_IP_LIMIT"
MAIL_EMAIL_LIMIT_KEY = "AUTH_THROTTLE_MAIL_EMAIL_LIMIT"
MAIL_WINDOW_KEY = "AUTH_THROTTLE_MAIL_WINDOW_SECONDS"

LOGIN_CONFIG_KEYS = (
    LOGIN_IP_LIMIT_KEY,
    LOGIN_EMAIL_LIMIT_KEY,
    LOGIN_WINDOW_KEY,
)
MAIL_CONFIG_KEYS = (
    MAIL_IP_LIMIT_KEY,
    MAIL_EMAIL_LIMIT_KEY,
    MAIL_WINDOW_KEY,
)


def normalize_throttle_email(value):
    """Normalize email the same way user rows are stored, then lowercase."""
    return User.objects.normalize_email(str(value or "")).strip().lower()


def hash_throttle_value(value):
    """SHA-256 hex digest so raw IP/email never become cache keys."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def auth_throttle_cache_key(scope, bucket, digest):
    return f"{CACHE_PREFIX}:{scope}:{bucket}:{digest}"


def resolve_positive_int(key, default):
    """Parse an IntegrationSetting integer, falling back on bad values."""
    raw = get_config(key, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def resolve_auth_throttle_limits(scope):
    """Return ``(ip_limit, email_limit, window_seconds)`` for ``scope``."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unknown auth throttle scope: {scope}")
    if scope == SCOPE_LOGIN:
        return (
            resolve_positive_int(LOGIN_IP_LIMIT_KEY, DEFAULT_LOGIN_IP_LIMIT),
            resolve_positive_int(LOGIN_EMAIL_LIMIT_KEY, DEFAULT_LOGIN_EMAIL_LIMIT),
            resolve_positive_int(LOGIN_WINDOW_KEY, DEFAULT_LOGIN_WINDOW_SECONDS),
        )
    return (
        resolve_positive_int(MAIL_IP_LIMIT_KEY, DEFAULT_MAIL_IP_LIMIT),
        resolve_positive_int(MAIL_EMAIL_LIMIT_KEY, DEFAULT_MAIL_EMAIL_LIMIT),
        resolve_positive_int(MAIL_WINDOW_KEY, DEFAULT_MAIL_WINDOW_SECONDS),
    )


def clear_auth_throttle_cache():
    """Drop auth-throttle counters from the shared cache (tests only)."""
    cache = caches[CACHE_ALIAS]
    raw = getattr(cache, "_cache", None)
    expire_info = getattr(cache, "_expire_info", None)
    if not isinstance(raw, dict):
        return
    prefix = f"{CACHE_PREFIX}:"
    for stored_key in [key for key in raw if prefix in str(key)]:
        raw.pop(stored_key, None)
        if isinstance(expire_info, dict):
            expire_info.pop(stored_key, None)


def consume_auth_throttle(request, email, scope):
    """Increment IP then email buckets; return a 429 response or ``None``.

    Call after the view has confirmed the required fields are present and
    before hashing passwords, looking up users, creating rows, or sending
    mail. If the IP bucket is already over limit, the email bucket is not
    incremented.
    """
    if scope not in VALID_SCOPES:
        raise ValueError(f"Unknown auth throttle scope: {scope}")
    if getattr(settings, "TESTING", False) and not _scope_is_configured(scope):
        return None

    ip_limit, email_limit, window = resolve_auth_throttle_limits(scope)
    cache = caches[CACHE_ALIAS]
    ip_digest = hash_throttle_value(_client_ip_or_unknown(request))
    email_digest = hash_throttle_value(normalize_throttle_email(email))
    ip_key = auth_throttle_cache_key(scope, "ip", ip_digest)
    email_key = auth_throttle_cache_key(scope, "email", email_digest)

    ip_count = _increment_counter(cache, ip_key, window)
    if ip_count > ip_limit:
        return _throttled_response(cache, ip_key, window, scope, "ip")

    email_count = _increment_counter(cache, email_key, window)
    if email_count > email_limit:
        return _throttled_response(cache, email_key, window, scope, "email")
    return None


def _scope_config_keys(scope):
    if scope == SCOPE_LOGIN:
        return LOGIN_CONFIG_KEYS
    return MAIL_CONFIG_KEYS


def _scope_is_configured(scope):
    return any(_key_is_configured(key) for key in _scope_config_keys(scope))


def _key_is_configured(key):
    if settings.configured:
        settings_val = getattr(settings, key, None)
        if settings_val is not None and str(settings_val).strip() != "":
            return True
    env_val = os.environ.get(key)
    if env_val is not None and str(env_val).strip() != "":
        return True
    return bool(str(get_config(key, "")).strip())


def _client_ip_or_unknown(request):
    ip = client_ip_from_request(request)
    return ip or UNKNOWN_IP_SENTINEL


def _increment_counter(cache, key, window):
    if cache.add(key, 1, window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:
        cache.add(key, 1, window)
        return 1


def _throttled_response(cache, key, window, scope, bucket):
    logger.warning(
        "Auth throttle limit exceeded",
        extra={
            "throttle_scope": scope,
            "throttle_bucket": bucket,
        },
    )
    response = JsonResponse({"error": THROTTLED_ERROR}, status=429)
    response["Retry-After"] = str(_retry_after_seconds(cache, key, window))
    return response


def _retry_after_seconds(cache, key, window):
    remaining = _remaining_ttl(cache, key)
    if remaining is None or remaining <= 0:
        return int(window)
    return remaining


def _remaining_ttl(cache, key):
    ttl_fn = getattr(cache, "ttl", None)
    if callable(ttl_fn):
        try:
            remaining = ttl_fn(key)
        except Exception:
            remaining = None
        else:
            if remaining is not None:
                try:
                    remaining = int(remaining)
                except (TypeError, ValueError):
                    remaining = None
                else:
                    if remaining > 0:
                        return remaining
    expire_info = getattr(cache, "_expire_info", None)
    if not isinstance(expire_info, dict):
        return None
    made_key = cache.make_key(key)
    expires_at = expire_info.get(made_key)
    if not expires_at:
        return None
    remaining = int(expires_at - time.time())
    if remaining > 0:
        return remaining
    return None
