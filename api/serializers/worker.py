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
  model. ``jobs.task_format`` owns that heuristic so both the Studio HTML
  view and the JSON API share one definition.
* Coupling our public JSON shape to a third-party model's column names
  is a footgun if django-q renames a field on minor upgrade.

Task formatting belongs to the neutral ``jobs`` domain. This module owns only
the worker API's JSON wire shape.
"""

from __future__ import annotations

import jobs.task_format as task_format


def _isoformat_or_none(value):
    """ISO-8601 with timezone for non-null datetimes, ``None`` otherwise."""
    if value is None:
        return None
    return value.isoformat()


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
        result_text = task_format.format_task_value(task.result)
        error_summary = task_format.extract_error_summary(result_text)
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
    args_text = task_format.format_task_value(task.args)
    kwargs_text = task_format.format_task_value(task.kwargs)

    if task.success:
        result_text = task_format.format_task_value(task.result) if task.result is not None else None
        error = None
        traceback_text = None
        is_traceback = False
    else:
        result_text = task_format.format_task_value(task.result)
        is_traceback = task_format.looks_like_traceback(result_text)
        error = task_format.extract_error_summary(result_text)
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
