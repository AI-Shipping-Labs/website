"""Shell-logic tests for the Phase 2A pre-deploy migrate+check gate.

Issue #1141 Phase 2A: ``deploy/deploy_dev.sh`` must run ONE pre-deploy
``aws ecs run-task`` (BOOT_MODE=predeploy) that runs migrate + ``check
--fail-level ERROR`` BEFORE rolling the service, and MUST abort the deploy
(without rolling the service) when that task exits non-zero.

Two layers:

1. Text-order assertions on the script (mirroring
   ``tests/test_dev_ecs_wake_workflows.py``): the run-task and its exit-code
   gate precede ``update-service``; the prod path runs a single pre-deploy
   task before BOTH service rollouts.
2. A real end-to-end run of the script with a STUBBED ``aws`` on PATH: the
   critical fail-closed property — a non-zero pre-deploy exit fails the
   script and never reaches ``update-service`` — is proven by execution, and
   the happy path (exit 0 -> ``update-service`` proceeds) is proven too.
"""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "deploy_dev.sh"


# A stub ``aws`` CLI: logs every invocation and returns canned output so the
# real deploy script can run end-to-end offline. ``FAKE_PREDEPLOY_EXIT``
# controls the exit code the pre-deploy migrate+check task reports.
FAKE_AWS_TEMPLATE = '''#!{python}
import os
import sys

args = sys.argv[1:]
log = os.environ.get("FAKE_AWS_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(" ".join(args) + "\\n")


def query():
    if "--query" in args:
        return args[args.index("--query") + 1]
    return ""


svc = args[0] if args else ""
sub = args[1] if len(args) > 1 else ""

if svc == "ecs" and sub == "describe-services":
    q = query()
    if "networkConfiguration" in q:
        print('{{"awsvpcConfiguration": {{"subnets": ["subnet-1"], '
              '"securityGroups": ["sg-1"], "assignPublicIp": "DISABLED"}}}}')
    elif "capacityProviderStrategy" in q:
        print("null")
    elif "launchType" in q:
        print("FARGATE")
    elif "taskDefinition" in q:
        print("arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:1")
    else:
        print("None")
    sys.exit(0)

if svc == "ecs" and sub == "describe-task-definition":
    if "essential" in query():
        print("ai-shipping-labs")
        sys.exit(0)
    print('{{"taskDefinition": {{"family": "ai-shipping-labs", '
          '"containerDefinitions": [{{"name": "ai-shipping-labs", '
          '"image": "repo:old", "essential": true, "environment": [], '
          '"portMappings": []}}]}}}}')
    sys.exit(0)

if svc == "ecs" and sub == "register-task-definition":
    print("arn:aws:ecs:eu-west-1:123:task-definition/ai-shipping-labs:2")
    sys.exit(0)

if svc == "ecs" and sub == "run-task":
    print("arn:aws:ecs:eu-west-1:123:task/predeploy-abc")
    sys.exit(0)

if svc == "ecs" and sub == "wait":
    sys.exit(0)

if svc == "ecs" and sub == "describe-tasks":
    q = query()
    # The exit-code gate query ends with "| [0]"; the diagnostics query does
    # not. Only the gate should read the simulated exit code.
    if "exitCode" in q and "|" in q:
        print(os.environ.get("FAKE_PREDEPLOY_EXIT", "0"))
    else:
        print("{{}}")
    sys.exit(0)

if svc == "ecs" and sub == "update-service":
    print("{{}}")
    sys.exit(0)

print("{{}}")
sys.exit(0)
'''


