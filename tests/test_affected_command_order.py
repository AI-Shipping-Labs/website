from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.affected_command_order import CHECK_TAILWIND_COMMAND, CSS_BUILD_COMMAND
from scripts.affected_tests import Plan, build_plan, render_text, run_commands

COMPOSED_TOOLING_PATHS = [
    "_docs/testing-operations.md",
    "scripts/affected_command_order.py",
    "scripts/affected_tests.py",
    "scripts/tailwind_producers.py",
    "scripts/verify_tailwind_build.py",
    "tests/test_affected_command_order.py",
    "tests/test_affected_tests.py",
    "tests/test_tailwind_producer_discovery.py",
]


class AffectedCommandOrderTest(SimpleTestCase):
    def test_composed_tooling_plan_builds_and_checks_css_before_django(self):
        plan = build_plan(COMPOSED_TOOLING_PATHS, expander=lambda modules: {})

        self.assertEqual(plan.commands()[0], CHECK_TAILWIND_COMMAND)
        self.assertTrue(plan.commands()[1].startswith("uv run python manage.py test "))
        self.assertEqual(plan.commands()[-1], "make test-playwright-core")
        self.assertEqual(plan.commands().count(CHECK_TAILWIND_COMMAND), 1)
        self.assertNotIn(CSS_BUILD_COMMAND, plan.commands())

    def test_bundle_contract_without_producer_gate_builds_css_first(self):
        plan = build_plan(["tests/test_tailwind_build.py"], expander=lambda modules: {})

        self.assertEqual(plan.commands()[0], CSS_BUILD_COMMAND)
        self.assertTrue(plan.commands()[1].startswith("uv run python manage.py test "))
        self.assertNotIn(CHECK_TAILWIND_COMMAND, plan.commands())

    def test_source_scan_only_tailwind_selection_stays_build_free(self):
        plan = build_plan(["events/views/detail.py"], expander=lambda modules: {})

        self.assertIn("tests.test_tailwind_build.TailwindSourceScanTest", plan.django_labels)
        self.assertNotIn(CSS_BUILD_COMMAND, plan.commands())
        self.assertNotIn(CHECK_TAILWIND_COMMAND, plan.commands())

    def test_parent_and_descendant_labels_select_the_bundle_contract(self):
        labels = [
            "tests",
            "tests.test_tailwind_build",
            "tests.test_tailwind_build.TailwindSourceContractTest",
            "tests.test_tailwind_build.TailwindSourceContractTest.test_compiled_bundle_exists",
        ]
        for label in labels:
            plan = Plan(base="origin/main", django_labels=[label], django_command="django", playwright="none")
            self.assertEqual(plan.commands(), [CSS_BUILD_COMMAND, "django"])

    def test_source_scan_sibling_does_not_select_bundle_contract(self):
        plan = Plan(
            base="origin/main",
            django_labels=["tests.test_tailwind_build.TailwindSourceScanTest"],
            django_command="django",
            playwright="none",
        )
        self.assertEqual(plan.commands(), ["django"])

    def test_non_bundle_plan_preserves_supplemental_order(self):
        plan = Plan(
            base="origin/main",
            django_labels=["events"],
            django_command="django",
            extra_commands=["echo first", CHECK_TAILWIND_COMMAND, "echo last"],
            playwright="none",
        )
        self.assertEqual(plan.commands(), ["django", "echo first", CHECK_TAILWIND_COMMAND, "echo last"])

    def test_text_json_and_execution_use_one_order_and_worst_exit(self):
        plan = build_plan(COMPOSED_TOOLING_PATHS, expander=lambda modules: {})
        expected = plan.commands()
        text_commands = render_text(plan, "0123456789abcdef").split("Commands:\n", 1)[1].splitlines()
        text_commands = [line.removeprefix("  ") for line in text_commands]

        with patch("scripts.affected_tests.subprocess.run") as run:
            run.side_effect = [
                SimpleNamespace(returncode=1),
                SimpleNamespace(returncode=3),
                SimpleNamespace(returncode=2),
            ]
            exit_code = run_commands(plan)

        executed = [call.args[0] for call in run.call_args_list]
        self.assertEqual(text_commands, expected)
        self.assertEqual(plan.to_dict()["commands"], expected)
        self.assertEqual(executed, expected)
        self.assertEqual(exit_code, 3)
