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
                "aishippinglabs.com,www.aishippinglabs.com",
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

    def _env_by_container(self, deploy_env, role=None):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(input_path)

            args = [str(input_path), "20260708-abcd123", str(output_path), deploy_env]
            if role is not None:
                args.append(role)

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(*args)

            task_def = self._read_task_definition(output_path)

        return {
            container["name"]: {
                item["name"]: item["value"] for item in container["environment"]
            }
            for container in task_def["containerDefinitions"]
        }

    def test_boot_mode_set_per_container_for_combined_dev(self):
        # Issue #1141 Phase 2A: the essential (web) container serves in
        # BOOT_MODE=web; the worker sidecar in BOOT_MODE=worker. Neither
        # migrates/checks on the serving boot — the pre-deploy task does.
        env_by_container = self._env_by_container("dev")

        self.assertEqual(env_by_container["ai-shipping-labs"]["BOOT_MODE"], "web")
        self.assertEqual(
            env_by_container["ai-shipping-labs-worker"]["BOOT_MODE"], "worker"
        )

    def test_boot_mode_set_for_prod_web_and_worker_roles(self):
        web_env = self._env_by_container("prod", "web")
        worker_env = self._env_by_container("prod", "worker")

        self.assertEqual(web_env["ai-shipping-labs"]["BOOT_MODE"], "web")
        self.assertEqual(
            worker_env["ai-shipping-labs-worker"]["BOOT_MODE"], "worker"
        )

    def test_run_migrations_kept_for_backward_compat_fallback(self):
        # RUN_MIGRATIONS is retained (not removed) so the BOOT_MODE-absent
        # legacy path in the entrypoint stays safe during a partial rollout.
        env_by_container = self._env_by_container("dev")
        self.assertEqual(env_by_container["ai-shipping-labs"]["RUN_MIGRATIONS"], "true")
        self.assertEqual(
            env_by_container["ai-shipping-labs-worker"]["RUN_MIGRATIONS"], "false"
        )

    def test_gunicorn_workers_two_for_dev(self):
        # Issue #1141 Phase 2C: dev runs 2 workers to ease 512 MB pressure.
        env_by_container = self._env_by_container("dev")
        for name, env in env_by_container.items():
            with self.subTest(container=name):
                self.assertEqual(env["GUNICORN_WORKERS"], "2")

    def test_gunicorn_workers_three_for_prod(self):
        web_env = self._env_by_container("prod", "web")
        worker_env = self._env_by_container("prod", "worker")
        self.assertEqual(web_env["ai-shipping-labs"]["GUNICORN_WORKERS"], "3")
        self.assertEqual(
            worker_env["ai-shipping-labs-worker"]["GUNICORN_WORKERS"], "3"
        )

    def test_web_role_strips_worker_sidecar(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(input_path)

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(
                    str(input_path),
                    "20260519-abcd",
                    str(output_path),
                    "prod",
                    "web",
                )

            task_def = self._read_task_definition(output_path)

        names = [c["name"] for c in task_def["containerDefinitions"]]
        self.assertEqual(names, ["ai-shipping-labs"])

    def test_worker_role_keeps_only_worker_container(self):
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            self._write_task_definition(input_path)

            with redirect_stdout(StringIO()):
                update_task_def.update_task_definition(
                    str(input_path),
                    "20260519-abcd",
                    str(output_path),
                    "prod",
                    "worker",
                )

            task_def = self._read_task_definition(output_path)

        names = [c["name"] for c in task_def["containerDefinitions"]]
        self.assertEqual(names, ["ai-shipping-labs-worker"])
        environment = {
            item["name"]: item["value"]
            for item in task_def["containerDefinitions"][0]["environment"]
        }
        self.assertEqual(environment["RUN_MIGRATIONS"], "false")
