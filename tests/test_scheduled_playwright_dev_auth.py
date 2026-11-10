"""Contracts for authenticated scheduled browsing of the protected dev site."""

from pathlib import Path
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase

from playwright_tests import conftest as playwright_config

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/scheduled-playwright-dev.yml"
DEV_PASSWORD = "AISL_DEV_SITE_PASSWORD"


def _step(workflow, job, name):
    return next(
        step for step in workflow["jobs"][job]["steps"]
        if step.get("name") == name
    )


class _FakeContext:
    def __init__(self, browser, options):
        self.browser = browser
        self.options = options

    def new_page(self):
        return "authenticated-page"


class _FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.options = []

    def new_context(self, *args, **kwargs):
        self.options.append((args, kwargs))
        context = _FakeContext(self, kwargs)
        self.contexts.append(context)
        return context

    def new_page(self, **kwargs):
        return "unauthenticated-page"


class ScheduledPlaywrightDevWorkflowTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())

    def test_required_secret_authenticates_warmups_and_browser_suite(self):
        missing = _step(self.workflow, "dev-readiness", "Require dev-site access secret")
        ping = _step(self.workflow, "dev-readiness", "Wait for Contabo dev endpoint")
        warm = _step(self.workflow, "dev-readiness", "Warm dev application endpoints")
        suite = _step(self.workflow, "playwright-dev", "Run Playwright dev shard")

        self.assertEqual(missing["env"][DEV_PASSWORD], "${{ secrets.AISL_DEV_SITE_PASSWORD }}")
        self.assertEqual(
            self.workflow["env"]["AISL_DEV_SITE_USERNAME"],
            playwright_config._DEV_SITE_AUTH_USERNAME,
        )
        self.assertIn('if [ -z "${AISL_DEV_SITE_PASSWORD}" ]', missing["run"])
        self.assertIn(DEV_PASSWORD, missing["run"])
        self.assertNotIn(DEV_PASSWORD, ping.get("env", {}))
        self.assertNotIn("--user", ping["run"])
        self.assertIn("--user", warm["run"])
        self.assertEqual(warm["env"][DEV_PASSWORD], "${{ secrets.AISL_DEV_SITE_PASSWORD }}")
        self.assertEqual(suite["env"][DEV_PASSWORD], "${{ secrets.AISL_DEV_SITE_PASSWORD }}")

    def test_remote_contexts_add_auth_without_replacing_context_options(self):
        browser = _FakeBrowser()
        with (
            patch.object(playwright_config, "_resolved_base_url", return_value="https://dev.aishippinglabs.com"),
            patch.object(playwright_config, "_base_url_is_local", return_value=False),
            patch.dict("os.environ", {DEV_PASSWORD: "test-only-password"}),
        ):
            playwright_config._add_remote_dev_auth(browser)
            context = browser.new_context(viewport={"width": 800, "height": 600}, locale="en-GB")
            page = browser.new_page(viewport={"width": 400, "height": 700})

        credentials = context.options["http_credentials"]
        self.assertEqual(credentials["username"], "aisl-scheduled")
        self.assertEqual(credentials["origin"], "https://dev.aishippinglabs.com")
        self.assertEqual(credentials["send"], "always")
        self.assertEqual(context.options["viewport"], {"width": 800, "height": 600})
        self.assertEqual(context.options["locale"], "en-GB")
        self.assertEqual(page, "authenticated-page")
        self.assertEqual(browser.options[-1][1]["viewport"], {"width": 400, "height": 700})

    def test_remote_context_fails_clearly_when_secret_is_missing(self):
        with (
            patch.object(playwright_config, "_resolved_base_url", return_value="https://dev.aishippinglabs.com"),
            patch.object(playwright_config, "_base_url_is_local", return_value=False),
            patch.dict("os.environ", {}, clear=True),
        ):
            with self.assertRaisesRegex(RuntimeError, DEV_PASSWORD):
                playwright_config._add_remote_dev_auth(_FakeBrowser())

    def test_other_remote_hosts_do_not_receive_the_dev_credential(self):
        browser = _FakeBrowser()
        with (
            patch.object(playwright_config, "_resolved_base_url", return_value="https://aishippinglabs.com"),
            patch.object(playwright_config, "_base_url_is_local", return_value=False),
            patch.dict("os.environ", {}, clear=True),
        ):
            playwright_config._add_remote_dev_auth(browser)
            context = browser.new_context(locale="en-GB")

        self.assertNotIn("http_credentials", context.options)
        self.assertEqual(context.options["locale"], "en-GB")
