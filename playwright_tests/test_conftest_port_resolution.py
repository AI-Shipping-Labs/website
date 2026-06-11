"""Unit tests for the Playwright local-server port resolver (conftest.py).

Pure-Python tests for ``_resolved_local_port`` / ``_parse_port_override``:
they manipulate the ``PLAYWRIGHT_DJANGO_PORT`` env var and the memoized
``_LOCAL_PORT`` global, never starting a browser, DB, or dev server. Tagged
``core`` so they run on every push via ``make test-playwright-core`` and guard
the regression in issue #911 (``PLAYWRIGHT_DJANGO_PORT=0`` must auto-pick a
free port, not bind literal port 0).
"""

import pytest

from playwright_tests import conftest

pytestmark = pytest.mark.core


@pytest.fixture(autouse=True)
def _reset_resolver(monkeypatch):
    """Clear the memoized port and the env var around each test."""
    monkeypatch.setattr(conftest, "_LOCAL_PORT", None)
    monkeypatch.delenv("PLAYWRIGHT_DJANGO_PORT", raising=False)
    yield
    conftest._LOCAL_PORT = None


def _is_usable_ephemeral(port):
    """A resolved auto-picked port must be a real bindable positive port."""
    return isinstance(port, int) and 0 < port <= 65535


def test_zero_falls_back_to_free_port(monkeypatch):
    """``PLAYWRIGHT_DJANGO_PORT=0`` must auto-pick, never bind literal 0 (#911)."""
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "0")
    port = conftest._resolved_local_port()
    assert port != 0
    assert _is_usable_ephemeral(port)


@pytest.mark.parametrize("raw", ["-1", "-5000", "abc", "8000x", "  ", "70000"])
def test_invalid_or_nonpositive_falls_back_to_free_port(monkeypatch, raw):
    """Negative, out-of-range, whitespace, and garbage overrides auto-pick."""
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", raw)
    port = conftest._resolved_local_port()
    assert _is_usable_ephemeral(port)


def test_unset_falls_back_to_free_port(monkeypatch):
    """No override at all picks a free ephemeral port."""
    monkeypatch.delenv("PLAYWRIGHT_DJANGO_PORT", raising=False)
    port = conftest._resolved_local_port()
    assert _is_usable_ephemeral(port)


def test_valid_positive_port_is_honored_verbatim(monkeypatch):
    """A valid positive override is used exactly as given."""
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8123")
    assert conftest._resolved_local_port() == 8123


def test_resolved_port_is_memoized(monkeypatch):
    """Once resolved, the same port is returned even if the env var changes."""
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8123")
    first = conftest._resolved_local_port()
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "9999")
    assert conftest._resolved_local_port() == first
