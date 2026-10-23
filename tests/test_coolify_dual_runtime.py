"""Offline operational checks: inventory, own-worker health and safe output."""

import copy
import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from deploy.coolify import dual_runtime_snapshot as collector
from deploy.coolify import verify_dual_runtime as verifier
from scripts.affected_tests import build_plan

SHA = "a" * 40
IMAGE = f"{verifier.IMAGE_REPOSITORY}:deploy-123-1-{SHA}"
NOW = datetime(2026, 10, 23, 14, 40, tzinfo=UTC)


def container(role):
    environment = {"RUN_MIGRATIONS": "true"}
    if role == "worker":
        environment = {"BOOT_MODE": "worker", "RUN_MIGRATIONS": "false"}
    return {
        "id": (role + "1" * 64)[:64], "name": f"{role}-{verifier.SOURCE_UUID}",
        "image": IMAGE, "image_id": "sha256:" + "b" * 64,
        "running": True, "status": "running", "health": "healthy",
        "started_at": (NOW - timedelta(minutes=10)).isoformat(),
        "last_healthcheck": {"exit_code": 0, "end": (NOW - timedelta(seconds=3)).isoformat()},
        "environment": environment, "networks": [verifier.SOURCE_UUID, "coolify"],
        "worker_heartbeat_probe": role == "worker",
    }


def snapshot():
    return {"generated_at": NOW.isoformat(), "containers": [container("web"), container("worker")]}


