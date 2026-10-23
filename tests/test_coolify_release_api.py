"""HTTP boundary tests for the fail-closed Coolify release helper."""

import io
import json
import tempfile
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from deploy.coolify.api import JsonClient, NoRedirectHandler, get_text
from deploy.coolify.deploy_exact_release import run, write_receipt_log, write_summary
from deploy.coolify.errors import ReleaseError
from tests.test_coolify_release_contracts import DIGEST, IMAGE, SHA, release_config

RELEASE_ENVIRONMENT = {
    "COOLIFY_URL": "https://coolify.169.58.119.163.sslip.io",
    "COOLIFY_TOKEN": "coolify",
    "COOLIFY_AISL_SERVICE_UUID": "mekt9kz74fvbixla6olinuma",
    "COOLIFY_AISL_WEB_APPLICATION_UUID": "w3mxfr6wzjnpg3hqaw3y0obq",
    "AISL_DEPLOY_VERIFICATION_API_TOKEN": "diagnostics",
}
SOURCE_RECEIPT = {
    "cutoff": "2026-10-23T14:00:00+00:00",
    "roles": [
        {"role": "web", "tag": SHA, "recorded_at": "2026-10-23T14:00:30+00:00"},
        {"role": "worker", "tag": SHA, "recorded_at": "2026-10-23T14:00:30+00:00"},
    ],
}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class RecordingOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def redirect_error(url):
    return urllib.error.HTTPError(
        url,
        302,
        "redirect",
        {"Location": "https://other.example/"},
        io.BytesIO(b"sensitive response"),
    )


class FakeDeployer:
    def __init__(self, *_args):
        pass

    def deploy(self, *_args):
        return SimpleNamespace(
            deployment_uuid="deploy-1",
            source_receipt=SOURCE_RECEIPT,
        )


def release_args():
    return SimpleNamespace(
        image=IMAGE,
        digest=DIGEST,
        sha=SHA,
        timeout_seconds=1,
        poll_seconds=1,
    )


def receipt_with_private_fields():
    receipt = {
        "cutoff": SOURCE_RECEIPT["cutoff"],
        "roles": [dict(item) for item in SOURCE_RECEIPT["roles"]],
        "private": "must-not-leak",
        "token": "must-not-leak",
    }
    receipt["roles"][0]["phases"] = {"total": 4.2, "private": "must-not-leak"}
    return receipt


@tag("core")
class ReleaseApiTest(SimpleTestCase):
    def test_default_client_installs_no_redirect_handler(self):
        client = JsonClient("https://example.test", "secret")

        handlers = client.opener.handlers
        self.assertTrue(any(isinstance(item, NoRedirectHandler) for item in handlers))

    def test_authentication_scheme_is_exact_and_explicit(self):
        cases = (("Bearer", "Bearer coolify"), ("Token", "Token diagnostics"))
        for scheme, expected in cases:
            opener = RecordingOpener(FakeResponse(b"{}"))
            client = JsonClient(
                "https://example.test",
                expected.split()[1],
                auth_scheme=scheme,
                opener=opener,
            )

            client.call("GET", "/resource")

            request = opener.requests[0][0]
            with self.subTest(scheme=scheme):
                self.assertEqual(request.get_header("Authorization"), expected)

    def test_json_client_rejects_redirect_without_response_body(self):
        opener = RecordingOpener(error=redirect_error("https://example.test/resource"))
        client = JsonClient("https://example.test", "secret", opener=opener)

        with self.assertRaisesMessage(ReleaseError, "status=302") as raised:
            client.call("GET", "/resource")

        self.assertNotIn("sensitive", str(raised.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_public_ping_rejects_redirect(self):
        opener = RecordingOpener(error=redirect_error("https://example.test/ping"))

        with self.assertRaisesMessage(ReleaseError, "public-ping-request-failed"):
            get_text("https://example.test/ping", opener=opener)

        self.assertEqual(len(opener.requests), 1)

    def test_entrypoint_uses_bearer_for_coolify_and_token_for_diagnostics(self):
        schemes = []

        def client_factory(_url, _token, auth_scheme="Bearer"):
            schemes.append(auth_scheme)
            return object()

        with (
            patch.dict("os.environ", RELEASE_ENVIRONMENT, clear=True),
            patch("deploy.coolify.deploy_exact_release.JsonClient", side_effect=client_factory),
            patch("deploy.coolify.deploy_exact_release.ReleaseDeployer", FakeDeployer),
            patch("deploy.coolify.deploy_exact_release.write_receipt_log") as receipt_log,
        ):
            run(release_args())

        self.assertEqual(schemes, ["Bearer", "Token"])
        receipt_log.assert_called_once_with(SOURCE_RECEIPT)

    def test_summary_allowlists_the_retained_source_proof(self):
        receipt = receipt_with_private_fields()
        result = SimpleNamespace(deployment_uuid="deploy-1", source_receipt=receipt)
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
                write_summary(release_config(), result)
            content = summary.read_text()

        self.assertIn(f"tag=`{SHA}`", content)
        self.assertIn("role=`web`", content)
        self.assertIn("role=`worker`", content)
        self.assertIn("Source boot cutoff: `2026-10-23T14:00:00+00:00`", content)
        self.assertEqual(
            content.count("recorded_at=`2026-10-23T14:00:30+00:00`"),
            2,
        )
        self.assertNotIn("phases", content)
        self.assertNotIn("total", content)
        self.assertNotIn("token", content)
        self.assertNotIn("must-not-leak", content)

    def test_stdout_receipt_is_compact_json_and_allowlisted(self):
        output = io.StringIO()
        with redirect_stdout(output):
            write_receipt_log(receipt_with_private_fields())

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        prefix, encoded = lines[0].split("=", 1)
        self.assertEqual(prefix, "COOLIFY_SOURCE_BOOT_RECEIPT")
        receipt = json.loads(encoded)
        self.assertEqual(receipt, SOURCE_RECEIPT)
        self.assertNotIn("must-not-leak", encoded)
