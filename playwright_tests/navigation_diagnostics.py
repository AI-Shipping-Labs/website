"""Best-effort diagnostics for residual Playwright ``Page.goto`` timeouts."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright_tests.local_capacity import sample_host_headroom
from playwright_tests.worktree_guard import SECRET_ARG_HINTS, current_xdist_worker_id

DIAGNOSTIC_SECTION_TITLE = "Playwright Page.goto timeout diagnostics"
REQUEST_JOURNAL_SIZE = 64
PING_TIMEOUT_SECONDS = 1.0
SENSITIVE_QUERY_HINTS = (*SECRET_ARG_HINTS, "cookie", "key", "session", "sig")
_NAVIGATING_URL = re.compile(r'navigating to ["\'](?P<url>[^"\']+)["\']', re.IGNORECASE)
_WAIT_STATE = re.compile(r'waiting until ["\'](?P<state>[^"\']+)["\']', re.IGNORECASE)
_DJANGO_REQUEST_LINE = re.compile(r'^"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[^" ]+" (?P<status>\d{3})')


def redact_url(value: str) -> str:
    """Redact credentials and sensitive query values without losing the route."""
    if not value or value == "unavailable":
        return "unavailable"
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except (ValueError, AttributeError):
        return "<redacted-url>"

    netloc = host
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{host}"
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        redacted = "<redacted>" if any(hint in lowered for hint in SENSITIVE_QUERY_HINTS) else item
        query.append((key, redacted))
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query, safe="<>"), parsed.fragment))


def _redacted_path(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-path>"
    result = parsed.path or "/"
    if parsed.query:
        safe = redact_url(value)
        result = urlsplit(safe).path
        query = urlsplit(safe).query
        if query:
            result = f"{result}?{query}"
    return result


class DjangoRequestJournal(logging.Handler):
    """Small thread-safe ring buffer of completed Django server requests."""

    def __init__(self, size=REQUEST_JOURNAL_SIZE):
        super().__init__()
        self.records = deque(maxlen=size)
        self._record_lock = threading.Lock()

    def emit(self, record):
        try:
            request = getattr(record, "request", None)
            match = None
            if request is not None and hasattr(request, "get_full_path"):
                method = getattr(request, "method", None) or "unavailable"
                path = _redacted_path(request.get_full_path())
            else:
                match = _DJANGO_REQUEST_LINE.match(record.getMessage())
                method = match.group("method") if match else "unavailable"
                raw_path = match.group("path") if match else getattr(record, "request_path", "unavailable")
                path = _redacted_path(str(raw_path))
            status = getattr(record, "status_code", None)
            if status is None and match:
                status = int(match.group("status"))
            item = {"method": str(method), "path": path, "status": status}
        except Exception:  # noqa: BLE001 - diagnostics must never affect requests
            item = {"method": "unavailable", "path": "unavailable", "status": "unavailable"}
        with self._record_lock:
            self.records.append(item)

    def snapshot(self):
        with self._record_lock:
            return list(self.records)


def is_page_goto_timeout(call) -> bool:
    if getattr(call, "excinfo", None) is None:
        return False
    exception = call.excinfo.value
    return isinstance(exception, PlaywrightTimeoutError) and "Page.goto" in str(exception)


def _navigation_details(exception) -> tuple[str, str]:
    message = str(exception)
    url_match = _NAVIGATING_URL.search(message)
    wait_match = _WAIT_STATE.search(message)
    return (
        redact_url(url_match.group("url")) if url_match else "unavailable",
        wait_match.group("state") if wait_match else "unavailable",
    )


def _safe_provider(provider, unavailable):
    try:
        return provider()
    except Exception as exc:  # noqa: BLE001 - preserve the original Playwright failure
        return {**unavailable, "error": f"unavailable ({type(exc).__name__})"}


def _server_state(config) -> dict:
    thread = getattr(config, "_playwright_django_server_thread", None)
    base_url = getattr(config, "_playwright_django_server_base_url", None)
    if thread is None or base_url is None:
        return {"alive": "unavailable", "base_url": "unavailable"}
    try:
        alive = bool(thread.is_alive())
    except Exception:  # noqa: BLE001
        alive = "unavailable"
    return {"alive": alive, "base_url": redact_url(base_url)}


def _ping_state(config, *, opener=urllib.request.urlopen, monotonic=time.monotonic) -> dict:
    base_url = getattr(config, "_playwright_django_server_base_url", None)
    if not base_url:
        return {"status": "unavailable", "latency_ms": "unavailable", "error": "local server unavailable"}
    destination = urljoin(f"{base_url.rstrip('/')}/", "ping")
    started = monotonic()
    try:
        with opener(destination, timeout=PING_TIMEOUT_SECONDS) as response:
            status = response.status
        return {"status": status, "latency_ms": round((monotonic() - started) * 1000, 1), "error": None}
    except Exception as exc:  # noqa: BLE001 - diagnostic probe cannot replace the original error
        return {
            "status": "unavailable",
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "error": f"unavailable ({type(exc).__name__})",
        }


def _browser_state(config) -> dict:
    browser = getattr(config, "_playwright_browser", None)
    if browser is None:
        return {"connected": "unavailable", "contexts": "unavailable", "pages": "unavailable"}
    connected = browser.is_connected()
    contexts = list(browser.contexts)
    return {
        "connected": bool(connected),
        "contexts": len(contexts),
        "pages": sum(len(context.pages) for context in contexts),
    }


def _capacity_state(config) -> dict:
    capacity = getattr(config, "_playwright_local_capacity", None)
    if capacity is not None:
        return capacity.diagnostic_state()
    workerinput = getattr(config, "workerinput", {}) or {}
    state = workerinput.get("playwright_capacity")
    if isinstance(state, dict):
        return state
    return {
        "requested_slots": "unavailable",
        "capacity": "unavailable",
        "granted": "unavailable",
        "wait_seconds": "unavailable",
    }


def _request_state(config) -> list[dict]:
    journal = getattr(config, "_playwright_request_journal", None)
    return journal.snapshot() if journal is not None else []


def _classification(server, ping, browser, requests, destination, host, capacity) -> str:
    target_path = urlsplit(destination).path if destination != "unavailable" else None
    target_statuses = [
        entry.get("status")
        for entry in requests
        if target_path and entry.get("path", "").split("?", 1)[0] == target_path
    ]
    high_pressure = False
    cpus = host.get("logical_cpus")
    load1 = host.get("load_average_1m")
    memory_pressure = host.get("memory_full_avg60")
    requested = capacity.get("requested_slots", 1)
    if not isinstance(requested, int):
        requested = 1
    if isinstance(cpus, int) and isinstance(load1, (int, float)):
        high_pressure = load1 + requested > cpus
    if isinstance(memory_pressure, (int, float)) and memory_pressure > 10:
        high_pressure = True
    starvation = (
        server.get("alive") is True
        and ping.get("status") == 200
        and 200 in target_statuses
        and browser.get("connected") is True
        and high_pressure
    )
    if starvation:
        return "evidence supports browser/host starvation; original node remains failed"
    return "application or harness evidence requires investigation; original node remains failed"


def build_navigation_diagnostics(item, call, *, providers=None) -> str:
    """Return a structured, redacted section without raising diagnostic errors."""
    providers = providers or {}
    config = item.config
    destination, wait_state = _navigation_details(call.excinfo.value)

    host = _safe_provider(
        providers.get("host", lambda: sample_host_headroom().diagnostic()),
        {
            "logical_cpus": "unavailable",
            "load_average_1m": "unavailable",
            "load_average_5m": "unavailable",
            "load_average_15m": "unavailable",
            "available_memory_bytes": "unavailable",
            "swap_total_bytes": "unavailable",
            "swap_used_bytes": "unavailable",
            "cpu_pressure": "unavailable",
            "memory_pressure": "unavailable",
            "memory_full_avg60": "unavailable",
        },
    )
    server = _safe_provider(
        providers.get("server", lambda: _server_state(config)), {"alive": "unavailable", "base_url": "unavailable"}
    )
    ping = _safe_provider(
        providers.get("ping", lambda: _ping_state(config)),
        {"status": "unavailable", "latency_ms": "unavailable"},
    )
    browser = _safe_provider(
        providers.get("browser", lambda: _browser_state(config)),
        {"connected": "unavailable", "contexts": "unavailable", "pages": "unavailable"},
    )
    capacity = _safe_provider(providers.get("capacity", lambda: _capacity_state(config)), {})
    requests = _safe_provider(providers.get("requests", lambda: _request_state(config)), {"records": "unavailable"})
    if not isinstance(requests, list):
        requests_for_classification = []
    else:
        requests_for_classification = requests

    result = {
        "browser": browser,
        "capacity": capacity,
        "classification": _classification(
            server,
            ping,
            browser,
            requests_for_classification,
            destination,
            host,
            capacity,
        ),
        "host": host,
        "navigation": {"destination": destination, "wait_until": wait_state},
        "node_id": item.nodeid,
        "pytest_phase": getattr(call, "when", "unavailable"),
        "ping": ping,
        "recent_django_requests": requests,
        "server": server,
        "xdist_worker_id": current_xdist_worker_id() or "controller/serial",
    }
    return json.dumps(result, indent=2, sort_keys=True, default=str)


def append_navigation_diagnostics(report, item, call):
    """Append one report section for a failed ``Page.goto`` timeout only."""
    if not getattr(report, "failed", False) or not is_page_goto_timeout(call):
        return
    try:
        section = build_navigation_diagnostics(item, call)
    except Exception as exc:  # noqa: BLE001 - final guard against obscuring the test traceback
        section = json.dumps(
            {
                "diagnostics": f"unavailable ({type(exc).__name__})",
                "node_id": getattr(item, "nodeid", "unavailable"),
                "outcome": "original node remains failed",
            },
            indent=2,
            sort_keys=True,
        )
    report.sections.append((DIAGNOSTIC_SECTION_TITLE, section))