class RuntimeInventoryTest(SimpleTestCase):
    def test_helper_changes_select_their_runtime_contract_tests(self):
        for name in ("dual_runtime_snapshot.py", "verify_dual_runtime.py"):
            with self.subTest(name=name):
                plan = build_plan(["deploy/coolify/" + name], expander=lambda modules: {})
                self.assertIn("tests.test_coolify_dual_runtime", plan.django_labels)
                self.assertNotIn("tests", plan.django_labels)
                self.assertEqual(plan.playwright, "core")

    def test_healthy_pair_reports_same_exact_release_and_source_boots(self):
        result = verifier.verify_snapshot(snapshot(), IMAGE)
        self.assertEqual(result["image"], IMAGE)
        self.assertEqual(result["image_id"], "sha256:" + "b" * 64)
        self.assertEqual(set(result["roles"]), {"web", "worker"})
        self.assertEqual(result["roles"]["worker"]["started_at"], container("worker")["started_at"])

    def test_missing_duplicate_and_stopped_orphan_inventory_is_rejected(self):
        inventories = [[], [container("web")], [container("web"), container("web")]]
        orphan = container("worker")
        orphan.update(name=f"old-worker-{verifier.SOURCE_UUID}", running=False, status="exited")
        inventories.append([container("web"), container("worker"), orphan])
        for inventory in inventories:
            with self.subTest(inventory=[item["name"] for item in inventory]):
                with self.assertRaisesRegex(verifier.VerificationError, "inventory"):
                    verifier.verify_snapshot({"generated_at": NOW.isoformat(), "containers": inventory})

    def test_created_unhealthy_and_missing_health_evidence_fail(self):
        changes = [
            {"running": False, "status": "created"}, {"health": "unhealthy"},
            {"last_healthcheck": {}}, {"last_healthcheck": {"exit_code": 1}},
            {"worker_heartbeat_probe": False},
        ]
        for change in changes:
            with self.subTest(change=change):
                data = snapshot()
                data["containers"][1].update(change)
                with self.assertRaises(verifier.VerificationError):
                    verifier.verify_snapshot(data)

    def test_stale_future_preboot_and_naive_health_times_fail(self):
        ends = [NOW - timedelta(seconds=91), NOW + timedelta(seconds=10), NOW - timedelta(hours=1)]
        times = [value.isoformat() for value in ends]
        times.extend(["2026-10-23T14:40:00", "invalid", None])
        for ended in times:
            with self.subTest(ended=ended):
                data = snapshot()
                data["containers"][1]["last_healthcheck"]["end"] = ended
                with self.assertRaises(verifier.VerificationError):
                    verifier.verify_snapshot(data)

    def test_distinct_image_tag_or_actual_config_image_id_fails(self):
        for key, value in [("image", IMAGE.replace("123", "124")), ("image_id", "sha256:" + "c" * 64)]:
            data = snapshot()
            data["containers"][1][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(verifier.VerificationError, "exact image"):
                verifier.verify_snapshot(data)
        with self.assertRaisesRegex(verifier.VerificationError, "expected release"):
            verifier.verify_snapshot(snapshot(), IMAGE.replace("123", "124"))

    def test_migration_role_and_creation_network_drift_fail(self):
        changes = [
            (0, "environment", {"BOOT_MODE": "worker", "RUN_MIGRATIONS": "true"}),
            (0, "environment", {"BOOT_MODE": "web", "RUN_MIGRATIONS": "false"}),
            (1, "environment", {"RUN_MIGRATIONS": "false"}),
            (1, "environment", {"BOOT_MODE": "worker", "RUN_MIGRATIONS": "true"}),
            (0, "networks", [verifier.SOURCE_UUID]), (1, "networks", ["coolify"]),
        ]
        for index, key, value in changes:
            with self.subTest(index=index, key=key, value=value):
                data = snapshot()
                data["containers"][index][key] = value
                with self.assertRaises(verifier.VerificationError):
                    verifier.verify_snapshot(data)

    def test_immutable_tags_allow_only_dated_baseline_and_authoritative_release_format(self):
        old = verifier.IMAGE_REPOSITORY + ":20261023-140349-7b74b51"
        self.assertEqual(verifier.immutable_image(old), old)
        for tag in ["latest", "main", SHA, f"prefix-{SHA}-suffix", "deploy-1-1-" + "a" * 39]:
            with self.subTest(tag=tag), self.assertRaises(verifier.VerificationError):
                verifier.immutable_image(verifier.IMAGE_REPOSITORY + ":" + tag)


class RemoteCollectionTest(SimpleTestCase):
    @patch.object(collector, "sanitized_container", return_value={"id": "safe-id"})
    @patch.object(collector.subprocess, "run")
    def test_collection_only_lists_and_inspects_containers(self, run, sanitize):
        run.side_effect = [
            Mock(stdout="source-id\n"), Mock(stdout="source-id\n"),
            Mock(stdout=json.dumps([{"unapproved": "private-secret"}])),
        ]
        result = collector.collect(verifier.SOURCE_UUID)
        self.assertEqual(result["containers"], [{"id": "safe-id"}])
        sanitize.assert_called_once_with({"unapproved": "private-secret"})
        self.assertEqual(len(run.call_args_list), 3)
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[0], "docker")
            self.assertIn(command[1], ("ps", "inspect"))
            self.assertTrue(call.kwargs["capture_output"])
        self.assertNotIn("private-secret", json.dumps(result))

    @patch.object(collector, "docker")
    def test_inventory_includes_stopped_and_renamed_project_owned_containers(self, docker):
        docker.side_effect = ["web-id\nworker-id\n", "worker-id\norphan-id\n"]
        self.assertEqual(collector.source_ids(verifier.SOURCE_UUID), ["orphan-id", "web-id", "worker-id"])
        for call in docker.call_args_list:
            self.assertEqual(call.args[:2], ("ps", "-aq"))
        self.assertIn(f"label=com.docker.compose.project={verifier.SOURCE_UUID}", docker.call_args_list[1].args)

    def test_collector_omits_secret_environment_and_healthcheck_output(self):
        raw = {
            "Id": "container", "Name": "/worker-source", "Image": "sha256:config",
            "Config": {"Image": IMAGE, "Env": ["SECRET_KEY=private-secret", "BOOT_MODE=worker"],
                       "Healthcheck": {"Test": ["CMD", "/app/.venv/bin/python", "-c", collector.WORKER_PROBE]}},
            "State": {"Health": {"Status": "healthy", "Log": [{"End": NOW.isoformat(), "ExitCode": 0,
                                                                        "Output": "private-secret"}]}},
            "NetworkSettings": {"Networks": {"coolify": {"Credentials": "private-secret"}}},
        }
        result = collector.sanitized_container(raw)
        self.assertEqual(result["environment"], {"BOOT_MODE": "worker"})
        self.assertEqual(result["last_healthcheck"], {"end": NOW.isoformat(), "exit_code": 0})
        self.assertTrue(result["worker_heartbeat_probe"])
        self.assertNotIn("private-secret", json.dumps(result))

    @patch.object(verifier.subprocess, "run")
    def test_remote_failure_never_surfaces_raw_stderr(self, run):
        run.return_value = Mock(returncode=1, stdout="private-secret", stderr="private-secret")
        with self.assertRaisesRegex(verifier.VerificationError, "remote Docker snapshot failed") as error:
            verifier.remote_snapshot("contabo-b", "ssh-config", "identity")
        self.assertNotIn("private-secret", str(error.exception))
        self.assertTrue(run.call_args.kwargs["capture_output"])


