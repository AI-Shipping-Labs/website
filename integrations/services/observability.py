"""Production-only Pydantic Logfire initialization.

``IntegrationsConfig.ready()`` runs before Django marks the app registry ready.
That path must stay independent of IntegrationSetting and the shared database
cache, so it resolves Logfire's three settings from the Django settings
snapshot, the process environment, and the registered defaults only. Serving
containers make one additional, post-``django.setup()`` pass that may use the
normal DB-backed integration configuration.
"""

import logging
import os

from django.conf import settings

from integrations.config import get_config
from integrations.settings_registry import get_group_by_name

logger = logging.getLogger(__name__)

_logfire_initialized = False
_TRUTHY_VALUES = frozenset({'true', '1', 'yes'})
_LOGFIRE_KEYS = (
    'LOGFIRE_ENABLED',
    'LOGFIRE_TOKEN',
    'LOGFIRE_ENVIRONMENT',
)


def _registered_logfire_defaults():
    """Return the defaults declared for the Observability settings group."""
    group = get_group_by_name('observability')
    definitions = {item['key']: item for item in group['keys']}
    return {
        key: definitions[key].get('default', '')
        for key in _LOGFIRE_KEYS
    }


def _get_boot_config(key, defaults):
    """Resolve one key without touching IntegrationSetting or shared caches."""
    setting_value = getattr(settings, key, None)
    if setting_value is not None:
        return setting_value

    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value

    return defaults[key]


def _resolved_logfire_config(*, use_runtime_config=False):
    """Resolve the three Logfire values for app-init or serving runtime."""
    defaults = _registered_logfire_defaults()
    if use_runtime_config:
        resolver = lambda key: get_config(key, defaults[key])
    else:
        resolver = lambda key: _get_boot_config(key, defaults)

    return {key: resolver(key) for key in _LOGFIRE_KEYS}


def _is_truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY_VALUES


def logfire_is_enabled(*, use_runtime_config=False):
    """Return whether the production-only Logfire gate is open.

    The default app-init path never reads the database. Serving entrypoints may
    opt into the normal IntegrationSetting-backed resolution after
    ``django.setup()`` completes.
    """
    if getattr(settings, 'TESTING', False):
        return False

    config = _resolved_logfire_config(use_runtime_config=use_runtime_config)
    return bool(config['LOGFIRE_TOKEN']) and _is_truthy(
        config['LOGFIRE_ENABLED'],
    )


def init_logfire(*, use_runtime_config=False):
    """Configure and instrument Logfire at most once in this process.

    ``use_runtime_config=False`` is the safe app-init mode. Passing ``True`` is
    reserved for a serving entrypoint after Django's app registry is ready and
    allows Studio's IntegrationSetting values to enable Logfire on restart.
    Failures are logged and swallowed so observability never prevents boot.
    """
    global _logfire_initialized

    if _logfire_initialized or getattr(settings, 'TESTING', False):
        return False

    config = _resolved_logfire_config(use_runtime_config=use_runtime_config)
    if not config['LOGFIRE_TOKEN'] or not _is_truthy(
        config['LOGFIRE_ENABLED'],
    ):
        return False

    try:
        import logfire  # noqa: PLC0415

        logfire.configure(
            token=config['LOGFIRE_TOKEN'],
            environment=config['LOGFIRE_ENVIRONMENT'],
        )
        _instrument(logfire)
    except Exception:  # noqa: BLE001 -- never let observability crash boot
        logger.warning('Failed to initialize Logfire', exc_info=True)
        return False

    _logfire_initialized = True
    return True


def _instrument(logfire):
    """Enable available auto-instrumentors without crashing on a missing one."""
    for name in (
        'instrument_django',
        'instrument_httpx',
        'instrument_requests',
        'instrument_anthropic',
    ):
        instrumentor = getattr(logfire, name, None)
        if instrumentor is None:
            continue
        try:
            instrumentor()
        except Exception:  # noqa: BLE001 -- optional instrumentor
            logger.warning('Logfire %s failed', name, exc_info=True)
