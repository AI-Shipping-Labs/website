"""Serializers for the worker task API (issue #714).

These convert ``django_q.models.Task`` rows into the JSON-ready dicts
documented in the issue. The wire shape is OWNED here -- the API does
NOT expose ``Task`` model rows directly because:

* ``args`` / ``kwargs`` / ``result`` are ``PickledObjectField`` columns
  that cannot be passed to ``JsonResponse`` as-is. We ``pprint.pformat``
  them into strings so callers get something legible (the Studio detail
  HTML view does the same).
* The collapsed-row "first useful line" summary that the Studio failed-
  tasks table renders is a derived field that does NOT exist on the
  model. ``extract_error_summary`` factors that heuristic out so both
  the Studio HTML view and the JSON API share one definition.
* Coupling our public JSON shape to a third-party model's column names
  is a footgun if django-q renames a field on minor upgrade.

The dependency direction is one-way: ``studio.views.worker`` imports
``extract_error_summary`` from this module; ``api.serializers.worker``
must NOT import anything from ``studio.*``.
"""

from __future__ import annotations

import pprint

# Same truncation budget the Studio collapsed-row summary uses (issue #218).
# Keep the constant here so the API and the Studio view stay in lock-step.
ERROR_SUMMARY_MAX_LENGTH = 160
NO_ERROR_DETAILS_PLACEHOLDER = "No error details"


def _isoformat_or_none(value):
    """ISO-8601 with timezone for non-null datetimes, ``None`` otherwise."""
    if value is None:
        return None
    return value.isoformat()


def _format_task_value(value):
    """Pretty-print a Task ``args`` / ``kwargs`` / ``result`` value.

    Mirrors ``studio.views.worker._format_task_value`` so the API and the
    Studio detail page render the same string for the same pickled value.

    * ``None`` becomes the empty string so callers can render "no value"
      without a None-check in the template / client.
    * Strings come through unchanged (multi-line tracebacks would be
      quoted to death by ``pprint``).
    * Everything else goes through ``pprint.pformat`` with the same
      width/sort settings the Studio view uses.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return pprint.pformat(value, width=100, sort_dicts=False)
    except Exception:  # pragma: no cover - defensive
        return repr(value)


def _looks_like_traceback(text):
    """Did this result string come from ``traceback.format_exc()``?

    Same heuristic as ``studio.views.worker._looks_like_traceback`` -- we
    accept either a result that starts with ``Traceback`` (django-q's
    typical shape) or one that embeds ``\nTraceback (most recent call
    last):`` somewhere in the middle (when the worker wrapped the
    traceback in its own preamble).
    """
    if not isinstance(text, str):
        return False
    return text.startswith("Traceback") or "\nTraceback (most recent call last):" in text


def extract_error_summary(error_message):
    """Return the most informative one-line summary of ``error_message``.

    Factored from ``studio/views/worker.py`` (issue #714) so the Studio
    HTML view and the JSON API share one definition. The heuristic:

    * If the result string looks like a ``traceback.format_exc()``
      payload, return the LAST non-blank line. The first line is the
      literal ``Traceback (most recent call last):`` banner, which is
      identical for every failure and tells operators nothing -- the
      exception class + message lives on the last line.
    * Otherwise return the FIRST non-blank line. Custom error wrappers
      and plain messages put the useful summary up front.
    * If there are no non-blank lines at all, return the canonical
      ``"No error details"`` placeholder.
    * If the chosen line is longer than ``ERROR_SUMMARY_MAX_LENGTH``,
      truncate to ``MAX - 3`` characters and append ``"..."`` so the
      total length stays within the budget.

    ``error_message`` may be any string-like value. ``None`` and other
    falsey values are normalised to the placeholder.
    """
    if not error_message:
        return NO_ERROR_DETAILS_PLACEHOLDER

    nonblank_lines = [line.strip() for line in error_message.splitlines() if line.strip()]
    if not nonblank_lines:
        return NO_ERROR_DETAILS_PLACEHOLDER

    if _looks_like_traceback(error_message):
        summary_line = nonblank_lines[-1]
    else:
        summary_line = nonblank_lines[0]

    if len(summary_line) > ERROR_SUMMARY_MAX_LENGTH:
        summary_line = summary_line[: ERROR_SUMMARY_MAX_LENGTH - 3] + "..."
    return summary_line


def _duration_seconds(task):
    """Return ``(stopped - started).total_seconds()`` or ``None``."""
    if task.started is None or task.stopped is None:
        return None
    return (task.stopped - task.started).total_seconds()


def serialize_task_row(task, *, affected_entity=None):
    """Compact row dict used by the list endpoints (failed + generic).

    Mirrors the columns the Studio failed-tasks table renders, plus the
    derived ``error_summary`` so list consumers don't have to fetch the
    detail endpoint just to render a one-line preview.
    """
    if task.success:
        error_summary = None
    else:
        result_text = _format_task_value(task.result)
        error_summary = extract_error_summary(result_text)
    return {
        "task_id": task.id,
        "name": task.name,
        "group": task.group,
        "function": task.func,
        "started_at": _isoformat_or_none(task.started),
        "stopped_at": _isoformat_or_none(task.stopped),
        "duration_seconds": _duration_seconds(task),
        "success": task.success,
        "error_summary": error_summary,
        "affected_entity": affected_entity,
    }


def serialize_task_detail(task, *, affected_entity=None):
    """Full detail dict for ``GET /api/worker/tasks/<task_id>``.

    Returns every column the Studio detail page renders (args/kwargs as
    pprint strings, duration in seconds, ``is_traceback`` flag) plus the
    one-line ``error`` summary used by the list endpoints so a single
    detail call gives clients both the summary and the full traceback.
    """
    duration = _duration_seconds(task)
    args_text = _format_task_value(task.args)
    kwargs_text = _format_task_value(task.kwargs)

    if task.success:
        result_text = _format_task_value(task.result) if task.result is not None else None
        error = None
        traceback_text = None
        is_traceback = False
    else:
        result_text = _format_task_value(task.result)
        is_traceback = _looks_like_traceback(result_text)
        error = extract_error_summary(result_text)
        traceback_text = result_text if is_traceback else None
        # When success=false we surface the failure through ``error`` /
        # ``traceback``; ``result`` stays null so clients don't have to
        # decide which field carries the failure payload.
        result_text = None

    return {
        "task_id": task.id,
        "name": task.name,
        "group": task.group,
        "function": task.func,
        "hook": task.hook,
        "args": args_text,
        "kwargs": kwargs_text,
        "started_at": _isoformat_or_none(task.started),
        "stopped_at": _isoformat_or_none(task.stopped),
        "duration_seconds": duration,
        "cluster": task.cluster,
        "attempt_count": task.attempt_count,
        "success": task.success,
        "result": result_text,
        "error": error,
        "traceback": traceback_text,
        "is_traceback": is_traceback,
        "affected_entity": affected_entity,
    }
