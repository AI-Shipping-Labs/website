"""Compose vs ECS container-dispatch smoke tests (issue #1510).

``entrypoint.sh`` must exec arguments when Compose supplies a ``command:``,
and must still start ``scripts.entrypoint_init`` when argv is empty (ECS).
These tests run in CI without a Docker daemon.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
ENTRYPOINT_INIT = REPO_ROOT / "scripts" / "entrypoint_init.py"

SETUP_COMMAND = (
    'sh -c "uv run python manage.py migrate && '
    "uv run python manage.py seed_data && "
    "uv run python manage.py seed_content_sources && "
    'uv run python manage.py sync_content --from-disk _content-repo"'
)
WEB_COMMAND = (
    'sh -c "uv run python manage.py migrate && '
    "uv run python manage.py collectstatic --noinput && "
    'uv run gunicorn website.wsgi:application --bind 0.0.0.0:8000 --workers 3"'
)
WORKER_COMMAND = "uv run python manage.py qcluster"
WATCHER_COMMAND = "uv run python manage.py watch_content _content-repo"


def _folded(value):
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return " ".join(str(value).split())


class EntrypointArgvDispatchTest(SimpleTestCase):
    def _run_entrypoint(self, args, *, env, cwd=None):
        return subprocess.run(
            ["sh", str(ENTRYPOINT), *args],
            cwd=cwd or REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_arguments_are_execd_and_skip_entrypoint_init(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            record = tmp_path / "uv-args"
            uv = tmp_path / "uv"
            uv.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > '{record}'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}

            completed = self._run_entrypoint(["printf", "compose-ok\n"], env=env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "compose-ok\n")
            self.assertFalse(record.exists())

    def test_python_c_arguments_run_without_entrypoint_init(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            marker = tmp_path / "ran"
            record = tmp_path / "uv-args"
            uv = tmp_path / "uv"
            uv.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > '{record}'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}
            payload = f"from pathlib import Path; Path({str(marker)!r}).write_text('ok')"

            completed = self._run_entrypoint([sys.executable, "-c", payload], env=env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "ok")
            self.assertFalse(record.exists())

    def test_empty_argv_invokes_entrypoint_init_via_uv(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            record = tmp_path / "uv-args"
            uv = tmp_path / "uv"
            uv.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > '{record}'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}

            completed = self._run_entrypoint([], env=env)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                record.read_text(encoding="utf-8").split(),
                ["run", "python", "-m", "scripts.entrypoint_init"],
            )


class ComposeCommandContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
        cls.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        cls.entrypoint_init = ENTRYPOINT_INIT.read_text(encoding="utf-8")

    def test_setup_web_worker_watcher_keep_intended_commands(self):
        services = self.compose["services"]
        self.assertEqual(_folded(services["setup"]["command"]), SETUP_COMMAND)
        self.assertEqual(_folded(services["web"]["command"]), WEB_COMMAND)
        self.assertEqual(_folded(services["worker"]["command"]), WORKER_COMMAND)
        self.assertEqual(_folded(services["watcher"]["command"]), WATCHER_COMMAND)

    def test_setup_is_a_one_shot_profile_and_db_is_unchanged(self):
        services = self.compose["services"]
        self.assertEqual(services["setup"].get("profiles"), ["setup"])
        self.assertEqual(services["db"]["image"], "postgres:17")
        self.assertNotIn("command", services["db"])

    def test_compose_app_services_do_not_set_boot_mode_or_run_migrations(self):
        for name in ("setup", "web", "worker", "watcher"):
            environment = self.compose["services"][name].get("environment") or {}
            self.assertNotIn("BOOT_MODE", environment, name)
            self.assertNotIn("RUN_MIGRATIONS", environment, name)

    def test_dockerfile_entrypoint_has_no_cmd(self):
        self.assertIn('ENTRYPOINT ["/app/entrypoint.sh"]', self.dockerfile)
        cmd_lines = [
            line
            for line in self.dockerfile.splitlines()
            if line.startswith("CMD ") or line.startswith("CMD[")
        ]
        self.assertEqual(cmd_lines, [])

    def test_comments_no_longer_claim_entrypoint_ignores_arguments(self):
        self.assertNotIn('does not consume "$@"', self.dockerfile)
        self.assertNotIn("does NOT consume", self.entrypoint_init)
        self.assertNotIn("does not consume", self.entrypoint_init)
