"""Gate for Pydantic Logfire observability (issue #813).

Logfire is production-only. It must NOT fire in the Django test suite, in
the #809 eval harness (``manage.py run_ai``), in the #811 live-judge
pytest set, or in any local/dev run unless an operator deliberately opts
in. :func:`logfire_is_enabled` is the single three-part AND gate that
enforces this; both the startup initializer in :mod:`integrations.apps`
and any future caller route through it.

The gate returns ``True`` only when ALL hold:

1. ``settings.TESTING`` is ``False`` (the Django test suite sets
   ``TESTING = 'test' in sys.argv``).
2. A non-empty ``LOGFIRE_TOKEN`` resolves via ``get_config``.
3. ``is_enabled('LOGFIRE_ENABLED')`` is ``True`` (default ``false`` —
   explicit opt-in).

Clause 3 is the load-bearing guard for the eval/judge paths: neither
``manage.py run_ai`` nor the live-judge pytest set runs under
``manage.py test`` (so ``TESTING`` is ``False`` there), and neither sets
``LOGFIRE_ENABLED=true``, so the explicit-enable clause keeps them silent.
"""

import logging

from django.conf import settings

from integrations.config import get_config, is_enabled

logger = logging.getLogger(__name__)


def logfire_is_enabled():
    """Return ``True`` only when Logfire should initialize (prod-only gate).

    All three conditions must hold: not running tests, a non-empty
    ``LOGFIRE_TOKEN`` is configured, and ``LOGFIRE_ENABLED`` is true.
    """
    if getattr(settings, 'TESTING', False):
        return False
    if not get_config('LOGFIRE_TOKEN', ''):
        return False
    return is_enabled('LOGFIRE_ENABLED')


def init_logfire():
    """Configure Logfire and enable auto-instrumentation, once, behind the gate.

    Returns immediately when :func:`logfire_is_enabled` is ``False`` — no
    ``logfire`` import side effects, no network, no ``configure()`` call
    when the gate is closed. When the gate is open, calls
    ``logfire.configure(...)`` once and enables instrumentation for Django,
    outbound HTTP (httpx + requests), and the Anthropic SDK when that
    instrumentor exists. Any misconfiguration or missing optional
    instrumentor is logged and swallowed so app boot never crashes.

    Called once per process from ``IntegrationsConfig.ready()`` (gunicorn
    workers + qcluster each configure their own exporter). Returns ``True``
    when configuration ran, ``False`` when the gate kept it closed.
    """
    if not logfire_is_enabled():
        return False

    try:
        import logfire  # noqa: PLC0415

        logfire.configure(
            token=get_config('LOGFIRE_TOKEN', ''),
            environment=get_config('LOGFIRE_ENVIRONMENT', 'production'),
        )
        _instrument(logfire)
    except Exception:  # noqa: BLE001 — never let observability crash boot
        logger.warning('Failed to initialize Logfire', exc_info=True)
        return False
    return True


def _instrument(logfire):
    """Enable available auto-instrumentors without crashing on a missing one.

    Django request traces, outbound HTTP (httpx covers the Anthropic SDK
    transport; requests covers other clients), and — when the installed
    Logfire exposes it — the Anthropic SDK directly. Each instrumentor is
    guarded independently so one missing/failing helper does not disable
    the rest or crash boot.
    """
    for name in ('instrument_django', 'instrument_httpx', 'instrument_requests',
                 'instrument_anthropic'):
        instrumentor = getattr(logfire, name, None)
        if instrumentor is None:
            continue
        try:
            instrumentor()
        except Exception:  # noqa: BLE001 — optional instrumentor, log and continue
            logger.warning('Logfire %s failed', name, exc_info=True)
