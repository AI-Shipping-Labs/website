"""Execute the full release gate with independently failing DEV responses."""

from unittest.mock import patch

from django.test import SimpleTestCase, tag

from deploy.coolify.api import get_text
from deploy.coolify.errors import ReleaseError
from deploy.coolify.orchestrator import ReleaseDeployer
from tests.test_coolify_release_api import RecordingOpener, redirect_error
from tests.test_coolify_release_contracts import SHA, release_config
from tests.test_coolify_release_orchestrator import FakeCoolify, FakeDiagnostics

DEV_PING = "https://dev.aishippinglabs.com/ping"
PROD_PING = "https://aishippinglabs.com/ping"


class ProbeClock:
    def __init__(self):
        self.now = 0

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def deploy_with_reader(reader):
    deployer = ReleaseDeployer(release_config(), FakeCoolify(), FakeDiagnostics(), reader)
    clock = ProbeClock()
    with (
        patch("deploy.coolify.verification.time.monotonic", clock.read),
        patch("deploy.coolify.verification.time.sleep", clock.sleep),
    ):
        return deployer.deploy(timeout=5, interval=1)


@tag("core")
class DevReleaseReadinessTest(SimpleTestCase):
    def test_both_endpoints_must_serve_the_exact_release_three_times(self):
        calls = []

        def reader(url):
            calls.append(url)
            return SHA

        result = deploy_with_reader(reader)

        self.assertEqual(result.deployment_uuid, "deploy-1")
        self.assertEqual(calls, [PROD_PING, DEV_PING] * 3)

    def test_production_success_cannot_hide_stale_or_empty_dev(self):
        for dev_body in ("stale", ""):
            calls = []

            def reader(url):
                calls.append(url)
                if url == DEV_PING:
                    return dev_body
                return SHA

            with self.subTest(body=dev_body), self.assertRaisesMessage(
                ReleaseError, "public-release-not-consecutive"
            ):
                deploy_with_reader(reader)
            self.assertIn(DEV_PING, calls)

    def test_unavailable_dev_fails_deployment_with_endpoint_identity(self):
        def reader(url):
            if url == DEV_PING:
                raise ReleaseError("public-ping-request-failed")
            return SHA

        with self.assertRaisesMessage(ReleaseError, "endpoint=dev"):
            deploy_with_reader(reader)

    def test_dev_redirect_cannot_pass_via_a_healthy_production_response(self):
        opener = RecordingOpener(error=redirect_error(DEV_PING))

        def reader(url):
            if url == DEV_PING:
                return get_text(url, opener=opener)
            return SHA

        with self.assertRaisesMessage(ReleaseError, "endpoint=dev"):
            deploy_with_reader(reader)
        self.assertEqual(len(opener.requests), 1)

    def test_a_stale_dev_sample_resets_the_joint_consecutive_count(self):
        dev_samples = iter((SHA, "stale", SHA, SHA, SHA))
        calls = []

        def reader(url):
            calls.append(url)
            if url == DEV_PING:
                return next(dev_samples)
            return SHA

        deploy_with_reader(reader)

        self.assertEqual(calls, [PROD_PING, DEV_PING] * 5)
