import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "deploy_dev.sh"
SCRATCH_ROOT = REPO_ROOT / ".tmp" / "test-deploy-dev-grace-poll"


FAKE_AWS = r'''#!__PYTHON__
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

if svc == "ecs" and sub == "describe-services":
    q = query()
    if "taskDefinition" in q:
        print("arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:1")
    elif "events" in q:
        print("[]")
    elif "loadBalancers" in q:
        print("arn:aws:elasticloadbalancing:eu-west-1:123:targetgroup/aisl-dev/abc")
    else:
        print("None")
    sys.exit(0)

if svc == "ecs" and sub == "describe-task-definition":
    print(
        '{"taskDefinition": {"family": "ai-shipping-labs", '
        '"containerDefinitions": ['
        '{"name": "ai-shipping-labs", "image": "repo:old", '
        '"essential": true, "environment": [], '
        '"portMappings": [{"containerPort": 8000}]}, '
        '{"name": "ai-shipping-labs-worker", "image": "repo:old", '
        '"essential": false, "environment": [], "portMappings": []}'
        '], "networkMode": "awsvpc", "cpu": "256", "memory": "512"}}'
    )
    sys.exit(0)

if svc == "ecs" and sub == "register-task-definition":
    print("arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:2")
    sys.exit(0)

if svc == "ecs" and sub == "update-service":
    print("{}")
    sys.exit(0)

if svc == "ecs" and sub == "wait":
    if len(args) > 2 and args[2] == "services-stable":
        sys.exit(int(os.environ.get("FAKE_SERVICES_STABLE_EXIT", "0")))
    sys.exit(0)

if svc == "ecs" and sub == "list-tasks":
    print("")
    sys.exit(0)

if svc == "ecs" and sub == "describe-tasks":
    print("{}")
    sys.exit(0)

if svc == "elbv2" and sub == "describe-target-health":
    print("target unhealthy")
    sys.exit(0)

print("{}")
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

expected_after = os.environ.get("FAKE_CURL_EXPECTED_AFTER")
if expected_after and attempt >= int(expected_after):
    print(os.environ["FAKE_EXPECTED_TAG"])
else:
    print(os.environ.get("FAKE_CURL_STALE_TAG", "previous-tag"))
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
        curl_expected_after=None,
        grace_attempts=None,
        grace_sleep_seconds=None,
        stale_tag="previous-tag",
    ):
        tmpdir = Path(tmpdir)
        bindir = tmpdir / "bin"
        bindir.mkdir()
        self._write_executable(bindir / "aws", FAKE_AWS)
        self._write_executable(bindir / "curl", FAKE_CURL)

        aws_log = tmpdir / "aws_calls.log"
        curl_log = tmpdir / "curl_calls.log"
        curl_count = tmpdir / "curl_count.txt"

        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env["FAKE_AWS_LOG"] = str(aws_log)
        env["FAKE_CURL_LOG"] = str(curl_log)
        env["FAKE_CURL_COUNT_FILE"] = str(curl_count)
        env["FAKE_EXPECTED_TAG"] = tag
        env["FAKE_CURL_STALE_TAG"] = stale_tag
        env["FAKE_SERVICES_STABLE_EXIT"] = services_stable_exit

        for name in (
            "PREDEPLOY_MIGRATE_CHECK_ENABLED",
            "DEPLOY_GRACE_ATTEMPTS",
            "DEPLOY_GRACE_SLEEP_SECONDS",
        ):
            env.pop(name, None)

        if curl_expected_after is not None:
            env["FAKE_CURL_EXPECTED_AFTER"] = str(curl_expected_after)
        if grace_attempts is not None:
            env["DEPLOY_GRACE_ATTEMPTS"] = str(grace_attempts)
        if grace_sleep_seconds is not None:
            env["DEPLOY_GRACE_SLEEP_SECONDS"] = str(grace_sleep_seconds)

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
        }

    def test_waiter_timeout_succeeds_when_expected_tag_eventually_served(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                curl_expected_after=2,
                grace_attempts=3,
                grace_sleep_seconds=0,
            )

        result = run["result"]
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertEqual(run["curl_attempts"], 2)
        self.assertIn("WARNING: ECS waiter timed out", result.stdout)
        self.assertIn("serving the expected tag", result.stdout)
        self.assertIn("deployment completed successfully", result.stdout)
        self.assertNotIn("--- Recent ECS service events ---", result.stdout)

    def test_unset_grace_env_uses_documented_default_window(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(tmpdir, curl_expected_after=1)

        result = run["result"]
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertEqual(run["curl_attempts"], 1)
        self.assertIn("for up to 1500s", result.stdout)

    def test_waiter_timeout_fails_and_diagnoses_when_expected_tag_never_served(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                grace_attempts=2,
                grace_sleep_seconds=0,
                stale_tag="20260707-235959-oldsha",
            )

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run["curl_attempts"], 2)
        self.assertIn("Grace attempt 2/2", result.stdout)
        self.assertIn("expected '20260708-011950-8dc969b'", result.stdout)
        self.assertIn("--- Recent ECS service events ---", result.stdout)
        self.assertIn("ecs list-tasks", run["aws_calls"])
        self.assertIn("elbv2 describe-target-health", run["aws_calls"])

    def test_worker_waiter_timeout_still_fails_without_ping_shortcut(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=SCRATCH_ROOT) as tmpdir:
            run = self._run_deploy(
                tmpdir,
                deploy_env="prod",
                curl_expected_after=1,
                grace_attempts=2,
                grace_sleep_seconds=0,
            )

        result = run["result"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(run["curl_attempts"], 0)
        self.assertIn("ERROR: ai-shipping-labs-worker-prod did not reach steady state.", result.stdout)
        self.assertIn("--- Recent ECS service events ---", result.stdout)
        self.assertNotIn("/ping", run["curl_calls"])
