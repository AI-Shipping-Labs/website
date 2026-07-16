#!/usr/bin/env python3
"""Fail closed unless one combined ECS task proves web + worker readiness.

This is deliberately an AWS-CLI consumer rather than an application endpoint:
the deploy job already has short-lived OIDC credentials for the required
read-only calls.  All AWS stdout/stderr stays captured.  Only allowlisted
readiness facts are emitted so task-definition secrets and unrelated
application log lines cannot leak into GitHub Actions output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MAX_TIMEOUT_SECONDS = 180
MIN_POLL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_POLL_SECONDS = 5

_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REPOSITORY_URI_RE = re.compile(
    r"^(?P<registry>[A-Za-z0-9.-]+)/(?P<repository>[A-Za-z0-9._/-]+)$"
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ECS_ARN_RE = re.compile(r"^arn:aws[a-zA-Z-]*:ecs:[A-Za-z0-9-]+:[0-9]+:[A-Za-z0-9_:/.-]+$")
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class VerificationError(RuntimeError):
    """A sanitized, operator-actionable readiness invariant failure."""

    def __init__(self, invariant: str):
        self.invariant = invariant
        super().__init__(invariant)


@dataclass(frozen=True)
class ContainerDefinition:
    name: str
    image: str
    log_group: str
    log_prefix: str


@dataclass(frozen=True)
class TaskDefinitionEvidence:
    arn: str
    web: ContainerDefinition
    worker: ContainerDefinition


@dataclass(frozen=True)
class RuntimeEvidence:
    task_arn: str
    task_id: str
    web_status: str
    worker_status: str


@dataclass(frozen=True)
class MarkerEvidence:
    publish_timestamp: int
    observe_timestamp: int
    qcluster_timestamp: int


class AwsCli:
    """Deadline-bounded AWS JSON calls that never relay raw command output."""

    def __init__(self, *, region: str, deadline: float):
        self.region = region
        self.deadline = deadline

    def call(self, service: str, operation: str, *args: str) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("deadline-exhausted")

        env = os.environ.copy()
        env.update(
            {
                "AWS_DEFAULT_REGION": self.region,
                "AWS_MAX_ATTEMPTS": "1",
                "AWS_PAGER": "",
                "AWS_CLI_AUTO_PROMPT": "off",
            }
        )
        command = [
            "aws",
            service,
            operation,
            "--region",
            self.region,
            *args,
            "--no-paginate",
            "--output",
            "json",
            "--no-cli-pager",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=env,
                timeout=max(0.1, remaining),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise VerificationError(f"aws-{service}-{operation}-failed") from None
        if result.returncode != 0:
            raise VerificationError(f"aws-{service}-{operation}-failed")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            raise VerificationError(f"aws-{service}-{operation}-malformed") from None
        if not isinstance(payload, dict):
            raise VerificationError(f"aws-{service}-{operation}-malformed")
        return payload


def _positive_int(value: str, *, name: str, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"{name} must be a positive integer") from None
    if parsed <= 0 or (maximum is not None and parsed > maximum):
        ceiling = f" no greater than {maximum}" if maximum is not None else ""
        raise argparse.ArgumentTypeError(
            f"{name} must be a positive integer{ceiling}"
        )
    return parsed


def _timeout_value(value: str) -> int:
    return _positive_int(value, name="timeout-seconds", maximum=MAX_TIMEOUT_SECONDS)


def _poll_value(value: str) -> int:
    parsed = _positive_int(value, name="poll-seconds")
    if parsed < MIN_POLL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"poll-seconds must be at least {MIN_POLL_SECONDS}"
        )
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify exact combined ECS web/worker readiness.",
    )
    parser.add_argument("--region", required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--repository-uri", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=_timeout_value,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--poll-seconds",
        type=_poll_value,
        default=DEFAULT_POLL_SECONDS,
    )
    args = parser.parse_args(argv)

    if not _TAG_RE.fullmatch(args.tag):
        parser.error("tag has an invalid format")
    repository = _REPOSITORY_URI_RE.fullmatch(args.repository_uri)
    if repository is None or ".." in repository.group("repository"):
        parser.error("repository-uri has an invalid format")
    for field in ("region", "cluster", "service"):
        value = getattr(args, field)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", value):
            parser.error(f"{field} has an invalid format")
    return args


def _require_ecs_arn(value: Any, invariant: str) -> str:
    if not isinstance(value, str) or not _ECS_ARN_RE.fullmatch(value):
        raise VerificationError(invariant)
    return value


def _resolve_primary(aws: AwsCli, *, cluster: str, service: str) -> str:
    payload = aws.call(
        "ecs",
        "describe-services",
        "--cluster",
        cluster,
        "--services",
        service,
    )
    services = payload.get("services")
    if not isinstance(services, list) or len(services) != 1:
        raise VerificationError("primary-deployment-missing")
    failures = payload.get("failures", [])
    if failures not in (None, []):
        raise VerificationError("primary-deployment-missing")
    deployments = services[0].get("deployments")
    if not isinstance(deployments, list):
        raise VerificationError("primary-deployment-malformed")
    primary = [item for item in deployments if item.get("status") == "PRIMARY"]
    if len(primary) != 1:
        raise VerificationError("primary-deployment-missing")
    deployment = primary[0]
    if deployment.get("rolloutState") != "COMPLETED":
        raise VerificationError("primary-rollout-not-completed")
    desired = deployment.get("desiredCount")
    running = deployment.get("runningCount")
    if (
        not isinstance(desired, int)
        or isinstance(desired, bool)
        or not isinstance(running, int)
        or isinstance(running, bool)
        or desired < 1
        or desired != running
    ):
        raise VerificationError("primary-counts-not-ready")
    return _require_ecs_arn(
        deployment.get("taskDefinition"),
        "primary-task-definition-malformed",
    )


def _environment_map(container: dict[str, Any]) -> dict[str, str]:
    environment = container.get("environment")
    if not isinstance(environment, list):
        raise VerificationError("task-definition-environment-malformed")
    values: dict[str, str] = {}
    for item in environment:
        if not isinstance(item, dict):
            raise VerificationError("task-definition-environment-malformed")
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            values[name] = value
    return values


def _container_definition(
    container: dict[str, Any], *, expected_image: str
) -> tuple[str, ContainerDefinition]:
    name = container.get("name")
    image = container.get("image")
    if (
        not isinstance(name, str)
        or not _CONTAINER_NAME_RE.fullmatch(name)
        or image != expected_image
    ):
        raise VerificationError("task-definition-image-mismatch")
    environment = _environment_map(container)
    role = environment.get("R1_SCHEMA_BARRIER_ROLE")
    expected_migrations = {"web": "true", "worker": "false"}.get(role)
    if expected_migrations is None:
        raise VerificationError("task-definition-role-mismatch")
    if environment.get("VERSION") != expected_image.rsplit(":", 1)[1]:
        raise VerificationError("task-definition-version-mismatch")
    if environment.get("RUN_MIGRATIONS") != expected_migrations:
        raise VerificationError("task-definition-migration-role-mismatch")

    log_configuration = container.get("logConfiguration")
    if (
        not isinstance(log_configuration, dict)
        or log_configuration.get("logDriver") != "awslogs"
    ):
        raise VerificationError("task-definition-log-config-missing")
    options = log_configuration.get("options")
    if not isinstance(options, dict):
        raise VerificationError("task-definition-log-config-missing")
    group = options.get("awslogs-group")
    prefix = options.get("awslogs-stream-prefix")
    if not isinstance(group, str) or not group or not isinstance(prefix, str) or not prefix:
        raise VerificationError("task-definition-log-config-missing")
    if any(character in group + prefix for character in "\r\n\x00"):
        raise VerificationError("task-definition-log-config-malformed")
    return role, ContainerDefinition(name, image, group, prefix)


def _resolve_task_definition(
    aws: AwsCli,
    *,
    task_definition_arn: str,
    expected_image: str,
) -> TaskDefinitionEvidence:
    payload = aws.call(
        "ecs",
        "describe-task-definition",
        "--task-definition",
        task_definition_arn,
    )
    task_definition = payload.get("taskDefinition")
    if not isinstance(task_definition, dict):
        raise VerificationError("task-definition-malformed")
    described_arn = _require_ecs_arn(
        task_definition.get("taskDefinitionArn"),
        "task-definition-arn-malformed",
    )
    if described_arn != task_definition_arn:
        raise VerificationError("task-definition-arn-mismatch")
    containers = task_definition.get("containerDefinitions")
    if not isinstance(containers, list) or len(containers) != 2:
        raise VerificationError("combined-container-set-mismatch")

    by_role: dict[str, ContainerDefinition] = {}
    for raw_container in containers:
        if not isinstance(raw_container, dict):
            raise VerificationError("task-definition-container-malformed")
        role, evidence = _container_definition(
            raw_container,
            expected_image=expected_image,
        )
        if role in by_role:
            raise VerificationError("task-definition-role-mismatch")
        by_role[role] = evidence
    if set(by_role) != {"web", "worker"}:
        raise VerificationError("task-definition-role-mismatch")
    return TaskDefinitionEvidence(
        arn=described_arn,
        web=by_role["web"],
        worker=by_role["worker"],
    )


def _resolve_digest(
    aws: AwsCli,
    *,
    repository_uri: str,
    tag: str,
) -> str:
    repository_match = _REPOSITORY_URI_RE.fullmatch(repository_uri)
    if repository_match is None:
        raise VerificationError("repository-uri-malformed")
    payload = aws.call(
        "ecr",
        "batch-get-image",
        "--repository-name",
        repository_match.group("repository"),
        "--image-ids",
        f"imageTag={tag}",
    )
    if payload.get("failures") not in (None, []):
        raise VerificationError("ecr-tag-or-digest-missing")
    images = payload.get("images")
    if not isinstance(images, list) or len(images) != 1:
        raise VerificationError("ecr-tag-or-digest-missing")
    image_id = images[0].get("imageId")
    digest = image_id.get("imageDigest") if isinstance(image_id, dict) else None
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise VerificationError("ecr-tag-or-digest-missing")
    return digest


def _list_running_task_arns(
    aws: AwsCli, *, cluster: str, service: str
) -> list[str]:
    arns: list[str] = []
    next_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        arguments = [
            "--cluster",
            cluster,
            "--service-name",
            service,
            "--desired-status",
            "RUNNING",
        ]
        if next_token is not None:
            arguments.extend(["--next-token", next_token])
        payload = aws.call("ecs", "list-tasks", *arguments)
        page = payload.get("taskArns")
        if not isinstance(page, list):
            raise VerificationError("running-task-list-malformed")
        for task_arn in page:
            arns.append(_require_ecs_arn(task_arn, "running-task-arn-malformed"))
        token = payload.get("nextToken")
        if token is None:
            return arns
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise VerificationError("running-task-pagination-malformed")
        seen_tokens.add(token)
        next_token = token


def _runtime_from_task(
    task: dict[str, Any],
    *,
    task_definition: TaskDefinitionEvidence,
    expected_digest: str,
) -> RuntimeEvidence:
    task_arn = _require_ecs_arn(task.get("taskArn"), "runtime-task-arn-malformed")
    if task.get("taskDefinitionArn") != task_definition.arn:
        raise VerificationError("runtime-task-definition-mismatch")
    if task.get("lastStatus") != "RUNNING":
        raise VerificationError("runtime-task-not-running")
    containers = task.get("containers")
    if not isinstance(containers, list):
        raise VerificationError("runtime-containers-malformed")
    by_name = {
        item.get("name"): item
        for item in containers
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    expected = (task_definition.web, task_definition.worker)
    statuses: dict[str, str] = {}
    for definition in expected:
        runtime = by_name.get(definition.name)
        if not isinstance(runtime, dict):
            raise VerificationError("runtime-container-missing")
        if runtime.get("lastStatus") != "RUNNING":
            raise VerificationError("runtime-container-not-running")
        if runtime.get("image") != definition.image:
            raise VerificationError("runtime-image-tag-mismatch")
        if runtime.get("imageDigest") != expected_digest:
            raise VerificationError("runtime-image-digest-mismatch")
        statuses[definition.name] = "RUNNING"
    task_id = task_arn.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9-]+", task_id):
        raise VerificationError("runtime-task-id-malformed")
    return RuntimeEvidence(
        task_arn=task_arn,
        task_id=task_id,
        web_status=statuses[task_definition.web.name],
        worker_status=statuses[task_definition.worker.name],
    )


def _describe_tasks(
    aws: AwsCli, *, cluster: str, task_arns: list[str]
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for offset in range(0, len(task_arns), 100):
        payload = aws.call(
            "ecs",
            "describe-tasks",
            "--cluster",
            cluster,
            "--tasks",
            *task_arns[offset : offset + 100],
        )
        if payload.get("failures") not in (None, []):
            raise VerificationError("runtime-task-describe-failed")
        page = payload.get("tasks")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise VerificationError("runtime-task-describe-malformed")
        tasks.extend(page)
    return tasks


def _select_runtime(
    aws: AwsCli,
    *,
    cluster: str,
    service: str,
    task_definition: TaskDefinitionEvidence,
    expected_digest: str,
) -> RuntimeEvidence:
    task_arns = _list_running_task_arns(aws, cluster=cluster, service=service)
    if not task_arns:
        raise VerificationError("running-task-missing")
    tasks = _describe_tasks(aws, cluster=cluster, task_arns=task_arns)
    candidates = [
        task for task in tasks if task.get("taskDefinitionArn") == task_definition.arn
    ]
    if not candidates:
        raise VerificationError("running-primary-task-missing")
    candidates.sort(key=lambda item: str(item.get("startedAt", "")), reverse=True)
    failures: list[VerificationError] = []
    for task in candidates:
        try:
            return _runtime_from_task(
                task,
                task_definition=task_definition,
                expected_digest=expected_digest,
            )
        except VerificationError as error:
            failures.append(error)
    raise failures[0] if failures else VerificationError("running-primary-task-missing")


def _recheck_runtime(
    aws: AwsCli,
    *,
    cluster: str,
    runtime: RuntimeEvidence,
    task_definition: TaskDefinitionEvidence,
    expected_digest: str,
) -> RuntimeEvidence:
    tasks = _describe_tasks(aws, cluster=cluster, task_arns=[runtime.task_arn])
    if len(tasks) != 1:
        raise VerificationError("final-runtime-task-missing")
    final = _runtime_from_task(
        tasks[0],
        task_definition=task_definition,
        expected_digest=expected_digest,
    )
    if final.task_arn != runtime.task_arn:
        raise VerificationError("final-runtime-task-replaced")
    return final


def _get_stream_events(
    aws: AwsCli, *, log_group: str, stream_name: str
) -> list[tuple[int, str]]:
    events: list[tuple[int, str]] = []
    next_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        arguments = [
            "--log-group-name",
            log_group,
            "--log-stream-name",
            stream_name,
            "--start-from-head",
        ]
        if next_token is not None:
            arguments.extend(["--next-token", next_token])
        payload = aws.call("logs", "get-log-events", *arguments)
        page = payload.get("events")
        if not isinstance(page, list):
            raise VerificationError("cloudwatch-events-malformed")
        for event in page:
            if not isinstance(event, dict):
                raise VerificationError("cloudwatch-events-malformed")
            timestamp = event.get("timestamp")
            message = event.get("message")
            if (
                not isinstance(timestamp, int)
                or isinstance(timestamp, bool)
                or not isinstance(message, str)
            ):
                raise VerificationError("cloudwatch-events-malformed")
            events.append((timestamp, message.strip()))
        token = payload.get("nextForwardToken")
        if not isinstance(token, str) or not token:
            raise VerificationError("cloudwatch-pagination-malformed")
        if token == next_token:
            return events
        if token in seen_tokens:
            raise VerificationError("cloudwatch-pagination-malformed")
        seen_tokens.add(token)
        next_token = token


def _marker_evidence(
    *, web_events: list[tuple[int, str]], worker_events: list[tuple[int, str]], tag: str
) -> tuple[MarkerEvidence | None, list[str]]:
    key = f"r1_serving_schema_ready:{tag}"
    publish_message = f"Published serving schema readiness marker {key}"
    observe_message = f"Serving schema readiness marker observed: {key}"
    qcluster_message = "Starting django-q cluster"

    publish = [event for event in web_events if event[1] == publish_message]
    observe_indexes = [
        index for index, event in enumerate(worker_events) if event[1] == observe_message
    ]
    qcluster_indexes = [
        index for index, event in enumerate(worker_events) if event[1] == qcluster_message
    ]
    missing: list[str] = []
    if not publish:
        missing.append("web-publish")
    if not observe_indexes:
        missing.append("worker-observe")
    if not qcluster_indexes:
        missing.append("worker-qcluster-start")
    if missing:
        return None, missing

    publish_event = publish[0]
    observe_index = observe_indexes[0]
    qcluster_index = qcluster_indexes[0]
    observe_event = worker_events[observe_index]
    qcluster_event = worker_events[qcluster_index]
    if publish_event[0] > observe_event[0]:
        raise VerificationError("marker-order-publish-after-observe")
    if qcluster_index <= observe_index:
        raise VerificationError("marker-order-qcluster-before-observe")
    return (
        MarkerEvidence(
            publish_timestamp=publish_event[0],
            observe_timestamp=observe_event[0],
            qcluster_timestamp=qcluster_event[0],
        ),
        [],
    )


def _wait_for_markers(
    aws: AwsCli,
    *,
    task_definition: TaskDefinitionEvidence,
    runtime: RuntimeEvidence,
    tag: str,
    poll_seconds: int,
) -> MarkerEvidence:
    web_stream = (
        f"{task_definition.web.log_prefix}/"
        f"{task_definition.web.name}/{runtime.task_id}"
    )
    worker_stream = (
        f"{task_definition.worker.log_prefix}/"
        f"{task_definition.worker.name}/{runtime.task_id}"
    )
    last_missing = ["web-publish", "worker-observe", "worker-qcluster-start"]
    attempted = False
    while True:
        if attempted and aws.deadline - time.monotonic() <= 0:
            break
        attempted = True
        web_events = _get_stream_events(
            aws,
            log_group=task_definition.web.log_group,
            stream_name=web_stream,
        )
        worker_events = _get_stream_events(
            aws,
            log_group=task_definition.worker.log_group,
            stream_name=worker_stream,
        )
        evidence, last_missing = _marker_evidence(
            web_events=web_events,
            worker_events=worker_events,
            tag=tag,
        )
        if evidence is not None:
            return evidence
        remaining = aws.deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(float(poll_seconds), remaining))
    raise VerificationError("markers-missing-" + "-".join(last_missing))


def verify(args: argparse.Namespace) -> tuple[
    TaskDefinitionEvidence,
    RuntimeEvidence,
    str,
    MarkerEvidence,
]:
    deadline = time.monotonic() + args.timeout_seconds
    aws = AwsCli(region=args.region, deadline=deadline)
    expected_image = f"{args.repository_uri}:{args.tag}"
    task_definition_arn = _resolve_primary(
        aws,
        cluster=args.cluster,
        service=args.service,
    )
    task_definition = _resolve_task_definition(
        aws,
        task_definition_arn=task_definition_arn,
        expected_image=expected_image,
    )
    digest = _resolve_digest(
        aws,
        repository_uri=args.repository_uri,
        tag=args.tag,
    )
    runtime = _select_runtime(
        aws,
        cluster=args.cluster,
        service=args.service,
        task_definition=task_definition,
        expected_digest=digest,
    )
    markers = _wait_for_markers(
        aws,
        task_definition=task_definition,
        runtime=runtime,
        tag=args.tag,
        poll_seconds=args.poll_seconds,
    )
    final_runtime = _recheck_runtime(
        aws,
        cluster=args.cluster,
        runtime=runtime,
        task_definition=task_definition,
        expected_digest=digest,
    )
    return task_definition, final_runtime, digest, markers


def _iso8601_milliseconds(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp / 1000, tz=UTC).isoformat(
        timespec="milliseconds"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        task_definition, runtime, digest, markers = verify(args)
    except VerificationError as error:
        print(
            "COMBINED_READINESS failed "
            f"invariant={error.invariant} "
            "action=inspect-sanitized-deploy-readiness-evidence",
            file=sys.stderr,
        )
        return 1

    print(
        "COMBINED_READINESS verified "
        f"tag={args.tag} "
        f"digest={digest} "
        f"task_definition={task_definition.arn} "
        f"task={runtime.task_arn} "
        f"containers={task_definition.web.name}:{runtime.web_status},"
        f"{task_definition.worker.name}:{runtime.worker_status} "
        "markers=publish:"
        f"{_iso8601_milliseconds(markers.publish_timestamp)},"
        f"observe:{_iso8601_milliseconds(markers.observe_timestamp)},"
        f"qcluster:{_iso8601_milliseconds(markers.qcluster_timestamp)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
