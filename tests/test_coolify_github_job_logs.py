"""Exercise real Requests redirect/authentication behavior for job receipts."""

from unittest.mock import patch

import requests
from django.test import SimpleTestCase, tag

from deploy.coolify import github_job_logs, production_identity
from deploy.coolify.api import JsonClient
from deploy.coolify.errors import ReleaseError
from tests.test_coolify_release_contracts import DIGEST, SHA

ENDPOINT = "https://api.github.com/repos/AI-Shipping-Labs/website/actions/jobs/3/logs"
RECEIPT = f"Verified exact-head Coolify release sha={SHA} digest={DIGEST} deployment=release-1"


class LogAdapter(requests.adapters.BaseAdapter):
    def __init__(self, entries):
        self.entries = iter(entries)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append((request, kwargs))
        entry = next(self.entries)
        if isinstance(entry, Exception):
            raise entry
        status, body, location = entry
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = status
        response._content = body.encode()
        response.encoding = "utf-8"
        if location:
            response.headers["Location"] = location
        return response

    def close(self):
        pass


@tag("core")
class GitHubJobLogsTest(SimpleTestCase):
    def setUp(self):
        network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.github = JsonClient("https://api.github.com", "fixture-github-token")

    def _read(self, entries):
        adapter = LogAdapter(entries)
        session = github_job_logs.GitHubLogSession()
        session.mount("https://", adapter)
        with patch.object(github_job_logs, "GitHubLogSession", return_value=session):
            digest = production_identity.recorded_digest(self.github, 3, SHA)
        return digest, adapter.requests

    def test_direct_receipt_uses_existing_github_identity_and_timeout(self):
        digest, calls = self._read([(200, RECEIPT, None)])
        self.assertEqual(digest, DIGEST)
        self.assertEqual(len(calls), 1)
        request, options = calls[0]
        self.assertEqual((request.method, request.url), ("GET", ENDPOINT))
        self.assertEqual(request.headers["Authorization"], "Bearer fixture-github-token")
        self.assertEqual(options["timeout"], self.github.timeout)

    def test_native_redirect_chain_keeps_auth_only_on_original_origin(self):
        digest, calls = self._read([
            (302, "private-body", "/same-origin"),
            (307, "private-body", "https://signed.example/receipt"),
            (302, "private-body", "/final-receipt"),
            (200, RECEIPT, None),
        ])
        self.assertEqual(digest, DIGEST)
        self.assertEqual([call[0].url for call in calls], [
            ENDPOINT, "https://api.github.com/same-origin",
            "https://signed.example/receipt", "https://signed.example/final-receipt",
        ])
        self.assertEqual(["Authorization" in call[0].headers for call in calls], [True, True, False, False])

    def test_http_failures_expose_only_fixed_operation_and_integer_status(self):
        for status in (403, 404, 500, 503):
            with self.subTest(status=status):
                with self.assertRaises(ReleaseError) as raised:
                    self._read([(status, "private-token private-body", None)])
                self.assertEqual(str(raised.exception), f"release-evidence-dev-job-logs-failed category=http status={status}")

    def test_transport_and_timeout_failures_redact_external_diagnostics(self):
        for error, category in [
            (requests.ConnectionError("private-url private-token"), "transport"),
            (requests.Timeout("private-url private-token"), "timeout"),
        ]:
            with self.subTest(category=category):
                with self.assertRaises(ReleaseError) as raised:
                    self._read([error])
                self.assertEqual(str(raised.exception), f"release-evidence-dev-job-logs-failed category={category}")

    def test_missing_duplicate_or_wrong_revision_http_receipts_fail_closed(self):
        for body in ("private-body", RECEIPT + RECEIPT, RECEIPT.replace(SHA, "b" * 40)):
            with self.subTest(body=body):
                with self.assertRaisesMessage(ReleaseError, "dev-deployment-digest-receipt-missing"):
                    self._read([(200, body, None)])

    def test_unsafe_redirect_is_rejected_before_destination_request(self):
        with self.assertRaisesMessage(ReleaseError, "category=unsafe-redirect"):
            self._read([(302, "private-body", "http://signed.example/private-token")])
