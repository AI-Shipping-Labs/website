#!/usr/bin/env python3
"""Read-only source Docker inventory and public ping verification for operators."""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SOURCE_UUID = "mekt9kz74fvbixla6olinuma"
IMAGE_REPOSITORY = "ghcr.io/ai-shipping-labs/website"
PUBLIC_PING = "https://aishippinglabs.com/ping"


class VerificationError(ValueError):
    """A sanitized failure suitable for operator output."""


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise VerificationError("Missing or malformed health timestamp") from error
    if parsed.tzinfo is None:
        raise VerificationError("Health timestamp must include a timezone")
    return parsed


def immutable_image(value):
    prefix = IMAGE_REPOSITORY + ":"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise VerificationError("Source image is outside the approved repository")
    tag = value[len(prefix):]
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", tag):
        raise VerificationError("Invalid image tag")
    pattern = r"(?:\d{8}-\d{6}-[a-f0-9]{7,40}|deploy-\d+-\d+-[a-f0-9]{40})"
    if not re.fullmatch(pattern, tag):
        raise VerificationError("Source image must use an immutable release tag")
    return value


def verify_health(container, observed_at, max_age):
    if container.get("running") is not True or container.get("status") != "running":
        raise VerificationError("Source container is not running")
    if container.get("health") != "healthy":
        raise VerificationError("Source container is not healthy")
    last = container.get("last_healthcheck", {})
    if last.get("exit_code") != 0:
        raise VerificationError("Source container lacks a successful healthcheck")
    ended = timestamp(last.get("end"))
    age = (observed_at - ended).total_seconds()
    if age < -5 or age > max_age or ended < timestamp(container.get("started_at")):
        raise VerificationError("Source healthcheck is stale or outside this boot")


def verify_role(container, role):
    environment = container.get("environment", {})
    if role == "worker":
        if environment.get("BOOT_MODE") != "worker" or environment.get("RUN_MIGRATIONS") != "false":
            raise VerificationError("Worker boot or migration mode changed")
        if container.get("worker_heartbeat_probe") is not True:
            raise VerificationError("Worker lacks the approved own-host heartbeat probe")
    elif environment.get("BOOT_MODE") or environment.get("RUN_MIGRATIONS") != "true":
        raise VerificationError("Source web must retain legacy Gunicorn migration ownership")
    if not {SOURCE_UUID, "coolify"}.issubset(container.get("networks", [])):
        raise VerificationError("Source container lacks a required creation-time network")


def verify_snapshot(snapshot, expected_image=None, max_age=90):
    observed = timestamp(snapshot.get("generated_at"))
    inventory = snapshot.get("containers", [])
    expected_names = {f"web-{SOURCE_UUID}", f"worker-{SOURCE_UUID}"}
    if len(inventory) != 2 or {c.get("name") for c in inventory} != expected_names:
        raise VerificationError("Source inventory must contain exactly web and worker; orphan or missing container")
    images = set()
    image_ids = set()
    summary = {}
    for container in inventory:
        role = container["name"].split("-", 1)[0]
        verify_health(container, observed, max_age)
        verify_role(container, role)
        images.add(immutable_image(container.get("image")))
        image_ids.add(container.get("image_id"))
        summary[role] = {"id": container["id"][:12], "started_at": container["started_at"]}
    if len(images) != 1 or len(image_ids) != 1 or not next(iter(image_ids)):
        raise VerificationError("Source web and worker do not share the exact image")
    image = images.pop()
    if expected_image and image != expected_image:
        raise VerificationError("Source image differs from the expected release")
    return {"observed_at": observed.isoformat(), "image": image, "image_id": image_ids.pop(), "roles": summary}


def remote_snapshot(host, ssh_config, identity):
    command = ["ssh", "-F", str(ssh_config), "-i", str(identity), "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=10", "--", host, f"sudo python3 - {SOURCE_UUID}"]
    collector = Path(__file__).with_name("dual_runtime_snapshot.py").read_text()
    result = subprocess.run(command, input=collector, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise VerificationError("Read-only remote Docker snapshot failed")
    return json.loads(result.stdout)


def public_ping(expected_sha=None):
    request = urllib.request.Request(PUBLIC_PING, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200 or response.url != PUBLIC_PING:
            raise VerificationError("Public ping did not return direct HTTP 200")
        body = response.read(256).decode().strip()
    if expected_sha and body != expected_sha:
        raise VerificationError("Public ping differs from the expected full release SHA")
    # A public response is untrusted; emit only the status and verified equality.
    return {"status": 200, "exact_sha_verified": bool(expected_sha)}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="contabo-b")
    parser.add_argument("--ssh-config", type=Path, default=Path.home() / ".ssh/config")
    parser.add_argument("--identity", type=Path, default=Path.home() / ".ssh/id_contabo")
    parser.add_argument("--expected-image")
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    if args.expected_image:
        immutable_image(args.expected_image)
    if args.expected_sha and not re.fullmatch("[a-f0-9]{40}", args.expected_sha):
        raise VerificationError("Expected SHA must be 40 lowercase hex characters")
    if args.expected_image and args.expected_sha:
        tag_sha = args.expected_image.rsplit("-", 1)[1]
        if not args.expected_sha.startswith(tag_sha):
            raise VerificationError("Expected image and public SHA describe different releases")
    return args


def main():
    try:
        args = arguments()
        before = verify_snapshot(remote_snapshot(args.host, args.ssh_config, args.identity), args.expected_image)
        ping = public_ping(args.expected_sha)
        after = verify_snapshot(remote_snapshot(args.host, args.ssh_config, args.identity), args.expected_image)
        if before["roles"] != after["roles"] or before["image_id"] != after["image_id"]:
            raise VerificationError("Source containers changed during the verification window; retry")
        release_verified = bool(args.expected_image and args.expected_sha)
        print(json.dumps({"status": "healthy-snapshot", "release_verified": release_verified,
                          **after, "public_ping": ping}))
        return 0
    except VerificationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError, urllib.error.URLError):
        # Remote/HTTP payloads and exception messages can contain secrets.
        print('{"status":"failed","error":"Read-only verification failed"}')
    return 1


if __name__ == "__main__":
    sys.exit(main())
