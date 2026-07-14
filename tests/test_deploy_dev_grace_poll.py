import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "deploy_dev.sh"
WAKE_ACTION_PATH = REPO_ROOT / ".github" / "actions" / "wake-dev-ecs" / "action.yml"
SCRATCH_ROOT = REPO_ROOT / ".tmp" / "test-deploy-dev-grace-poll"

OLD_TASK_DEF = "arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:1"
NEW_TASK_DEF = "arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:2"
RUNNING_TASK = "arn:aws:ecs:eu-west-1:123:task/ready"


FAKE_AWS = rf'''#!__PYTHON__
import os
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_AWS_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(args) + "\n")


def query():
    if "--query" in args:
        return args[args.index("--query") + 1]
    return ""


svc = args[0] if args else ""
sub = args[1] if len(args) > 1 else ""
q = query()

if svc == "ecs" and sub == "describe-services":
    if "deployments" in q:
        print(
            os.environ.get("FAKE_PRIMARY_TASK_DEF", "{NEW_TASK_DEF}"),
            os.environ.get("FAKE_DESIRED_COUNT", "1"),
            os.environ.get("FAKE_RUNNING_COUNT", "1"),
            sep="\t",
        )
    elif "taskDefinition" in q:
        print("{OLD_TASK_DEF}")
    elif "events" in q:
        print("[]")
    elif "loadBalancers" in q:
        print("arn:aws:elasticloadbalancing:eu-west-1:123:targetgroup/aisl-dev/abc")
    else:
        print("None")
    sys.exit(0)

if svc == "ecs" and sub == "describe-task-definition":
    if "containerDefinitions[].name" in q:
        print("ai-shipping-labs\tai-shipping-labs-worker")
    else:
        print(
            '{{"taskDefinition": {{"family": "ai-shipping-labs", '
            '"containerDefinitions": ['
            '{{"name": "ai-shipping-labs", "image": "repo:old", '
            '"essential": true, "environment": [], '
            '"portMappings": [{{"containerPort": 8000}}]}}, '
            '{{"name": "ai-shipping-labs-worker", "image": "repo:old", '
            '"essential": false, "environment": [], "portMappings": []}}'
            '], "networkMode": "awsvpc", "cpu": "256", "memory": "512"}}}}'
        )
    sys.exit(0)

if svc == "ecs" and sub == "register-task-definition":
    print("{NEW_TASK_DEF}")
    sys.exit(0)

if svc == "ecs" and sub == "update-service":
    print("{{}}")
    sys.exit(0)

if svc == "ecs" and sub == "wait":
    if len(args) > 2 and args[2] == "services-stable":
        sys.exit(int(os.environ.get("FAKE_SERVICES_STABLE_EXIT", "0")))
    sys.exit(0)

if svc == "ecs" and sub == "list-tasks":
    if "--desired-status" in args and args[args.index("--desired-status") + 1] == "RUNNING":
        print("{RUNNING_TASK}")
    else:
        print("")
    sys.exit(0)

if svc == "ecs" and sub == "describe-tasks":
    if "taskDefinitionArn,lastStatus" in q:
        print(
            os.environ.get("FAKE_RUNNING_TASK_DEF", "{NEW_TASK_DEF}"),
            os.environ.get("FAKE_TASK_LAST_STATUS", "RUNNING"),
            sep="\t",
        )
    elif "containers[].[name,lastStatus]" in q:
        print(
            "ai-shipping-labs",
            os.environ.get("FAKE_WEB_STATUS", "RUNNING"),
            sep="\t",
        )
        print(
            "ai-shipping-labs-worker",
            os.environ.get("FAKE_WORKER_STATUS", "RUNNING"),
            sep="\t",
        )
    else:
        print("{{}}")
    sys.exit(0)

if svc == "elbv2" and sub == "describe-target-health":
    print("target unhealthy")
    sys.exit(0)

print("{{}}")
sys.exit(0)
'''


FAKE_CURL = r'''#!__PYTHON__
import os
import sys

count_file = os.environ["FAKE_CURL_COUNT_FILE"]
try:
    with open(count_file) as fh:
        attempt = int(fh.read().strip() or "0") + 1
except FileNotFoundError:
    attempt = 1

with open(count_file, "w") as fh:
    fh.write(str(attempt))

log = os.environ.get("FAKE_CURL_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\n")

responses = os.environ.get("FAKE_CURL_RESPONSES", "previous-tag").split(",")
response = responses[min(attempt - 1, len(responses) - 1)]
if response != "<unreachable>":
    print(response)
'''


