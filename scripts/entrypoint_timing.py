"""Boot timing helpers for the container entrypoint."""

import logging
import os
import time

logger = logging.getLogger("scripts.entrypoint_init")


def _emit_timing(phase, seconds):
    """Print a single flushed ``BOOT_TIMING`` line for CloudWatch."""
    print(f"BOOT_TIMING phase={phase} seconds={seconds:.3f}", flush=True)


def _timed(phase, fn, record=None):
    """Run ``fn()``, emit elapsed timing, and return the result.

    The timing line is emitted from a ``finally`` block so a phase that raises
    still records its elapsed time before the exception propagates unchanged.
    When ``record`` is a dict, it stores the same value that was printed.
    """
    start = time.perf_counter()
    try:
        return fn()
    finally:
        elapsed = time.perf_counter() - start
        _emit_timing(phase, elapsed)
        if record is not None:
            record[phase] = elapsed


def persist_boot_timing(role, phases):
    """Persist boot timings to the shared cache without blocking startup."""
    try:
        from django.core.cache import caches
        from django.utils import timezone

        payload = {
            "tag": os.environ.get("VERSION") or "unknown",
            "recorded_at": timezone.now().isoformat(),
            "role": role,
            "phases": dict(phases),
        }
        caches["django_q"].set(
            f"boot_timing:{role}", payload, timeout=None,
        )
    except Exception:
        # A store/cache failure is non-critical; keep booting and log context.
        logger.exception("persist_boot_timing failed during entrypoint boot")
