import importlib.util
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase


def _load_update_task_def_module():
    module_path = Path(__file__).resolve().parent.parent / "deploy" / "update_task_def.py"
    spec = importlib.util.spec_from_file_location("deploy_update_task_def", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update_task_def = _load_update_task_def_module()


class UpdateTaskDefinitionAllowedHostsTest(SimpleTestCase):
    def _write_task_definition(self, path, *, environment=None):
        task_definition = {
            "taskDefinition": {
                "containerDefinitions": [
                    {
                        "name": "ai-shipping-labs",
                        "image": "repo:old",
                        "environment": environment or [],
                    },
                    {
                        "name": "ai-shipping-labs-worker",
                        "image": "repo:old",
                        "environment": environment or [],
                    },
                ],
            }
        }
        path.write_text(json.dumps(task_definition))

    def _read_task_definition(self, path):
        return json.loads(path.read_text())

    def test_dev_deploy_sets_dev_allowed_host(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(input_path)

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(
                    str(input_path),
                    "20260422-123456-abcd123",
                    str(output_path),
                    "dev",
                )

            task_def = self._read_task_definition(output_path)

        for container in task_def["containerDefinitions"]:
            environment = {item["name"]: item["value"] for item in container["environment"]}
            self.assertEqual(environment["VERSION"], "20260422-123456-abcd123")
            self.assertEqual(environment["DEBUG"], "False")
            self.assertEqual(environment["ALLOWED_HOSTS"], "dev.aishippinglabs.com")

    def test_prod_deploy_sets_prod_allowed_hosts(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(
                input_path,
                environment=[{"name": "ALLOWED_HOSTS", "value": "internal-alb.local"}],
            )

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(
                    str(input_path),
                    "20260422-123456-abcd123",
                    str(output_path),
                    "prod",
                )

            task_def = self._read_task_definition(output_path)

        for container in task_def["containerDefinitions"]:
            environment = {item["name"]: item["value"] for item in container["environment"]}
            self.assertEqual(environment["DEBUG"], "False")
            self.assertEqual(
                environment["ALLOWED_HOSTS"],
                "aishippinglabs.com,www.aishippinglabs.com,prod.aishippinglabs.com",
            )

    def test_run_migrations_only_set_on_web_container(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(input_path)

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(
                    str(input_path),
                    "20260422-123456-abcd123",
                    str(output_path),
                    "dev",
                )

            task_def = self._read_task_definition(output_path)

        env_by_container = {
            container["name"]: {
                item["name"]: item["value"] for item in container["environment"]
            }
            for container in task_def["containerDefinitions"]
        }
        self.assertEqual(env_by_container["ai-shipping-labs"]["RUN_MIGRATIONS"], "true")
        self.assertEqual(
            env_by_container["ai-shipping-labs-worker"]["RUN_MIGRATIONS"], "false"
        )
