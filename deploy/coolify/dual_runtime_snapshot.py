"""Collect only approved Docker metadata; run over SSH, never dump inspect/env."""

import json
import subprocess
import sys
from datetime import UTC, datetime

WORKER_PROBE = (
    'import os,socket; os.environ.setdefault("DJANGO_SETTINGS_MODULE","website.settings"); '
    'import django; django.setup(); from django_q.status import Stat; '
    'from django.utils import timezone; assert any(s.host == socket.gethostname() '
    'and s.status in ("Idle", "Working") and '
    '(timezone.now()-s.timestamp).total_seconds() < 10 for s in Stat.get_all())'
)


def docker(*arguments):
    return subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True, timeout=30,
    ).stdout


def source_ids(uuid):
    ids = set()
    for selector in (f"name={uuid}", f"label=com.docker.compose.project={uuid}"):
        ids.update(docker("ps", "-aq", "--filter", selector).split())
    return sorted(ids)


def sanitized_container(container):
    config = container["Config"]
    state = container["State"]
    health = state.get("Health", {})
    logs = health.get("Log", [])
    last = {}
    if logs:
        last = {"end": logs[-1].get("End"), "exit_code": logs[-1].get("ExitCode")}
    environment = {}
    for entry in config.get("Env", []):
        key, _, value = entry.partition("=")
        if key in ("BOOT_MODE", "RUN_MIGRATIONS", "VERSION"):
            environment[key] = value
    return {
        "id": container["Id"], "name": container["Name"].lstrip("/"),
        "image": config["Image"], "image_id": container["Image"],
        "running": state.get("Running"), "status": state.get("Status"),
        "started_at": state.get("StartedAt"), "health": health.get("Status"),
        "last_healthcheck": last, "environment": environment,
        "networks": sorted(container["NetworkSettings"].get("Networks", {})),
        "worker_heartbeat_probe": config.get("Healthcheck", {}).get("Test") == [
            "CMD", "/app/.venv/bin/python", "-c", WORKER_PROBE,
        ],
    }


def collect(uuid):
    containers = []
    ids = source_ids(uuid)
    if ids:
        for container in json.loads(docker("inspect", *ids)):
            containers.append(sanitized_container(container))
    return {"generated_at": datetime.now(UTC).isoformat(), "containers": containers}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(sys.argv[1])))
    except (KeyError, ValueError, IndexError, OSError, subprocess.SubprocessError):
        # This external boundary must never print Docker stderr, env, or inspect.
        print('{"error":"Docker snapshot collection failed"}')
        sys.exit(1)