class PublicAndWindowTest(SimpleTestCase):
    @patch.object(verifier.urllib.request, "urlopen")
    def test_public_exact_sha_and_baseline_unverified_are_distinguished(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status, response.url = 200, verifier.PUBLIC_PING
        response.read.return_value = SHA.encode()
        self.assertEqual(verifier.public_ping(SHA), {"status": 200, "exact_sha_verified": True})
        response.read.return_value = b"N/A"
        self.assertEqual(verifier.public_ping(), {"status": 200, "exact_sha_verified": False})
        with self.assertRaisesRegex(verifier.VerificationError, "full release SHA"):
            verifier.public_ping(SHA)

    @patch.object(verifier.urllib.request, "urlopen")
    def test_redirect_or_non_200_is_rejected(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        for status, url in [(503, verifier.PUBLIC_PING), (200, "https://example.test/")]:
            response.status, response.url = status, url
            with self.subTest(status=status, url=url), self.assertRaises(verifier.VerificationError):
                verifier.public_ping()

    @patch.object(verifier, "public_ping", return_value={"status": 200})
    @patch.object(verifier, "remote_snapshot")
    @patch.object(verifier, "arguments")
    def test_worker_loss_after_public_success_prevents_healthy_verdict(self, arguments, remote, _ping):
        arguments.return_value = Mock(expected_image=IMAGE, expected_sha=SHA)
        changed = copy.deepcopy(snapshot())
        changed["containers"][1]["health"] = "unhealthy"
        remote.side_effect = [snapshot(), changed]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(verifier.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    @patch.object(verifier, "public_ping", return_value={"status": 200})
    @patch.object(verifier, "remote_snapshot")
    @patch.object(verifier, "arguments")
    def test_replacement_during_snapshot_window_requires_retry(self, arguments, remote, _ping):
        arguments.return_value = Mock(expected_image=IMAGE, expected_sha=SHA)
        changed = snapshot()
        changed["containers"][1]["id"] = "replacement-container"
        remote.side_effect = [snapshot(), changed]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(verifier.main(), 1)
        self.assertIn("changed during", json.loads(output.getvalue())["error"])


class CommandOutputTest(SimpleTestCase):
    @patch.object(verifier, "remote_snapshot", return_value=["private-secret"])
    @patch.object(verifier, "arguments")
    def test_malformed_remote_snapshot_fails_without_raw_payload(self, arguments, _remote):
        arguments.return_value = Mock(expected_image=IMAGE, expected_sha=SHA)
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(verifier.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")
        self.assertNotIn("private-secret", output.getvalue() + errors.getvalue())


    def test_cli_rejects_inconsistent_source_image_and_public_revision(self):
        argv = ["verify", "--expected-image", IMAGE, "--expected-sha", "c" * 40]
        with patch.object(verifier.sys, "argv", argv):
            with self.assertRaisesRegex(verifier.VerificationError, "different releases"):
                verifier.arguments()


    @patch.object(verifier, "public_ping", return_value={"status": 200, "exact_sha_verified": True})
    @patch.object(verifier, "remote_snapshot")
    @patch.object(verifier, "arguments")
    def test_success_only_emits_the_approved_release_summary(self, arguments, remote, _ping):
        arguments.return_value = Mock(expected_image=IMAGE, expected_sha=SHA)
        data = snapshot()
        data["raw_inspect"] = "private-secret"
        data["containers"][1]["environment"]["SECRET_KEY"] = "private-secret"
        data["containers"][1]["last_healthcheck"]["output"] = "private-secret"
        remote.return_value = data
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(verifier.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "healthy-snapshot")
        self.assertTrue(result["release_verified"])
        self.assertEqual(result["public_ping"], {"status": 200, "exact_sha_verified": True})
        self.assertNotIn("private-secret", output.getvalue())
        self.assertNotIn("environment", output.getvalue())

    @patch.object(verifier, "remote_snapshot")
    @patch.object(verifier, "arguments")
    def test_remote_subprocess_failure_never_emits_stdout_or_stderr(self, arguments, remote):
        arguments.return_value = Mock(expected_image=IMAGE, expected_sha=SHA)
        remote.side_effect = subprocess.CalledProcessError(
            1, "private-secret", output="private-secret", stderr="private-secret",
        )
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(verifier.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")
        self.assertNotIn("private-secret", output.getvalue() + errors.getvalue())
