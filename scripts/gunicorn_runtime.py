"""Deploy-time serving settings for the gunicorn web process (issue #1854).

Every value here is read from ``os.environ`` on the gunicorn master BEFORE the
app serves a request, so none of them go through ``get_config``: the
IntegrationSetting override cannot be consulted pre-boot, and querying RDS
here would re-introduce the pre-bind DB round trip removed in #1141. A bad
value falls back to the default with a logged warning so a typo in the task
definition can never crash boot.

Why these defaults (prod is one 0.25 vCPU / 1 GB task):

* ``gthread`` with 3 workers x 4 threads gives 12 request slots for the same
  three processes (no extra memory per thread worth counting). Threads do not
  add CPU, but a thread blocked on Postgres or a slow staff call no longer
  holds the whole worker, so ``/ping`` and ordinary page views still find a
  free slot and the ALB health check keeps passing while the site is slow.
  12 slots also means at most 12 web DB connections, far below RDS limits.
* ``--timeout 30``: with ``gthread`` the heartbeat comes from the worker's
  main loop, so this restarts a worker whose loop is wedged rather than
  capping a single request. It stays below the default 60 s ALB idle
  timeout. Runaway SQL is capped separately by ``statement_timeout``.
* ``--graceful-timeout 20``: in-flight requests get 20 s to finish on a
  deploy or scale-in, which fits inside the default 30 s ECS stop timeout.
* ``--keep-alive 0``: close each connection after its response, exactly as
  the previous sync workers did. gthread's default 2 s keep-alive is shorter
  than the ALB idle timeout, which lets the ALB reuse a connection gunicorn
  is closing and return sporadic 502s.
* ``WEB_DB_STATEMENT_TIMEOUT_MS`` 10000: no web request should run a single
  statement for more than 10 s; anything longer is a runaway query that
  would otherwise hold a request slot and an RDS backend. ``0`` disables.
  The limit is installed by ``apply_web_statement_timeout`` right before the
  gunicorn handoff, never from ``settings.py``, so the django-q worker,
  ``migrate`` (including the legacy path that migrates in this same process
  first), and management commands keep Postgres' unlimited default. SQLite
  connections are left untouched.
"""

import logging
import os

from django.db import connections

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 3
DEFAULT_THREADS = 4
WORKER_TIMEOUT_SECONDS = 30
GRACEFUL_TIMEOUT_SECONDS = 20
KEEP_ALIVE_SECONDS = 0
DEFAULT_WEB_STATEMENT_TIMEOUT_MS = 10_000


def _int_env(name, default, *, minimum):
    """Return ``int(os.environ[name])`` or ``default`` when unset/invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = minimum - 1
    if value < minimum:
        logger.warning("Invalid %s=%r; falling back to %s", name, raw, default)
        return default
    return value


def gunicorn_worker_count():
    """Return ``GUNICORN_WORKERS`` (default 3)."""
    return _int_env("GUNICORN_WORKERS", DEFAULT_WORKERS, minimum=1)


def gunicorn_thread_count():
    """Return ``GUNICORN_THREADS`` per worker (default 4)."""
    return _int_env("GUNICORN_THREADS", DEFAULT_THREADS, minimum=1)


def web_statement_timeout_ms():
    """Return ``WEB_DB_STATEMENT_TIMEOUT_MS`` (default 10000, ``0`` = off)."""
    return _int_env(
        "WEB_DB_STATEMENT_TIMEOUT_MS",
        DEFAULT_WEB_STATEMENT_TIMEOUT_MS,
        minimum=0,
    )


def gunicorn_argv(workers, threads):
    """Return the gunicorn CLI argv for the web serving process."""
    return [
        "gunicorn",
        "website.wsgi:application",
        "--bind", "0.0.0.0:8000",
        "--worker-class", "gthread",
        "--workers", str(workers),
        "--threads", str(threads),
        "--timeout", str(WORKER_TIMEOUT_SECONDS),
        "--graceful-timeout", str(GRACEFUL_TIMEOUT_SECONDS),
        "--keep-alive", str(KEEP_ALIVE_SECONDS),
        "--preload",
    ]


def statement_timeout_option(timeout_ms):
    """Return the libpq ``options`` flag for ``timeout_ms`` milliseconds."""
    return f"-c statement_timeout={int(timeout_ms)}"


def apply_web_statement_timeout(timeout_ms, connection_handler=connections):
    """Add the timeout to every Postgres alias and drop open connections.

    The flag is passed as a libpq startup option, so it costs no extra round
    trip and survives ``CONN_MAX_AGE`` reuse. Closing the existing (boot-time)
    connections makes every forked worker open a fresh one that carries the
    limit. Returns the aliases that were changed.
    """
    if timeout_ms <= 0:
        return []
    applied = []
    for alias in connection_handler:
        connection = connection_handler[alias]
        if connection.vendor != "postgresql":
            continue
        options = connection.settings_dict.setdefault("OPTIONS", {})
        existing = options.get("options", "")
        flag = statement_timeout_option(timeout_ms)
        options["options"] = f"{existing} {flag}".strip()
        connection.close()
        applied.append(alias)
    return applied
