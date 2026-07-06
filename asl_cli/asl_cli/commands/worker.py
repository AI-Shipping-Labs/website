"""``asl worker`` -- background task observability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def worker():
    """Observe background tasks."""


@worker.command("tasks")
@format_option
def worker_tasks(fmt):
    """List recent background tasks."""
    data = get_client().get(f"{API}/worker/tasks")
    if fmt == "table":
        rows = data.get("tasks", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["task_id", "name", "success", "result"])
    else:
        emit(data, fmt)


@worker.command("failed-tasks")
@format_option
def worker_failed_tasks(fmt):
    """List failed background tasks."""
    emit(get_client().get(f"{API}/worker/tasks/failed"), fmt)


@worker.command("task")
@click.argument("task_id")
@format_option
def worker_task(task_id, fmt):
    """Get a single background task."""
    emit(get_client().get(f"{API}/worker/tasks/{task_id}"), fmt)


groups = [worker]
