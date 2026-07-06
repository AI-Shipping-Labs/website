"""``asl worker`` -- background task observability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"


@click.group()
def worker():
    """Observe background tasks."""


@worker.group("tasks")
def worker_tasks():
    """List background tasks."""


@worker_tasks.command("list")
@format_option
def worker_tasks_list(fmt):
    """List recent background tasks."""
    data = get_client().get(f"{API}/worker/tasks")
    if fmt == "table":
        rows = data.get("tasks", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["task_id", "name", "success", "result"])
    else:
        emit(data, fmt)


@worker_tasks.command("failed")
@format_option
def worker_tasks_failed(fmt):
    """List failed background tasks."""
    emit(get_client().get(f"{API}/worker/tasks/failed"), fmt)


@worker_tasks.command("get")
@click.argument("task_id")
@format_option
def worker_task_get(task_id, fmt):
    """Get a single background task."""
    emit(get_client().get(f"{API}/worker/tasks/{task_id}"), fmt)


groups = [worker]
