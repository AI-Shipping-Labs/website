"""``asl worker`` -- background task observability."""

from __future__ import annotations

import click

from asl_cli.commands._shared import emit, format_option, get_client

API = "/api"

commands = []


@click.command("worker-tasks-list")
@format_option
def worker_tasks_list(fmt):
    """List recent background tasks."""
    data = get_client().get(f"{API}/worker/tasks")
    if fmt == "table":
        rows = data.get("tasks", []) if isinstance(data, dict) else data
        emit(rows, fmt, columns=["task_id", "name", "success", "result"])
    else:
        emit(data, fmt)


commands.append(worker_tasks_list)


@click.command("worker-tasks-failed")
@format_option
def worker_tasks_failed(fmt):
    """List failed background tasks."""
    data = get_client().get(f"{API}/worker/tasks/failed")
    emit(data, fmt)


commands.append(worker_tasks_failed)


@click.command("worker-task-get")
@click.argument("task_id")
@format_option
def worker_task_get(task_id, fmt):
    """Get a single background task."""
    data = get_client().get(f"{API}/worker/tasks/{task_id}")
    emit(data, fmt)


commands.append(worker_task_get)
