"""Per-token load shedding for staff API token calls (issue #1854).

Two limits apply to every request authenticated with a staff API token
(``accounts.auth.token_required``); Studio and other session traffic is never
throttled:

* a token-bucket rate: ``STAFF_API_RATE_LIMIT_PER_MINUTE`` requests per
  minute, with a burst of ``BURST_SECONDS`` worth of requests;
* a concurrency cap: at most ``STAFF_API_MAX_CONCURRENT_PER_TOKEN`` requests
  from one token in flight at once.

Rejections are 429 with ``Retry-After`` and a ``code`` the ``asl`` CLI keys
its backoff on. Admission happens before expensive token verification, keyed
by a SHA-256 digest of the complete submitted credential, and the slot stays
held through the view. Retrying a rejection is therefore safe for every method.

State is in-process on purpose, unlike the cross-worker
``accounts.services.auth_throttle``. The goal is protecting this process's
request threads, not abuse accounting, and a shared DatabaseCache counter
would add RDS round trips to every API call exactly when the site is under
load. Each gunicorn worker therefore enforces the limits independently: with
3 workers a token can reach up to 3x the configured numbers in total, while
never holding more than the cap of any one worker's threads.

Inactive rate buckets expire after they fully refill and their cardinality is
bounded. In-flight credential digests are never evicted, so storage cleanup
cannot open another concurrent slot. No raw credential enters limiter state.

Both limits are off under ``settings.TESTING`` unless a test configures them,
so unrelated API tests that share one token are never throttled.
"""

import hashlib
import logging
import math
import threading
import time
from contextlib import contextmanager

from django.conf import settings
from django.http import JsonResponse

from integrations.config import get_config

logger = logging.getLogger(__name__)

RATE_KEY = "STAFF_API_RATE_LIMIT_PER_MINUTE"
CONCURRENCY_KEY = "STAFF_API_MAX_CONCURRENT_PER_TOKEN"
DEFAULT_RATE_PER_MINUTE = 120
DEFAULT_MAX_CONCURRENT = 2
BURST_SECONDS = 10
MAX_TRACKED_CREDENTIALS = 4096

CODE_RATE_LIMITED = "rate_limited"
CODE_TOO_MANY_CONCURRENT = "too_many_concurrent_requests"
THROTTLE_CODES = frozenset({CODE_RATE_LIMITED, CODE_TOO_MANY_CONCURRENT})

_lock = threading.Lock()
_buckets = {}
_in_flight = {}


def _resolve_limit(key, default):
    """Return a non-negative int config value; ``0`` disables the limit."""
    if getattr(settings, "TESTING", False):
        default = 0
    raw = get_config(key, str(default))
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; falling back to %s", key, default)
        return default
    if value < 0:
        logger.warning("Invalid %s value; falling back to %s", key, default)
        return default
    return value


def _credential_digest(credential):
    """Return the non-secret limiter key for one submitted credential."""
    return hashlib.sha256(credential.encode("utf-8")).digest()


def _prune_rate_buckets(now, rate_per_minute):
    """Drop inactive buckets whose rate state has completely refilled."""
    if rate_per_minute:
        refill_seconds = max(BURST_SECONDS, 60 / rate_per_minute)
    else:
        refill_seconds = 0
    stale = []
    for credential_key, (_, updated_at) in _buckets.items():
        if credential_key in _in_flight:
            continue
        if now - updated_at >= refill_seconds:
            stale.append(credential_key)
    for credential_key in stale:
        _buckets.pop(credential_key, None)


def _make_bucket_room():
    """Bound attacker-controlled bucket cardinality without evicting active keys."""
    while len(_buckets) >= MAX_TRACKED_CREDENTIALS:
        evictable = None
        for credential_key in _buckets:
            if credential_key not in _in_flight:
                evictable = credential_key
                break
        if evictable is None:
            return
        _buckets.pop(evictable, None)


def _take_rate_token(credential_key, rate_per_minute, now):
    """Spend one bucket token; return seconds to wait when empty, else 0."""
    refill_per_second = rate_per_minute / 60
    capacity = max(1.0, refill_per_second * BURST_SECONDS)
    if credential_key not in _buckets:
        _make_bucket_room()
    tokens, updated_at = _buckets.get(credential_key, (capacity, now))
    tokens = min(capacity, tokens + (now - updated_at) * refill_per_second)
    if tokens < 1:
        _buckets[credential_key] = (tokens, now)
        return math.ceil((1 - tokens) / refill_per_second)
    _buckets[credential_key] = (tokens - 1, now)
    return 0


def _throttled(code, retry_after):
    if code == CODE_RATE_LIMITED:
        message = "Too many requests for this API token."
    else:
        message = "Too many concurrent requests for this API token."
    response = JsonResponse(
        {"error": f"{message} Retry after {retry_after}s.", "code": code},
        status=429,
    )
    response["Retry-After"] = str(retry_after)
    return response


def _admit(credential_key):
    """Return ``(rejection_response_or_None, counted_in_flight)``."""
    max_concurrent = _resolve_limit(CONCURRENCY_KEY, DEFAULT_MAX_CONCURRENT)
    rate_per_minute = _resolve_limit(RATE_KEY, DEFAULT_RATE_PER_MINUTE)
    with _lock:
        now = time.monotonic()
        _prune_rate_buckets(now, rate_per_minute)
        in_flight = _in_flight.get(credential_key, 0)
        if max_concurrent and in_flight >= max_concurrent:
            return _throttled(CODE_TOO_MANY_CONCURRENT, 1), False
        if rate_per_minute:
            wait = _take_rate_token(
                credential_key,
                rate_per_minute,
                now,
            )
            if wait:
                return _throttled(CODE_RATE_LIMITED, wait), False
        _in_flight[credential_key] = in_flight + 1
    return None, True


def _release(credential_key):
    with _lock:
        remaining = _in_flight.get(credential_key, 0) - 1
        if remaining > 0:
            _in_flight[credential_key] = remaining
        else:
            _in_flight.pop(credential_key, None)


@contextmanager
def staff_api_slot(credential):
    """Hold a pre-auth slot keyed by a digest of ``credential``."""
    credential_key = _credential_digest(credential)
    rejection, counted = _admit(credential_key)
    try:
        yield rejection
    finally:
        if counted:
            _release(credential_key)


def reset_staff_api_throttle():
    """Forget every bucket and in-flight count (tests only)."""
    with _lock:
        _buckets.clear()
        _in_flight.clear()
