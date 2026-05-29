"""Pytest wiring for the live LLM-judge set (issue #811).

Responsibilities:

- Session lifecycle: reset the cost file on session start, print the
  per-model + total usage summary on session finish.
- Skip-not-error: when ``integrations.services.llm.is_enabled()`` is
  False, skip every collected test in this set (so ``make test-judge``
  with no key reports skips, makes zero live calls, and exits cleanly --
  never a hard error).
- Logfire-off: assert no Logfire span exporter is active during a
  live-judge run. #813 owns the actual gating; this set must inherit it
  and never light up production observability. Until #813 lands we simply
  do not enable Logfire here and assert it stays off.

Django is configured via the ``DJANGO_SETTINGS_MODULE`` in
``pyproject.toml`` ``[tool.pytest.ini_options]``; we call
``django.setup()`` defensively so ``integrations.config`` /
``settings_registry`` resolve.
"""

import os

import django
import pytest

# Ensure Django settings are loaded before any project import that reads
# config (pyproject sets DJANGO_SETTINGS_MODULE; this is belt-and-braces).
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
try:
    django.setup()
except Exception:
    # Already configured by pytest-django; ignore.
    pass

from integrations.services import llm  # noqa: E402

from .cost_tracker import display_total_usage, reset_cost_file  # noqa: E402

# Reason shown when the whole set is skipped for lack of an LLM key.
SKIP_REASON = (
    'live_judge: LLM is not configured (llm.is_enabled() is False); '
    'set LLM_API_KEY to run the live judge set. Zero live calls made.'
)


def pytest_sessionstart(session):
    reset_cost_file()


def pytest_sessionfinish(session, exitstatus):
    display_total_usage()


def _llm_enabled(config):
    """Resolve ``llm.is_enabled()``, unblocking DB access if pytest-django blocks it.

    ``is_enabled()`` reads config which can touch the DB cache table.
    pytest-django blocks DB access during collection, so we briefly unblock
    around the read when its fixture is available.
    """
    blocker = None
    try:
        from pytest_django.plugin import blocking_manager_key

        blocker = config.stash.get(blocking_manager_key, None)
    except Exception:
        blocker = None
    if blocker is None:
        return llm.is_enabled()
    with blocker.unblock():
        return llm.is_enabled()


def pytest_collection_modifyitems(config, items):
    """Skip the whole live_judge set when the LLM service is disabled.

    This runs at collection time so a no-key machine reports every test as
    skipped and never makes a live provider call.
    """
    if _llm_enabled(config):
        return
    skip_marker = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if item.get_closest_marker('live_judge'):
            item.add_marker(skip_marker)


def _logfire_span_exporter_active():
    """Return True if a Logfire/OpenTelemetry span exporter looks active.

    Defensive: Logfire is an optional dependency. If it is not installed,
    or no tracer provider with a real (non-no-op) exporter is configured,
    this returns False. We never import Logfire to enable it -- only to
    confirm it is NOT exporting.
    """
    try:
        from opentelemetry import trace as otel_trace
    except Exception:
        return False
    provider = otel_trace.get_tracer_provider()
    # The default no-op provider has no span processors.
    processor = getattr(provider, '_active_span_processor', None)
    if processor is None:
        return False
    span_processors = getattr(processor, '_span_processors', None)
    return bool(span_processors)


@pytest.fixture(autouse=True)
def _assert_logfire_off():
    """Assert no Logfire span exporter fires during a live-judge test."""
    assert not _logfire_span_exporter_active(), (
        'A Logfire/OpenTelemetry span exporter is active during a '
        'live-judge run; this set must not emit to Logfire (#813).'
    )
    yield
