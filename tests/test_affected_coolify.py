"""Keep the DEV repair's affected-test exception narrow and composable."""

from django.test import SimpleTestCase, tag

from scripts.affected_coolify import DEV_REPAIR_PATHS, FOCUSED_COOLIFY_CONTRACT_PATHS, RELEASE_TESTS
from scripts.affected_tests import FOCUSED_CONTRACT_PATHS, build_plan, render_text


def plan_for(paths):
    return build_plan(paths, base="test", expander=lambda _modules: {})


@tag("core")
class CoolifyAffectedTestsTest(SimpleTestCase):
    def test_deployment_sources_select_their_offline_owners(self):
        self.assertTrue(set(FOCUSED_COOLIFY_CONTRACT_PATHS).issubset(FOCUSED_CONTRACT_PATHS))
        for path, owners in FOCUSED_COOLIFY_CONTRACT_PATHS:
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertTrue(set(owners).issubset(plan.django_labels))
                self.assertNotIn("tests", plan.django_labels)
                self.assertEqual(plan.unmapped, [])

    def test_the_complete_dev_repair_uses_offline_tests_without_local_browser(self):
        plan = plan_for([*DEV_REPAIR_PATHS, "_docs/audits/2026-10-24-dev-route.md"])

        self.assertEqual(plan.playwright, "none")
        self.assertIn("Playwright: none", render_text(plan, "test"))
        self.assertTrue(set(RELEASE_TESTS).issubset(plan.django_labels))
        self.assertIn("tests.test_coolify_dev_route", plan.django_labels)
        self.assertEqual(plan.unmapped, [])
        self.assertFalse(any("test-playwright" in command for command in plan.commands()))

    def test_unreviewed_infrastructure_paths_keep_core_browser_selection(self):
        paths = ("deploy/coolify/api.py", ".github/workflows/deploy-dev.yml", "Dockerfile")
        for path in paths:
            with self.subTest(path=path):
                plan = plan_for(["deploy/coolify/dev-route.yml", path])
                self.assertEqual(plan.playwright, "core")

    def test_mixed_app_or_shared_template_diffs_retain_browser_coverage(self):
        cases = (("events/views/detail.py", "core"), ("templates/base.html", "full"))
        for path, expected in cases:
            with self.subTest(path=path):
                plan = plan_for([*DEV_REPAIR_PATHS, path])
                self.assertEqual(plan.playwright, expected)
