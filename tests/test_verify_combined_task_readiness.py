"""Executable mocked-AWS coverage for the combined dev readiness gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from django.test import SimpleTestCase, tag

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "deploy" / "verify_combined_task_readiness.py"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "deploy-dev.yml"
SCHEDULED_WAKE_PATH = (
    PROJECT_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml"
)
SCRATCH_ROOT = PROJECT_ROOT / ".tmp" / "test-combined-readiness"

TAG = "20260716-141054-b53762b"
REPOSITORY_URI = (
    "387546586013.dkr.ecr.eu-west-1.amazonaws.com/ai-shipping-labs"
)
DIGEST = "sha256:" + "a" * 64
TASK_DEFINITION_ARN = (
    "arn:aws:ecs:eu-west-1:387546586013:task-definition/"
    "ai-shipping-labs-dev:727"
)
OLD_TASK_DEFINITION_ARN = (
    "arn:aws:ecs:eu-west-1:387546586013:task-definition/"
    "ai-shipping-labs-dev:726"
)
TASK_ARN = (
    "arn:aws:ecs:eu-west-1:387546586013:task/"
    "ai-shipping-labs/task-abc"
)
SENTINEL_SECRET = "NEVER_PRINT_TASK_SECRET_1293"
SENTINEL_LOG = "NEVER_PRINT_UNRELATED_LOG_1293"


FAKE_AWS = r'''#!__PYTHON__
import json
import os
import sys
from pathlib import Path

TAG = os.environ["FAKE_TAG"]
REPOSITORY_URI = os.environ["FAKE_REPOSITORY_URI"]
DIGEST = os.environ["FAKE_DIGEST"]
TASK_DEFINITION_ARN = os.environ["FAKE_TASK_DEFINITION_ARN"]
OLD_TASK_DEFINITION_ARN = os.environ["FAKE_OLD_TASK_DEFINITION_ARN"]
TASK_ARN = os.environ["FAKE_TASK_ARN"]
SENTINEL_SECRET = os.environ["FAKE_SENTINEL_SECRET"]
SENTINEL_LOG = os.environ["FAKE_SENTINEL_LOG"]
SCENARIO = os.environ.get("FAKE_SCENARIO", "success")

args = sys.argv[1:]
service = args[0] if args else ""
operation = args[1] if len(args) > 1 else ""
with open(os.environ["FAKE_AWS_CALLS"], "a") as log:
    log.write(" ".join(args) + "\n")

if SCENARIO == "aws_error" and service == "ecs" and operation == "describe-services":
    print("permission denied " + SENTINEL_SECRET, file=sys.stderr)
    raise SystemExit(254)
if SCENARIO == "malformed" and service == "ecs" and operation == "describe-services":
    print("not-json-" + SENTINEL_SECRET)
    raise SystemExit(0)

def output(payload):
    print(json.dumps(payload))
    raise SystemExit(0)

def value_after(flag, default=None):
    if flag not in args:
        return default
    return args[args.index(flag) + 1]

if service == "ecs" and operation == "describe-services":
    task_definition = (
        OLD_TASK_DEFINITION_ARN if SCENARIO == "old_primary"
        else TASK_DEFINITION_ARN
    )
    rollout = "IN_PROGRESS" if SCENARIO == "rollout_in_progress" else "COMPLETED"
    running = 0 if SCENARIO == "counts_not_ready" else 1
    output({
        "services": [{
            "deployments": [{
                "status": "PRIMARY",
                "rolloutState": rollout,
                "desiredCount": 1,
                "runningCount": running,
                "taskDefinition": task_definition,
            }],
        }],
        "failures": [],
    })

if service == "ecs" and operation == "describe-task-definition":
    expected_image = REPOSITORY_URI + ":" + TAG
    worker_image = (
        REPOSITORY_URI + ":stale" if SCENARIO == "task_definition_image_mismatch"
        else expected_image
    )
    worker_role = (
        "invalid" if SCENARIO == "task_definition_role_mismatch" else "worker"
    )
    output({
        "taskDefinition": {
            "taskDefinitionArn": TASK_DEFINITION_ARN,
            "containerDefinitions": [
                {
                    "name": "ai-shipping-labs",
                    "image": expected_image,
                    "environment": [
                        {"name": "VERSION", "value": TAG},
                        {"name": "RUN_MIGRATIONS", "value": "true"},
                        {"name": "R1_SCHEMA_BARRIER_ROLE", "value": "web"},
                        {"name": "DATABASE_URL", "value": SENTINEL_SECRET},
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": "/ecs/ai-shipping-labs",
                            "awslogs-stream-prefix": "ecs",
                        },
                    },
                },
                {
                    "name": "ai-shipping-labs-worker",
                    "image": worker_image,
                    "environment": [
                        {"name": "VERSION", "value": TAG},
                        {"name": "RUN_MIGRATIONS", "value": "false"},
                        {"name": "R1_SCHEMA_BARRIER_ROLE", "value": worker_role},
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": "/ecs/ai-shipping-labs",
                            "awslogs-stream-prefix": "ecs",
                        },
                    },
                },
            ],
        },
    })

if service == "ecr" and operation == "batch-get-image":
    if SCENARIO == "ecr_missing":
        output({"images": [], "failures": [{"failureCode": SENTINEL_SECRET}]})
    output({
        "images": [{"imageId": {"imageTag": TAG, "imageDigest": DIGEST}}],
        "failures": [],
    })

if service == "ecs" and operation == "list-tasks":
    if SCENARIO == "task_list_pagination_cycle":
        token = value_after("--next-token")
        output({"taskArns": [], "nextToken": "cycle" if token is None else "cycle"})
    output({"taskArns": [TASK_ARN]})

if service == "ecs" and operation == "describe-tasks":
    count_path = Path(os.environ["FAKE_DESCRIBE_TASKS_COUNT"])
    count = int(count_path.read_text()) + 1 if count_path.exists() else 1
    count_path.write_text(str(count))
    worker_status = "RUNNING"
    worker_digest = DIGEST
    worker_image = REPOSITORY_URI + ":" + TAG
    task_definition = TASK_DEFINITION_ARN
    if SCENARIO == "worker_stopped":
        worker_status = "STOPPED"
    if SCENARIO == "final_worker_stopped" and count >= 2:
        worker_status = "STOPPED"
    if SCENARIO == "runtime_digest_mismatch":
        worker_digest = "sha256:" + "b" * 64
    if SCENARIO == "runtime_tag_mismatch":
        worker_image = REPOSITORY_URI + ":stale"
    if SCENARIO == "runtime_old_revision":
        task_definition = OLD_TASK_DEFINITION_ARN
    output({
        "tasks": [{
            "taskArn": TASK_ARN,
            "taskDefinitionArn": task_definition,
            "lastStatus": "RUNNING",
            "startedAt": "2026-07-16T14:14:00Z",
            "containers": [
                {
                    "name": "ai-shipping-labs",
                    "lastStatus": "RUNNING",
                    "image": REPOSITORY_URI + ":" + TAG,
                    "imageDigest": DIGEST,
                },
                {
                    "name": "ai-shipping-labs-worker",
                    "lastStatus": worker_status,
                    "image": worker_image,
                    "imageDigest": worker_digest,
                },
            ],
        }],
        "failures": [],
    })

if service == "logs" and operation == "get-log-events":
    stream = value_after("--log-stream-name", "")
    token = value_after("--next-token")
    if SCENARIO == "cloudwatch_pagination_cycle":
        if token is None:
            output({"events": [], "nextForwardToken": "token-a"})
        if token == "token-a":
            output({"events": [], "nextForwardToken": "token-b"})
        output({"events": [], "nextForwardToken": "token-a"})

    stream_token = "worker-done" if "worker" in stream else "web-done"
    if token is not None:
        output({"events": [], "nextForwardToken": stream_token})

    log_count_path = Path(os.environ["FAKE_LOG_FIRST_PAGE_COUNT"])
    log_count = int(log_count_path.read_text()) + 1 if log_count_path.exists() else 1
    log_count_path.write_text(str(log_count))
    events = [{"timestamp": 900, "message": SENTINEL_LOG}]
    key = "r1_serving_schema_ready:" + TAG
    if "ai-shipping-labs-worker" in stream:
        delayed_missing = SCENARIO == "delayed_logs" and log_count <= 2
        if SCENARIO not in {"missing_observe", "selected_task_incomplete"} and not delayed_missing:
            events.append({
                "timestamp": 1001,
                "message": "Serving schema readiness marker observed: " + key,
            })
        if SCENARIO != "missing_qcluster" and not delayed_missing:
            qcluster = {"timestamp": 1002, "message": "Starting django-q cluster"}
            if SCENARIO == "qcluster_before_observe":
                events.insert(1, qcluster)
            else:
                events.append(qcluster)
    else:
        if SCENARIO != "missing_publish":
            timestamp = 1005 if SCENARIO == "publish_after_observe" else 1000
            events.append({
                "timestamp": timestamp,
                "message": "Published serving schema readiness marker " + key,
            })
    output({"events": events, "nextForwardToken": stream_token})

output({})
'''


@tag("core")
class CombinedTaskReadinessExecutionTest(SimpleTestCase):
    def _write_fake_aws(self, directory: Path) -> None:
        fake = directory / "aws"
        fake.write_text(FAKE_AWS.replace("__PYTHON__", sys.executable))
        fake.chmod(0o755)

    def _run(self, tmpdir: str, *, scenario: str = "success", timeout: int = 1):
        root = Path(tmpdir)
        bindir = root / "bin"
        bindir.mkdir()
        self._write_fake_aws(bindir)
        calls = root / "aws-calls.log"
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
                "FAKE_SCENARIO": scenario,
                "FAKE_TAG": TAG,
                "FAKE_REPOSITORY_URI": REPOSITORY_URI,
                "FAKE_DIGEST": DIGEST,
                "FAKE_TASK_DEFINITION_ARN": TASK_DEFINITION_ARN,
                "FAKE_OLD_TASK_DEFINITION_ARN": OLD_TASK_DEFINITION_ARN,
                "FAKE_TASK_ARN": TASK_ARN,
                "FAKE_SENTINEL_SECRET": SENTINEL_SECRET,
                "FAKE_SENTINEL_LOG": SENTINEL_LOG,
                "FAKE_AWS_CALLS": str(calls),
                "FAKE_DESCRIBE_TASKS_COUNT": str(root / "describe-count"),
                "FAKE_LOG_FIRST_PAGE_COUNT": str(root / "log-count"),
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--region",
                "eu-west-1",
                "--cluster",
                "ai-shipping-labs",
                "--service",
                "ai-shipping-labs-dev",
                "--repository-uri",
                REPOSITORY_URI,
                "--tag",
                TAG,
                "--timeout-seconds",
                str(timeout),
                "--poll-seconds",
                "5",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout + 5,
        )
        return result, calls.read_text() if calls.exists() else ""

    def _run_scenario(self, scenario: str, *, timeout: int = 5):
        # The verifier launches several fake-AWS subprocesses per scenario.
        # Keep enough test-only deadline headroom for Django's parallel core
        # suite, where process startup can be delayed by CPU contention.
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            return self._run(tmpdir, scenario=scenario, timeout=timeout)

    def assertSanitized(self, result: subprocess.CompletedProcess[str]) -> None:
        combined = result.stdout + result.stderr
        self.assertNotIn(SENTINEL_SECRET, combined)
        self.assertNotIn(SENTINEL_LOG, combined)
        self.assertNotIn("DATABASE_URL", combined)

    def test_success_proves_exact_task_digest_markers_and_final_liveness(self):
        result, calls = self._run_scenario("success")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("COMBINED_READINESS verified", result.stdout)
        self.assertIn(f"tag={TAG}", result.stdout)
        self.assertIn(f"digest={DIGEST}", result.stdout)
        self.assertIn(f"task_definition={TASK_DEFINITION_ARN}", result.stdout)
        self.assertIn("ai-shipping-labs:RUNNING", result.stdout)
        self.assertIn("ai-shipping-labs-worker:RUNNING", result.stdout)
        self.assertIn("markers=publish:", result.stdout)
        self.assertEqual(calls.count("ecs describe-tasks"), 2)
        self.assertIn(
            "--log-stream-name ecs/ai-shipping-labs/task-abc",
            calls,
        )
        self.assertIn(
            "--log-stream-name ecs/ai-shipping-labs-worker/task-abc",
            calls,
        )
        self.assertSanitized(result)

    def test_delayed_logs_are_polled_within_the_deadline(self):
        # Leave ample subprocess startup/AWS-fixture headroom around the
        # required five-second poll when the core suite runs many DB workers.
        result, calls = self._run_scenario("delayed_logs", timeout=20)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertGreaterEqual(calls.count("logs get-log-events"), 8)
        self.assertSanitized(result)

    def test_primary_revision_and_rollout_failures_are_terminal(self):
        for scenario, invariant in (
            ("old_primary", "task-definition-arn-mismatch"),
            ("rollout_in_progress", "primary-rollout-not-completed"),
            ("counts_not_ready", "primary-counts-not-ready"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_task_definition_role_and_image_mismatches_fail(self):
        for scenario, invariant in (
            ("task_definition_image_mismatch", "task-definition-image-mismatch"),
            ("task_definition_role_mismatch", "task-definition-role-mismatch"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_nonessential_worker_must_be_running(self):
        result, _ = self._run_scenario("worker_stopped")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariant=runtime-container-not-running", result.stderr)
        self.assertSanitized(result)

    def test_registry_and_runtime_image_identity_fail_closed(self):
        for scenario, invariant in (
            ("ecr_missing", "ecr-tag-or-digest-missing"),
            ("runtime_digest_mismatch", "runtime-image-digest-mismatch"),
            ("runtime_tag_mismatch", "runtime-image-tag-mismatch"),
            ("runtime_old_revision", "running-primary-task-missing"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_each_missing_marker_times_out_without_raw_logs(self):
        for scenario, marker in (
            ("missing_publish", "web-publish"),
            ("missing_observe", "worker-observe"),
            ("missing_qcluster", "worker-qcluster-start"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invariant=markers-missing-", result.stderr)
                self.assertIn(marker, result.stderr)
                self.assertSanitized(result)

    def test_marker_order_is_fail_closed(self):
        for scenario, invariant in (
            ("publish_after_observe", "marker-order-publish-after-observe"),
            ("qcluster_before_observe", "marker-order-qcluster-before-observe"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_other_task_evidence_cannot_satisfy_selected_task(self):
        result, calls = self._run_scenario("selected_task_incomplete")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariant=markers-missing-worker-observe", result.stderr)
        self.assertNotIn("task-other", calls)
        self.assertSanitized(result)

    def test_final_recheck_catches_worker_that_stopped_after_start_log(self):
        result, calls = self._run_scenario("final_worker_stopped")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariant=runtime-container-not-running", result.stderr)
        self.assertEqual(calls.count("ecs describe-tasks"), 2)
        self.assertSanitized(result)

    def test_aws_errors_and_malformed_payloads_are_sanitized(self):
        for scenario, invariant in (
            ("aws_error", "aws-ecs-describe-services-failed"),
            ("malformed", "aws-ecs-describe-services-malformed"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_pagination_cycles_fail_closed(self):
        for scenario, invariant in (
            ("task_list_pagination_cycle", "running-task-pagination-malformed"),
            ("cloudwatch_pagination_cycle", "cloudwatch-pagination-malformed"),
        ):
            with self.subTest(scenario=scenario):
                result, _ = self._run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invariant={invariant}", result.stderr)
                self.assertSanitized(result)

    def test_timeout_and_poll_validation_happen_before_aws(self):
        for option, value in (("--timeout-seconds", "181"), ("--poll-seconds", "4")):
            with self.subTest(option=option):
                command = [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--region",
                    "eu-west-1",
                    "--cluster",
                    "ai-shipping-labs",
                    "--service",
                    "ai-shipping-labs-dev",
                    "--repository-uri",
                    REPOSITORY_URI,
                    "--tag",
                    TAG,
                    "--timeout-seconds",
                    "1",
                    "--poll-seconds",
                    "5",
                ]
                command[command.index(option) + 1] = value
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "PATH": ""},
                    timeout=5,
                )
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("COMBINED_READINESS", result.stdout)


@tag("core")
class CombinedTaskReadinessWorkflowStructureTest(SimpleTestCase):
    def test_mandatory_verifier_runs_after_exact_ping_step(self):
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
        steps = workflow["jobs"]["deploy"]["steps"]
        names = [step.get("name") for step in steps]

        ping_index = names.index("Verify deploy landed")
        verifier_index = names.index("Verify combined web and worker readiness")
        self.assertEqual(verifier_index, ping_index + 1)
        verifier = steps[verifier_index]
        self.assertIn("verify_combined_task_readiness.py", verifier["run"])
        self.assertIn('--tag "${TAG}"', verifier["run"])
        self.assertNotIn("continue-on-error", verifier)

        permissions = workflow["jobs"]["deploy"]["permissions"]
        self.assertEqual(permissions, {"contents": "read", "id-token": "write"})
        self.assertNotIn("secrets", verifier)

    def test_scheduled_no_tag_wake_workflow_is_unchanged_by_verifier(self):
        scheduled = SCHEDULED_WAKE_PATH.read_text()

        self.assertNotIn("verify_combined_task_readiness", scheduled)
        self.assertIn("wake-dev-ecs", scheduled)

    def test_verifier_is_executable(self):
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK))