@tag("core")
class PredeployGateTextOrderTest(SimpleTestCase):
    def setUp(self):
        self.script = DEPLOY_SCRIPT_PATH.read_text()

    def test_run_task_precedes_update_service(self):
        run_task = self.script.index("aws ecs run-task")
        update_service = self.script.index("aws ecs update-service")
        self.assertLess(run_task, update_service)

    def test_predeploy_uses_boot_mode_predeploy_override(self):
        self.assertIn('"value":"predeploy"', self.script)
        self.assertIn("BOOT_MODE", self.script)

    def test_exit_code_gate_precedes_update_service(self):
        gate = self.script.index('if [ "${EXIT_CODE}" != "0" ]')
        update_service = self.script.index("aws ecs update-service")
        self.assertLess(
            gate,
            update_service,
            "the non-zero exit-code gate must precede (and gate) update-service",
        )

    def test_gate_aborts_with_exit_1_before_rolling(self):
        # The failure branch must exit 1 (abort) rather than continue.
        gate_block = self.script.split('if [ "${EXIT_CODE}" != "0" ]', 1)[1]
        gate_block = gate_block.split("fi", 1)[0]
        self.assertIn("exit 1", gate_block)

    def test_deploy_service_runs_gate_before_rolling(self):
        # In the per-service flow, the gate call precedes the roll call.
        gate_call = self.script.index('run_predeploy_migrate_check "${SERVICE}"')
        roll_call = self.script.index('roll_service "${SERVICE}"')
        self.assertLess(gate_call, roll_call)

    def test_prod_runs_single_predeploy_before_both_rollouts(self):
        # Exactly one pre-deploy task on the prod path, before BOTH rollouts.
        predeploy_call = 'run_predeploy_migrate_check "${WEB_SERVICE}"'
        self.assertEqual(self.script.count(predeploy_call), 1)

        predeploy_idx = self.script.index(predeploy_call)
        worker_roll = self.script.index('roll_service "${WORKER_SERVICE}"')
        web_roll = self.script.index('roll_service "${WEB_SERVICE}"')
        self.assertLess(predeploy_idx, worker_roll)
        self.assertLess(predeploy_idx, web_roll)

    def test_predeploy_uses_the_registered_task_def_arn(self):
        # The gate must run against the same ARN the service will roll to.
        self.assertIn(
            'run_predeploy_migrate_check "${SERVICE}" "${NEW_TASK_DEF_ARN}"',
            self.script,
        )


@tag("core")
class PredeployGateExecutionTest(SimpleTestCase):
    """Run the real script with a stubbed ``aws`` to prove fail-closed."""

    def _run_deploy(self, tmpdir, predeploy_exit):
        bindir = Path(tmpdir) / "bin"
        bindir.mkdir()
        aws_path = bindir / "aws"
        aws_path.write_text(FAKE_AWS_TEMPLATE.format(python=sys.executable))
        aws_path.chmod(0o755)

        log_path = Path(tmpdir) / "aws_calls.log"

        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env["FAKE_AWS_LOG"] = str(log_path)
        env["FAKE_PREDEPLOY_EXIT"] = predeploy_exit

        result = subprocess.run(
            ["bash", str(DEPLOY_SCRIPT_PATH), "testtag-20260708", "dev"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        calls = log_path.read_text() if log_path.exists() else ""
        return result, calls

    def test_happy_path_runs_predeploy_then_updates_service(self):
        with TemporaryDirectory() as tmpdir:
            result, calls = self._run_deploy(tmpdir, predeploy_exit="0")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("ecs run-task", calls)
        self.assertIn("ecs update-service", calls)
        # run-task must have happened before update-service.
        self.assertLess(calls.index("ecs run-task"), calls.index("ecs update-service"))
        self.assertIn("deployment completed successfully", result.stdout)

    def test_failing_predeploy_aborts_and_never_rolls_service(self):
        with TemporaryDirectory() as tmpdir:
            result, calls = self._run_deploy(tmpdir, predeploy_exit="1")

        # The deploy must FAIL (non-zero) ...
        self.assertNotEqual(result.returncode, 0)
        # ... the pre-deploy task did run ...
        self.assertIn("ecs run-task", calls)
        # ... but the service was NEVER rolled (no update-service call).
        self.assertNotIn(
            "ecs update-service",
            calls,
            msg="service must NOT be rolled when pre-deploy migrate/check fails",
        )
        self.assertIn("service NOT rolled", result.stdout)