FAKE_PYTHON = r'''#!__PYTHON__
import os
import sys

if len(sys.argv) >= 3 and sys.argv[1] == "-c" and "time.monotonic" in sys.argv[2]:
    count_file = os.environ["FAKE_CLOCK_COUNT_FILE"]
    try:
        with open(count_file) as fh:
            count = int(fh.read().strip() or "0")
    except FileNotFoundError:
        count = 0
    with open(count_file, "w") as fh:
        fh.write(str(count + 1))
    start = int(os.environ.get("FAKE_MONOTONIC_START", "0"))
    step = int(os.environ.get("FAKE_MONOTONIC_STEP", "0"))
    print(start + count * step)
    raise SystemExit(0)

os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *sys.argv[1:]])
'''


FAKE_SLEEP = r'''#!/bin/sh
printf '%s\n' "$1" >> "${FAKE_SLEEP_LOG}"
'''


FAKE_TIMEOUT = r'''#!/bin/sh
if [ "$1" = "--signal=TERM" ]; then
    shift
fi
printf '%s\n' "$1" >> "${FAKE_TIMEOUT_LOG}"
shift
exec "$@"
'''


@tag("core")
class DeployDevGracePollExecutionTest(SimpleTestCase):
    def _write_executable(self, path, script):
        path.write_text(script.replace("__PYTHON__", sys.executable))
        path.chmod(0o755)

    def _run_deploy(
        self,
        tmpdir,
        *,
        tag="20260708-011950-8dc969b",
        deploy_env="dev",
        services_stable_exit="1",
        responses=None,
        timeout_seconds=1500,
        poll_seconds=0,
        max_attempts=150,
        required_matches=3,
        monotonic_step=0,
        primary_task_def=NEW_TASK_DEF,
        desired_count="1",
        running_count="1",
        running_task_def=NEW_TASK_DEF,
        task_last_status="RUNNING",
        web_status="RUNNING",
        worker_status="RUNNING",
    ):
        tmpdir = Path(tmpdir)
        bindir = tmpdir / "bin"
        bindir.mkdir()
        self._write_executable(bindir / "aws", FAKE_AWS)
        self._write_executable(bindir / "curl", FAKE_CURL)
        self._write_executable(bindir / "python", FAKE_PYTHON)
        self._write_executable(bindir / "python3", FAKE_PYTHON)
        self._write_executable(bindir / "sleep", FAKE_SLEEP)
        self._write_executable(bindir / "timeout", FAKE_TIMEOUT)

        aws_log = tmpdir / "aws_calls.log"
        curl_log = tmpdir / "curl_calls.log"
        curl_count = tmpdir / "curl_count.txt"
        clock_count = tmpdir / "clock_count.txt"
        sleep_log = tmpdir / "sleep.log"
        timeout_log = tmpdir / "timeout.log"

        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env.update({
            "REAL_PYTHON": sys.executable,
            "FAKE_AWS_LOG": str(aws_log),
            "FAKE_CURL_LOG": str(curl_log),
            "FAKE_CURL_COUNT_FILE": str(curl_count),
            "FAKE_CLOCK_COUNT_FILE": str(clock_count),
            "FAKE_SLEEP_LOG": str(sleep_log),
            "FAKE_TIMEOUT_LOG": str(timeout_log),
            "FAKE_SERVICES_STABLE_EXIT": services_stable_exit,
            "FAKE_CURL_RESPONSES": ",".join(responses or [tag, tag, tag]),
            "FAKE_MONOTONIC_STEP": str(monotonic_step),
            "FAKE_PRIMARY_TASK_DEF": primary_task_def,
            "FAKE_DESIRED_COUNT": desired_count,
            "FAKE_RUNNING_COUNT": running_count,
            "FAKE_RUNNING_TASK_DEF": running_task_def,
            "FAKE_TASK_LAST_STATUS": task_last_status,
            "FAKE_WEB_STATUS": web_status,
            "FAKE_WORKER_STATUS": worker_status,
            "DEPLOY_GRACE_TIMEOUT_SECONDS": str(timeout_seconds),
            "DEPLOY_GRACE_POLL_SECONDS": str(poll_seconds),
            "DEPLOY_GRACE_ATTEMPTS": str(max_attempts),
            "DEPLOY_GRACE_REQUIRED_MATCHES": str(required_matches),
        })
        for name in (
            "PREDEPLOY_MIGRATE_CHECK_ENABLED",
            "DEPLOY_GRACE_MAX_ATTEMPTS",
            "DEPLOY_GRACE_SLEEP_SECONDS",
        ):
            env.pop(name, None)

        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT_PATH), tag, deploy_env],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        return {
            "result": result,
            "aws_calls": aws_log.read_text() if aws_log.exists() else "",
            "curl_calls": curl_log.read_text() if curl_log.exists() else "",
            "curl_attempts": int(curl_count.read_text()) if curl_count.exists() else 0,
            "sleep_calls": sleep_log.read_text().splitlines() if sleep_log.exists() else [],
            "timeout_calls": timeout_log.read_text().splitlines() if timeout_log.exists() else [],
        }

    def _run_wake_action(
        self,
        tmpdir,
        *,
        responses,
        expected_text="release-tag",
        timeout_seconds="1500",
        poll_seconds="0",
        max_attempts="150",
        required_matches="3",
        monotonic_step=0,
    ):
        tmpdir = Path(tmpdir)
        bindir = tmpdir / "bin"
        bindir.mkdir()
        self._write_executable(bindir / "aws", FAKE_AWS)
        self._write_executable(bindir / "curl", FAKE_CURL)
        self._write_executable(bindir / "python3", FAKE_PYTHON)
        self._write_executable(bindir / "sleep", FAKE_SLEEP)

        aws_log = tmpdir / "aws_calls.log"
        curl_count = tmpdir / "curl_count.txt"
        clock_count = tmpdir / "clock_count.txt"
        sleep_log = tmpdir / "sleep.log"
        action = yaml.safe_load(WAKE_ACTION_PATH.read_text())

        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env.update({
            "REAL_PYTHON": sys.executable,
            "FAKE_AWS_LOG": str(aws_log),
            "FAKE_CURL_COUNT_FILE": str(curl_count),
            "FAKE_CLOCK_COUNT_FILE": str(clock_count),
            "FAKE_SLEEP_LOG": str(sleep_log),
            "FAKE_CURL_RESPONSES": ",".join(responses),
            "FAKE_MONOTONIC_STEP": str(monotonic_step),
            "GITHUB_ACTION_PATH": str(WAKE_ACTION_PATH.parent),
            "AWS_REGION": "eu-west-1",
            "ECS_CLUSTER": "ai-shipping-labs",
            "ECS_SERVICE": "ai-shipping-labs-dev",
            "DESIRED_COUNT": "1",
            "PING_URL": "https://dev.example.test/ping",
            "EXPECTED_TEXT": expected_text,
            "TIMEOUT_SECONDS": str(timeout_seconds),
            "POLL_SECONDS": str(poll_seconds),
            "MAX_ATTEMPTS": str(max_attempts),
            "REQUIRED_CONSECUTIVE": str(required_matches),
        })
        env.pop("READINESS_PYTHON_BIN", None)

        result = subprocess.run(
            ["bash", "-c", action["runs"]["steps"][0]["run"]],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "result": result,
            "aws_calls": aws_log.read_text() if aws_log.exists() else "",
            "curl_attempts": int(curl_count.read_text()) if curl_count.exists() else 0,
            "sleep_calls": sleep_log.read_text().splitlines() if sleep_log.exists() else [],
        }

    def test_waiter_timeout_requires_stable_tag_and_healthy_new_revision(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                responses=["previous-tag", *(["20260708-011950-8dc969b"] * 3)],
            )

        result = run["result"]
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertEqual(run["curl_attempts"], 4)
        self.assertIn("WARNING: ECS waiter timed out", result.stdout)
        self.assertIn("ECS recovery state ready", result.stdout)
        self.assertIn(f"task_definition={NEW_TASK_DEF}", result.stdout)
        self.assertIn("task_last_status=RUNNING", result.stdout)
        self.assertIn("consecutive=3/3", result.stdout)
        self.assertNotIn("--- Recent ECS service events ---", result.stdout)

    def test_transient_exact_responses_do_not_turn_deploy_green(self):
        tag = "20260708-011950-8dc969b"
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                responses=[tag, tag, "previous-tag", tag, tag, tag],
                max_attempts=6,
            )

        self.assertEqual(run["result"].returncode, 0)
        self.assertEqual(run["curl_attempts"], 6)
        self.assertIn("consecutive=0/3", run["result"].stdout)
        self.assertIn("consecutive=3/3", run["result"].stdout)

    def test_hard_deadline_includes_slow_curl_and_does_not_sleep_after(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                timeout_seconds=10,
                poll_seconds=10,
                monotonic_step=6,
            )

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run["curl_attempts"], 1)
        self.assertEqual(run["sleep_calls"], [])
        self.assertIn("hard 10s monotonic recovery deadline", result.stdout)
        self.assertIn("Recovery deadline exhausted", result.stdout)

    def test_ecs_verification_must_finish_inside_the_same_deadline(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                timeout_seconds=10,
                max_attempts=3,
                monotonic_step=1,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertEqual(run["curl_attempts"], 3)
        self.assertEqual(run["timeout_calls"], ["1s"])
        self.assertIn("task-definition-unavailable", run["result"].stdout)
        self.assertNotIn("WARNING: ECS waiter timed out", run["result"].stdout)

    def test_ecs_verification_finishing_at_deadline_is_rejected(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                timeout_seconds=14,
                max_attempts=3,
                monotonic_step=1,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertEqual(run["curl_attempts"], 3)
        self.assertEqual(run["timeout_calls"], ["5s", "4s", "3s", "2s", "1s"])
        self.assertIn(
            "Readiness verification completed after the hard deadline",
            run["result"].stdout,
        )
        self.assertNotIn("WARNING: ECS waiter timed out", run["result"].stdout)

    def test_no_sleep_occurs_after_final_attempt(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                responses=["old", "old"],
                poll_seconds=7,
                max_attempts=2,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertEqual(run["sleep_calls"], ["7"])

    def test_deploy_invalid_recovery_config_fails_before_polling(self):
        cases = (
            ({"timeout_seconds": 0}, "DEPLOY_GRACE_TIMEOUT_SECONDS"),
            ({"timeout_seconds": "invalid"}, "DEPLOY_GRACE_TIMEOUT_SECONDS"),
            ({"poll_seconds": -1}, "DEPLOY_GRACE_POLL_SECONDS"),
            ({"poll_seconds": "invalid"}, "DEPLOY_GRACE_POLL_SECONDS"),
            ({"max_attempts": 0}, "DEPLOY_GRACE_ATTEMPTS"),
            ({"required_matches": 0}, "DEPLOY_GRACE_REQUIRED_MATCHES"),
        )
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        for overrides, error_name in cases:
            with self.subTest(overrides=overrides):
                with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
                    run = self._run_deploy(tmpdir, **overrides)

                self.assertNotEqual(run["result"].returncode, 0)
                self.assertEqual(run["curl_attempts"], 0)
                self.assertIn(error_name, run["result"].stderr)
                self.assertEqual(run["aws_calls"], "")

    def test_stale_tag_exhaustion_fails_and_runs_diagnostics(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                responses=["20260707-235959-oldsha"],
                max_attempts=2,
            )

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run["curl_attempts"], 2)
        self.assertIn("Recovery deadline exhausted", result.stdout)
        self.assertIn("--- Recent ECS service events ---", result.stdout)
        self.assertIn("ecs list-tasks", run["aws_calls"])
        self.assertIn("elbv2 describe-target-health", run["aws_calls"])

    def test_revision_mismatch_rejects_stable_tag(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                max_attempts=3,
                primary_task_def=OLD_TASK_DEF,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertIn("ECS recovery state not ready", run["result"].stdout)
        self.assertIn(f"primary={OLD_TASK_DEF}", run["result"].stdout)

    def test_running_tasks_on_old_revision_reject_stable_tag(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                max_attempts=3,
                running_task_def=OLD_TASK_DEF,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertIn("no-matching-all-running-task", run["result"].stdout)

    def test_desired_running_task_with_pending_last_status_is_rejected(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                max_attempts=3,
                task_last_status="PENDING",
                web_status="RUNNING",
                worker_status="RUNNING",
            )

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"task_definition={NEW_TASK_DEF} last_status=PENDING",
            result.stdout,
        )
        self.assertIn(
            f"primary={NEW_TASK_DEF} desired=1 running=1",
            result.stdout,
        )
        self.assertIn(
            "tasks[0].[taskDefinitionArn,lastStatus]",
            run["aws_calls"],
        )
        self.assertIn("no-matching-all-running-task", result.stdout)
        self.assertNotIn("ECS recovery state ready", result.stdout)
        self.assertNotIn("WARNING: ECS waiter timed out", result.stdout)

    def test_insufficient_running_count_rejects_stable_tag(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                max_attempts=3,
                desired_count="1",
                running_count="0",
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertIn("desired=1 running=0", run["result"].stdout)

    def test_stopped_worker_sidecar_rejects_stable_tag(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                max_attempts=3,
                worker_status="STOPPED",
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertIn("no-matching-all-running-task", run["result"].stdout)

    def test_unreachable_endpoint_remains_fail_closed(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                responses=["<unreachable>"],
                max_attempts=2,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertIn("response_state=empty-or-unreachable", run["result"].stdout)

    def test_worker_waiter_timeout_still_fails_without_ping_shortcut(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(tmpdir, deploy_env="prod")

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run["curl_attempts"], 0)
        self.assertIn(
            "ERROR: ai-shipping-labs-worker-prod did not reach steady state.",
            result.stdout,
        )
        self.assertIn("--- Recent ECS service events ---", result.stdout)
        self.assertNotIn("/ping", run["curl_calls"])

    def test_wake_action_executes_transient_then_stable_exact_match(self):
        tag_value = "release-tag"
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_wake_action(
                tmpdir,
                responses=[tag_value, tag_value, "stale", tag_value, tag_value, tag_value],
                max_attempts="6",
            )

        self.assertEqual(run["result"].returncode, 0)
        self.assertEqual(run["curl_attempts"], 6)
        self.assertIn("satisfied its response expectation stably", run["result"].stdout)
        self.assertIn("ecs update-service", run["aws_calls"])

    def test_wake_action_rejects_exact_response_completed_at_deadline(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_wake_action(
                tmpdir,
                responses=["release-tag"],
                timeout_seconds="10",
                poll_seconds="10",
                monotonic_step=6,
            )

        self.assertNotEqual(run["result"].returncode, 0)
        self.assertEqual(run["curl_attempts"], 1)
        self.assertEqual(run["sleep_calls"], [])
        self.assertIn("rejecting the response", run["result"].stdout)

    def test_wake_action_invalid_config_fails_before_polling(self):
        cases = (
            ({"timeout_seconds": "0"}, "readiness timeout"),
            ({"timeout_seconds": "invalid"}, "readiness timeout"),
            ({"poll_seconds": "-1"}, "readiness poll interval"),
            ({"poll_seconds": "invalid"}, "readiness poll interval"),
            ({"max_attempts": "0"}, "readiness max attempts"),
            ({"required_matches": "0"}, "readiness required matches"),
        )
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        for overrides, error_text in cases:
            with self.subTest(overrides=overrides):
                with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
                    run = self._run_wake_action(
                        tmpdir,
                        responses=["release-tag"],
                        **overrides,
                    )

                self.assertNotEqual(run["result"].returncode, 0)
                self.assertEqual(run["curl_attempts"], 0)
                self.assertIn(error_text, run["result"].stderr)
                self.assertEqual(run["aws_calls"], "")

    def test_wake_action_never_logs_an_unexpected_response_body(self):
        secret_body = "super-secret-body"
        secret_expected = "super-secret-expected-text"
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_wake_action(
                tmpdir,
                responses=[secret_body],
                expected_text=secret_expected,
                max_attempts="1",
            )

        output = run["result"].stdout + run["result"].stderr
        self.assertNotEqual(run["result"].returncode, 0)
        self.assertNotIn(secret_body, output)
        self.assertNotIn(secret_expected, output)
        self.assertIn("response_state=non-matching", output)
