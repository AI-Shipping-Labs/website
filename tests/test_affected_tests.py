"""Self-tests for ``scripts/affected_tests.py`` (issue #1468).

The helper decides which tests an agent runs locally, so a silent regression
here would either re-create the 17,200-test local full-suite run it exists to
prevent, or -- worse -- silently drop the tests that cover a change. These
tests pin the ordered rule chain, the curated hub map, the reverse-import
expansion and its cap, the escalation table, and the fail-closed behaviour for
unmapped paths.

They also act as rot guards: the curated map keys must still exist on disk,
``APP_LABELS`` must still match ``INSTALLED_APPS``, and the agent definitions
must still point at ``make test-affected`` rather than the full local suite.
"""

import ast
import contextlib
import dataclasses
import fnmatch
import functools
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase, tag

# The checkers rule 14 selects. Imported at module scope on purpose: the
# evidence tests below drive the declared scan sets off these modules' own
# enumerators and predicates, which is what stops a row from being narrower
# than the checker it stands for.
from content.tests import test_design_system_lint, test_template_comment_lint
from email_app.tests import test_email_service_shim_1651
from jobs.tests import test_async_task_names
from scripts import verify_tailwind_build
from scripts.affected_tests import (
    APP_LABELS,
    CHECK_TAILWIND_COMMAND,
    CONTRACT_PATHS,
    CORE_COMMAND,
    DJANGO_COMMAND_SUFFIX,
    ESCALATION_TRIGGERS,
    FOCUSED_CONTRACT_PATHS,
    HUB_MODULE_MAP,
    PLAYWRIGHT_CORE_COMMAND,
    PLAYWRIGHT_FULL_COMMAND,
    REPO_WIDE_GUARDS,
    REPO_WIDE_TEMPLATE_LINT_LABELS,
    REVERSE_IMPORT_APP_CAP,
    TAILWIND_PRODUCER_GLOBS,
    TEST_TREE_CONTRACTS,
    TESTS_PACKAGE,
    Plan,
    _parent_reexports,
    build_arg_parser,
    build_plan,
    git_grep_references,
    is_no_test_path,
    producer_globs,
    repo_wide_guard_labels,
    reverse_import_patterns,
    run_commands,
    test_tree_contract_labels,
)
from scripts.verify_tailwind_build import PRODUCER_FILES
from studio.tests import test_admin_links
from tests import test_tailwind_build
from tests.source_scan_policy import SOURCE_SCAN_EXCLUDED_PARTS, excluded_path_globs

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Real ``git grep`` output for ``integrations/services/zoom.py`` at the time
#: this test was written. Used to pin the label mapping without making the
#: assertion depend on the current consumer set (the live grep is exercised
#: separately in ``ReverseImportGrepTest``).
ZOOM_REFERENCES = [
    "api/tests/test_event_zoom_sync.py",
    "api/views/events.py",
    "events/services/zoom_lifecycle.py",
    "events/tests/test_zoom_lifecycle.py",
    "integrations/tests/test_zoom.py",
    "integrations/views/zoom_webhook.py",
    "jobs/tasks/recording_upload.py",
    "playwright_tests/test_studio_series_create_zoom_859.py",
    "studio/views/events.py",
]

LEXICAL_TEST_TREE_POLICY_LABELS = [
    "tests.test_assert_called_once_ratchet",
    "tests.test_layout_assertion_ratchet",
    "tests.test_source_inspection_ratchet",
    "tests.test_status_200_assertion_ratchet",
]
PLAYWRIGHT_TEST_TREE_POLICY_LABELS = sorted(
    [
        *LEXICAL_TEST_TREE_POLICY_LABELS,
        "tests.test_playwright_owner_inventory",
        "tests.test_browser_journey_policy",
        "tests.test_conftest_port_resolution",
        "tests.test_date_rot_guard",
        "tests.test_dev_goto_resilience_guard",
    ]
)


def guards(*paths):
    """The rule-14 labels that guard ``paths``.

    Rule 14 is per-path (a test file, a migration and a view are read by
    different checkers), so expectations below ask for the labels of the exact
    path under test. Whether those labels are *right* is pinned by
    ``RepoWideGuardTest`` and ``RepoWideScannerDiscoveryTest``.
    """
    labels: set[str] = set()
    for path in paths:
        labels.update(repo_wide_guard_labels(path))
    return sorted(labels)


#: The rule-14 labels for a plain non-test, non-migration Python module.
PYTHON_GUARD_LABELS = guards("events/views/detail.py")
#: ... and for a Python file inside a test tree, which fewer checkers read.
TEST_TREE_GUARD_LABELS = guards("content/tests/test_probe.py")
TEMPLATE_GUARD_LABELS = guards("templates/plans/my_plan_detail.html")
JAVASCRIPT_GUARD_LABELS = guards("static/js/video.js")


def collapsed(*labels):
    """The documented label shape: sorted, minus submodules a package covers.

    Rule 14 labels are module/class labels, so a plan that also selects their
    owning app label drops them. That collapse is pinned independently by
    ``test_labels_are_deduplicated_and_collapsed``; this helper just keeps the
    per-rule expectations below readable.
    """
    ordered = sorted(set(labels))
    return [
        label
        for label in ordered
        if not any(label != other and label.startswith(f"{other}.") for other in ordered)
    ]


def django_test_files():
    """Every Django test module, tracked and untracked.

    ``-c -o`` includes untracked modules: the SWE's new tests are uncommitted
    when the tester runs this, and a new repo-wide lint has to be visible to
    the discovery guard below before it is committed.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "--", "*/tests/*.py", "tests/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(listed))


#: Where a repo-wide checker can live. Django test modules are the obvious
#: half; the other half is the non-test gates -- ``make check-tailwind`` runs
#: ``scripts/verify_tailwind_build.py``, which walks the repo for Tailwind
#: class producers and is not a test module at all. A census that only parses
#: ``tests/**`` cannot see a checker like that, which is exactly how the
#: producer row came to be narrower than its checker with three controls green.
CHECKER_TREES = ("*/tests/*.py", "tests/*.py", "scripts/*.py", "*/management/commands/*.py", "asl_cli/*.py")

#: Prefixes whose modules are checkers rather than Django test modules: a
#: discovered scan here maps to a ``REPO_WIDE_GUARDS`` row by ``checker=``,
#: not to a Django label.
NON_TEST_CHECKER_PREFIXES = ("scripts/", "asl_cli/")


def checker_candidate_files():
    """Every module that could hold a repo-wide checker, tracked and untracked."""
    listed = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard", "--", *CHECKER_TREES],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(listed))


def _is_non_test_checker(relative):
    return relative.startswith(NON_TEST_CHECKER_PREFIXES) or "/management/commands/" in relative


def tracked_and_untracked_files():
    """Every file git knows about -- used by the rot guards below."""
    listed = subprocess.run(
        ["git", "ls-files", "-c", "-o", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(listed))


def plan_for(files, references=None):
    """Build a plan with a canned reverse-import expansion."""
    lookup = references or {}
    return build_plan(files, expander=lambda modules: {m: lookup.get(m, []) for m in modules})


@tag("core")
class RuleChainTest(SimpleTestCase):
    def test_app_source_targets_owning_app(self):
        plan = plan_for(["events/views/detail.py"])
        expected = collapsed("events", *PYTHON_GUARD_LABELS)
        self.assertEqual(plan.django_labels, expected)
        self.assertEqual(
            plan.django_command,
            f"uv run python manage.py test {' '.join(expected)} {DJANGO_COMMAND_SUFFIX}",
        )
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.playwright, "core")

    def test_reverse_import_expansion_adds_consumer_apps(self):
        plan = plan_for(
            ["integrations/services/zoom.py"],
            {"integrations.services.zoom": ZOOM_REFERENCES},
        )
        self.assertEqual(
            plan.django_labels,
            collapsed("api", "events", "integrations", "jobs", "studio", *PYTHON_GUARD_LABELS),
        )
        self.assertNotIn(CORE_COMMAND, plan.extra_commands)

    def test_reverse_import_expansion_over_cap_substitutes_core(self):
        too_many = [f"{label}/views/thing.py" for label in APP_LABELS[: REVERSE_IMPORT_APP_CAP + 2]]
        plan = plan_for(["content/models/article.py"], {"content.models.article": too_many})
        self.assertEqual(
            plan.django_labels,
            collapsed("content", *PYTHON_GUARD_LABELS),
            "the owning app must survive the cap",
        )
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(
            any(note.startswith("NOTE broad-impact:") for note in plan.notes),
            plan.notes,
        )

    def test_reverse_import_expansion_at_cap_is_not_substituted(self):
        at_cap = [f"{label}/views/thing.py" for label in APP_LABELS[:REVERSE_IMPORT_APP_CAP]]
        plan = plan_for(["content/models/article.py"], {"content.models.article": at_cap})
        self.assertNotIn(CORE_COMMAND, plan.extra_commands)
        app_labels = [label for label in plan.django_labels if "." not in label]
        self.assertEqual(len(app_labels), REVERSE_IMPORT_APP_CAP + 1)

    def test_reverse_import_hits_in_tests_package_target_the_test_module(self):
        plan = plan_for(
            ["voting/services/tally.py"],
            {"voting.services.tally": ["tests/test_health_check.py"]},
        )
        self.assertEqual(
            plan.django_labels,
            collapsed("tests.test_health_check", "voting", *PYTHON_GUARD_LABELS),
        )

    def test_playwright_references_are_a_note_not_an_escalation(self):
        plan = plan_for(
            ["integrations/services/zoom.py"],
            {"integrations.services.zoom": ZOOM_REFERENCES},
        )
        self.assertEqual(plan.playwright, "core")
        self.assertTrue(any(note.startswith("NOTE playwright-refs:") for note in plan.notes))

    def test_docs_only_diff_requires_no_tests(self):
        plan = plan_for(["_docs/configuration.md", "specs/04-content-articles.md", "_docs/audits/2026-01-01-x.md"])
        self.assertTrue(plan.no_tests_required)
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.commands(), [])
        self.assertEqual(plan.playwright, "none")

    def test_docs_do_not_suppress_code_in_the_same_diff(self):
        plan = plan_for(["_docs/configuration.md", "voting/models/poll.py"])
        self.assertEqual(plan.django_labels, collapsed("voting", *PYTHON_GUARD_LABELS))
        self.assertFalse(plan.no_tests_required)

    def test_agent_and_skill_markdown_is_not_a_no_test_path(self):
        # tests/ asserts the content of these files, so they map to `tests`.
        self.assertFalse(is_no_test_path(".claude/agents/software-engineer.md"))
        self.assertFalse(is_no_test_path(".claude/skills/x/SKILL.md"))
        self.assertTrue(is_no_test_path("_docs/configuration.md"))
        plan = plan_for([".claude/agents/tester.md"])
        self.assertEqual(plan.django_labels, ["tests"])

    def test_only_unguarded_docs_and_top_level_markdown_short_circuit(self):
        for path in ("_docs/configuration.md", "_docs/audits/2026-01-01-x.md", "specs/04-content-articles.md"):
            with self.subTest(path=path):
                self.assertTrue(is_no_test_path(path))
        for path in (
            "email_app/email_templates/welcome.md",
            "integrations/services/ai_eval/README.md",
            "asl_cli/README.md",
            "skills/ai-shipping-labs-member-api/SKILL.md",
            "docs/member-api/plans.md",
            "CLAUDE.md",
            "AGENTS.md",
            "README.md",
            "_docs/design-system.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_no_test_path(path))

    def test_email_template_markdown_maps_to_its_app(self):
        # email_app/email_templates/*.md are shipped email bodies asserted by
        # email_app tests -- an email-template-only diff must not report
        # "NO TESTS REQUIRED". Since #1750 the rule-14 comment lint rides
        # along, because it compiles this tree too (pinned in detail by
        # test_email_template_markdown_selects_the_template_comment_lint).
        plan = plan_for(["email_app/email_templates/welcome_paid.md"])
        self.assertFalse(plan.no_tests_required)
        self.assertEqual(
            plan.django_labels,
            collapsed("email_app", *guards("email_app/email_templates/welcome_paid.md")),
        )

    def test_readme_inside_an_app_maps_to_its_app(self):
        plan = plan_for(["integrations/services/ai_eval/README.md"])
        self.assertEqual(plan.django_labels, ["integrations"])

    def test_skills_markdown_maps_to_member_api(self):
        # member_api/tests/test_usage_docs_1112.py reads
        # skills/ai-shipping-labs-member-api/{SKILL,README,plans,books}.md.
        # The Playwright test over the same artifact is `local_only`, so the
        # core Playwright subset does NOT cover it -- the Django label is the
        # only real guard.
        for path in (
            "skills/ai-shipping-labs-member-api/SKILL.md",
            "skills/ai-shipping-labs-member-api/books.md",
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertFalse(plan.no_tests_required)
                self.assertEqual(plan.django_labels, ["member_api"])

    def test_canonical_event_skill_maps_to_its_exact_api_guard(self):
        plan = plan_for(
            [
                ".agents/skills/ai-shipping-labs-events/SKILL.md",
            ]
        )

        self.assertFalse(plan.no_tests_required)
        self.assertEqual(
            plan.django_labels,
            ["api.tests.test_events.EventsSkillDocSyncTest"],
        )
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.unmapped, [])
        self.assertFalse(any(note.startswith("WARN unmapped:") for note in plan.notes))

    def test_event_recap_skill_maps_to_its_exact_api_guard(self):
        plan = plan_for(
            [
                ".agents/skills/ai-shipping-labs-event-recaps/SKILL.md",
            ]
        )

        self.assertFalse(plan.no_tests_required)
        self.assertEqual(
            plan.django_labels,
            ["api.tests.test_events.EventsSkillDocSyncTest"],
        )
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.unmapped, [])
        self.assertFalse(any(note.startswith("WARN unmapped:") for note in plan.notes))

    def test_member_api_guide_maps_to_member_api(self):
        plan = plan_for(["docs/member-api/plans.md"])
        self.assertEqual(plan.django_labels, ["member_api"])

    def test_agent_instruction_files_map_to_the_tests_package(self):
        # CLAUDE.md, AGENTS.md, _docs/PROCESS.md and
        # _docs/testing-guidelines.md all have rot guards in RotGuardTest
        # below, which lives in tests/. A diff touching only one of them must
        # therefore run `tests`, not print NO TESTS REQUIRED.
        for path in (
            "CLAUDE.md",
            "AGENTS.md",
            "README.md",
            "_docs/PROCESS.md",
            "_docs/setup.md",
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertFalse(plan.no_tests_required)
                self.assertEqual(plan.django_labels, ["tests"])
        guideline_plan = plan_for(["_docs/testing-guidelines.md"])
        self.assertEqual(guideline_plan.django_labels, ["tests.test_affected_tests"])

    def test_guarded_doc_artifacts_map_to_their_reading_app(self):
        for path, expected in (
            ("_docs/design-system.md", ["accounts", "content"]),
            ("_docs/product.md", ["content"]),
            ("_docs/content.md", ["content"]),
            ("_docs/openapi.json", ["api"]),
            ("_docs/member-openapi.json", ["member_api"]),
            ("_docs/integrations/zoom.md", ["integrations"]),
            ("specs/07-events.md", ["content"]),
        ):
            with self.subTest(path=path):
                self.assertEqual(plan_for([path]).django_labels, expected)

    def test_ci_and_script_paths_target_the_tests_package(self):
        plan = plan_for([".github/workflows/deploy-dev.yml", "scripts/watch-ci.py", "Makefile"])
        self.assertEqual(plan.django_labels, collapsed("tests", *PYTHON_GUARD_LABELS))

    def test_compose_dispatch_files_map_to_entrypoint_contract(self):
        for path in ("entrypoint.sh", "docker-compose.yml"):
            with self.subTest(path=path):
                self.assertEqual(
                    plan_for([path]).django_labels,
                    ["jobs.tests.test_entrypoint_compose_dispatch"],
                )
        self.assertEqual(
            plan_for(["Dockerfile"]).django_labels,
            [
                "jobs.tests.test_entrypoint_compose_dispatch",
                "tests.test_tailwind_build",
            ],
        )

    def test_playwright_owner_inventory_contracts_target_exact_policy_tests(self):
        plan = plan_for(
            [
                "scripts/playwright_owner_inventory.py",
                "scripts/playwright_owner_inventory_ceilings.py",
                "tests/playwright_owner_inventory_live.json",
                "tests/test_playwright_owner_inventory.py",
            ]
        )
        self.assertEqual(
            plan.django_labels,
            collapsed(
                *LEXICAL_TEST_TREE_POLICY_LABELS,
                "tests.test_playwright_owner_inventory",
                *PYTHON_GUARD_LABELS,
            ),
        )
        self.assertEqual(plan.unmapped, [])

    def test_affected_test_tool_and_guideline_target_their_exact_contract(self):
        plan = plan_for(["scripts/affected_tests.py", "_docs/testing-guidelines.md"])
        self.assertEqual(
            plan.django_labels,
            collapsed("tests.test_affected_tests", *PYTHON_GUARD_LABELS),
        )

    def test_screenshot_capture_helper_targets_its_native_contract(self):
        plan = plan_for(["scripts/capture_screenshots.py"])
        self.assertEqual(
            plan.django_labels,
            collapsed("tests.test_capture_screenshots", *PYTHON_GUARD_LABELS),
        )
        self.assertEqual(plan.unmapped, [])

    def test_agent_branch_retirement_helper_targets_its_exact_synthetic_contract(self):
        plan = plan_for(
            [
                "scripts/retire-agent-branches.py",
                "tests/test_retire_agent_branches.py",
            ]
        )
        self.assertEqual(
            plan.django_labels,
            collapsed(
                *LEXICAL_TEST_TREE_POLICY_LABELS,
                "tests.test_retire_agent_branches",
                *PYTHON_GUARD_LABELS,
            ),
        )
        self.assertEqual(plan.unmapped, [])

    def test_website_paths_add_tests_website_and_core(self):
        plan = plan_for(["website/urls.py"])
        self.assertEqual(plan.django_labels, collapsed("tests", "website", *PYTHON_GUARD_LABELS))
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(plan.playwright, "full")

    def test_dependency_manifest_is_a_soft_core_fallback(self):
        plan = plan_for(["uv.lock", "pyproject.toml"])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        self.assertEqual(plan.playwright, "core", "manifests must not force full Playwright")
        self.assertTrue(any(note.startswith("NOTE dependency-manifest:") for note in plan.notes))

    def test_test_file_maps_to_its_exact_module_not_the_whole_app(self):
        path = "studio/tests/test_events.py"
        plan = plan_for([path])
        self.assertEqual(
            plan.django_labels,
            collapsed("studio.tests.test_events", *LEXICAL_TEST_TREE_POLICY_LABELS, *guards(path)),
        )

    def test_test_helper_maps_to_the_app_tests_package(self):
        path = "studio/tests/factories.py"
        plan = plan_for([path])
        self.assertEqual(
            plan.django_labels,
            collapsed("studio.tests", *LEXICAL_TEST_TREE_POLICY_LABELS, *guards(path)),
        )

    def test_top_level_test_module_maps_to_its_dotted_path(self):
        path = "tests/test_robots_txt.py"
        plan = plan_for([path])
        self.assertEqual(
            plan.django_labels,
            collapsed("tests.test_robots_txt", *LEXICAL_TEST_TREE_POLICY_LABELS, *guards(path)),
        )

    def test_playwright_test_file_runs_that_file_plus_core(self):
        path = "playwright_tests/test_dashboard.py"
        plan = plan_for([path])
        self.assertEqual(
            plan.django_labels,
            collapsed(*PLAYWRIGHT_TEST_TREE_POLICY_LABELS, *guards(path)),
        )
        self.assertEqual(plan.extra_commands, ["uv run pytest playwright_tests/test_dashboard.py -v"])
        self.assertEqual(plan.commands()[-1], PLAYWRIGHT_CORE_COMMAND)

    def test_deleted_playwright_test_file_does_not_emit_a_missing_pytest_command(self):
        missing = "playwright_tests/test_does_not_exist_1479.py"
        self.assertFalse((REPO_ROOT / missing).exists())
        plan = plan_for([missing])
        self.assertEqual(
            plan.django_labels,
            collapsed(*PLAYWRIGHT_TEST_TREE_POLICY_LABELS, *guards(missing)),
        )
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.commands()[-1], PLAYWRIGHT_CORE_COMMAND)

    def test_full_playwright_supersedes_per_file_playwright_commands(self):
        plan = plan_for(
            [
                "playwright_tests/conftest.py",  # escalation trigger
                "playwright_tests/test_dashboard.py",
                "playwright_tests/test_newsletter.py",
            ]
        )
        self.assertEqual(plan.playwright, "full")
        self.assertEqual(
            [c for c in plan.extra_commands if c.startswith("uv run pytest playwright_tests/")],
            [],
            "per-file Playwright commands must not survive escalation to the full suite",
        )
        self.assertEqual(
            plan.django_labels,
            collapsed(*PLAYWRIGHT_TEST_TREE_POLICY_LABELS, *guards("playwright_tests/conftest.py")),
        )
        self.assertEqual(
            plan.commands(),
            [plan.django_command, PLAYWRIGHT_FULL_COMMAND],
        )
        self.assertTrue(
            any(note.startswith("NOTE playwright-full-supersedes:") for note in plan.notes),
            plan.notes,
        )

    def test_per_file_playwright_commands_survive_a_core_plan(self):
        plan = plan_for(["playwright_tests/test_dashboard.py", "playwright_tests/test_newsletter.py"])
        self.assertEqual(plan.playwright, "core")
        self.assertEqual(
            plan.extra_commands,
            [
                "uv run pytest playwright_tests/test_dashboard.py -v",
                "uv run pytest playwright_tests/test_newsletter.py -v",
            ],
        )
        self.assertEqual(
            plan.django_labels,
            collapsed(*PLAYWRIGHT_TEST_TREE_POLICY_LABELS, *guards("playwright_tests/test_dashboard.py")),
        )

    def test_repository_test_tree_contracts_are_data_driven_and_composable(self):
        self.assertEqual(len(TEST_TREE_CONTRACTS), 2)
        for path in (
            "tests/test_policy.py",
            "accounts/tests/test_auth.py",
            "asl_cli/tests/test_cli.py",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    list(test_tree_contract_labels(path)),
                    LEXICAL_TEST_TREE_POLICY_LABELS,
                )
        self.assertEqual(
            list(test_tree_contract_labels("playwright_tests/test_dashboard.py")),
            PLAYWRIGHT_TEST_TREE_POLICY_LABELS,
        )
        self.assertEqual(test_tree_contract_labels("studio/tests/fixture.json"), ())

    def test_issue_1499_playwright_path_selects_collection_and_exact_owners(self):
        path = "playwright_tests/test_studio_campaigns.py"
        plan = plan_for([path])

        self.assertEqual(
            plan.django_labels,
            collapsed(*PLAYWRIGHT_TEST_TREE_POLICY_LABELS, *guards(path)),
        )
        self.assertIn(f"uv run pytest {path} -v", plan.extra_commands)

    def test_issue_1500_app_test_paths_select_lexical_policies_and_exact_modules(self):
        paths = [
            "events/tests/test_recap.py",
            "integrations/tests/test_cover_image_validation_797.py",
        ]
        plan = plan_for(paths)

        self.assertEqual(
            plan.django_labels,
            collapsed(
                *LEXICAL_TEST_TREE_POLICY_LABELS,
                "events.tests.test_recap",
                "integrations.tests.test_cover_image_validation_797",
                *guards(*paths),
            ),
        )

    def test_issue_1501_api_test_path_selects_status_policy_and_exact_module(self):
        path = "api/tests/test_user_merge.py"
        plan = plan_for([path])

        self.assertEqual(
            plan.django_labels,
            collapsed(*LEXICAL_TEST_TREE_POLICY_LABELS, "api.tests.test_user_merge", *guards(path)),
        )

    def test_unrelated_app_source_does_not_add_repository_test_tree_policies(self):
        plan = plan_for(["events/views/detail.py"])

        self.assertEqual(plan.django_labels, collapsed("events", *PYTHON_GUARD_LABELS))
        self.assertTrue(
            set(plan.django_labels).isdisjoint(LEXICAL_TEST_TREE_POLICY_LABELS)
        )

    def test_asl_cli_runs_plain_pytest(self):
        plan = plan_for(["asl_cli/asl_cli/cli.py"])
        self.assertEqual(plan.extra_commands, ["uv run pytest asl_cli/tests"])
        # The CLI is first-party Python, so the repo-wide Python scans (rule
        # 14) cover it too -- they read every non-test .py in the tree.
        self.assertEqual(plan.django_labels, collapsed(*PYTHON_GUARD_LABELS))

    def test_app_template_targets_its_app_and_core_playwright(self):
        plan = plan_for(["templates/plans/my_plan_detail.html"])
        self.assertEqual(plan.django_labels, collapsed("plans", *TEMPLATE_GUARD_LABELS))
        self.assertEqual(plan.playwright, "core")
        self.assertIn(PLAYWRIGHT_CORE_COMMAND, plan.commands())

    def test_email_template_markdown_selects_the_template_comment_lint(self):
        """The one compiled-template tree that is not ``templates/**`` (#1750).

        ``email_app/email_templates/*.md`` is compiled by
        ``django.template.Template`` in
        ``email_app/services/email_rendering.py``, so the single-line-token
        lint reads it. Until this row existed the plan for such a diff was
        ``email_app`` alone -- the lint that guards the file could not run on
        the diff that changed it.
        """
        plan = plan_for(["email_app/email_templates/welcome.md"])

        self.assertIn("content.tests.test_template_comment_lint", plan.django_labels)
        self.assertEqual(
            plan.django_labels,
            collapsed("email_app", "content.tests.test_template_comment_lint"),
        )
        # Only that lint: the other six template lints read `*.html` and never
        # open a `.md`, so a grouped row would have over-selected here.
        for label in REPO_WIDE_TEMPLATE_LINT_LABELS:
            self.assertNotIn(label, plan.django_labels)

    def test_shared_template_fragment_adds_content_core_and_full_playwright(self):
        plan = plan_for(["templates/includes/header.html"])
        self.assertEqual(plan.django_labels, collapsed("content", *TEMPLATE_GUARD_LABELS))
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(plan.playwright, "full")

    def test_unowned_template_dir_falls_back_to_core(self):
        plan = plan_for(["templates/legal/terms.html"])
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        note = next(note for note in plan.notes if note.startswith("NOTE template-fallback:"))
        for label in TEMPLATE_GUARD_LABELS:
            self.assertIn(label, note)

    def test_unowned_non_html_template_says_why_no_guard_applies(self):
        # The template lints read *.html, so this one matches none. The note
        # has to say that rather than print an empty label list.
        note = next(
            note
            for note in plan_for(["templates/emails/digest.txt"]).notes
            if note.startswith("NOTE template-fallback:")
        )
        self.assertIn("no repo-wide template guard matches this suffix", note)
        self.assertNotIn("()", note)

    def test_static_assets_select_the_javascript_guards_and_core_playwright(self):
        # First-party JS is also a Tailwind class producer (``_producer_classes()``
        # scans static/js literals), so it gets the bundle gate too.
        plan = plan_for(["static/js/video.js"])
        self.assertEqual(plan.django_labels, collapsed(*JAVASCRIPT_GUARD_LABELS))
        self.assertEqual(plan.extra_commands, [CHECK_TAILWIND_COMMAND])
        self.assertEqual(
            plan.commands(),
            [plan.django_command, CHECK_TAILWIND_COMMAND, PLAYWRIGHT_CORE_COMMAND],
        )

    def test_non_javascript_static_assets_only_need_core_playwright(self):
        plan = plan_for(["static/images/hero.png"])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.commands(), [PLAYWRIGHT_CORE_COMMAND])

    def test_tailwind_config_escalates_to_full_playwright(self):
        # The purge config is also first-party JavaScript at the repo root, so
        # the repo-root reference scan reads it (rule 14).
        plan = plan_for(["tailwind.config.js"])
        self.assertEqual(plan.playwright, "full")
        self.assertEqual(plan.django_labels, collapsed(*guards("tailwind.config.js")))
        self.assertEqual(plan.commands(), [plan.django_command, PLAYWRIGHT_FULL_COMMAND])

    def test_single_app_migration_targets_only_that_app(self):
        path = "events/migrations/0042_add_field.py"
        plan = plan_for([path])
        self.assertEqual(plan.django_labels, collapsed("events", *guards(path)))
        self.assertEqual(plan.extra_commands, [])

    def test_multi_app_migrations_add_core(self):
        plan = plan_for(
            [
                "events/migrations/0042_add_field.py",
                "jobs/migrations/0007_add_field.py",
            ]
        )
        self.assertEqual(
            plan.django_labels,
            collapsed("events", "jobs", *guards("events/migrations/0042_add_field.py")),
        )
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(any(note.startswith("NOTE multi-app-migration:") for note in plan.notes))

    def test_unmapped_path_fails_closed_with_a_warning(self):
        plan = plan_for(["weird/unknown.txt"])
        self.assertEqual(plan.unmapped, ["weird/unknown.txt"])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(
            any(note.startswith("WARN unmapped: weird/unknown.txt") for note in plan.notes),
            plan.notes,
        )

    def test_labels_are_deduplicated_and_collapsed(self):
        plan = plan_for(["studio/tests/test_events.py", "studio/views/events.py"])
        self.assertEqual(
            plan.django_labels,
            collapsed("studio", *LEXICAL_TEST_TREE_POLICY_LABELS, *PYTHON_GUARD_LABELS),
        )

    def test_empty_diff_requires_no_tests(self):
        plan = plan_for([])
        self.assertTrue(plan.no_tests_required)

    def test_django_command_uses_bounded_parallelism_and_excludes_slow_tags(self):
        plan = plan_for(["voting/models/poll.py"])
        self.assertIn("--parallel 4", plan.django_command)
        self.assertIn("--exclude-tag=visual_regression", plan.django_command)
        self.assertIn("--exclude-tag=postgres_migration", plan.django_command)


@tag("core")
class HubModuleMapTest(SimpleTestCase):
    def test_integrations_config_targets_integrations_tests_and_core(self):
        plan = plan_for(["integrations/config.py"])
        self.assertEqual(
            plan.django_labels,
            collapsed("integrations", "tests", *PYTHON_GUARD_LABELS),
        )
        self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_settings_registry_targets_integrations_tests_and_core(self):
        plan = plan_for(["integrations/settings_registry.py"])
        self.assertEqual(
            plan.django_labels,
            collapsed("integrations", "tests", *PYTHON_GUARD_LABELS),
        )
        self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_access_control_hubs_target_content_accounts_core_and_full_playwright(self):
        for path in ("content/access.py", "content/tier_config.py", "accounts/gating.py"):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(
                    plan.django_labels,
                    collapsed("accounts", "content", *PYTHON_GUARD_LABELS),
                )
                self.assertIn(CORE_COMMAND, plan.extra_commands)
                self.assertEqual(plan.playwright, "full")

    def test_shared_fixtures_target_core_and_full_playwright(self):
        path = "tests/fixtures.py"
        plan = plan_for([path])
        self.assertEqual(
            plan.django_labels,
            collapsed(*LEXICAL_TEST_TREE_POLICY_LABELS, *guards(path)),
        )
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        self.assertEqual(plan.playwright, "full")

    def test_payments_hubs_target_payments_accounts_api_and_full_playwright(self):
        for path in (
            "payments/tier_state.py",
            "payments/stripe_links.py",
            "payments/services/webhook_handlers.py",
            "payments/views/checkout.py",
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(
                    plan.django_labels,
                    collapsed("accounts", "api", "payments", *PYTHON_GUARD_LABELS),
                )
                self.assertEqual(plan.playwright, "full")

    def test_payments_tests_do_not_escalate(self):
        plan = plan_for(["payments/tests/test_stripe_webhook_observability.py"])
        self.assertEqual(plan.playwright, "core")

    def test_accounts_hubs_target_accounts_and_core(self):
        for path in (
            "accounts/models/user.py",
            "accounts/auth.py",
            "accounts/adapters.py",
            "accounts/signals.py",
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(plan.django_labels, collapsed("accounts", *PYTHON_GUARD_LABELS))
                self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_every_curated_map_key_still_exists_on_disk(self):
        for glob, _labels, _core in HUB_MODULE_MAP:
            with self.subTest(glob=glob):
                if glob.endswith("/*"):
                    target = REPO_ROOT / glob[:-2]
                    self.assertTrue(target.is_dir(), f"{glob} no longer exists")
                else:
                    self.assertTrue((REPO_ROOT / glob).is_file(), f"{glob} no longer exists")

    def test_every_curated_map_label_is_a_real_target(self):
        known = set(APP_LABELS) | {"tests", "website"}
        for glob, labels, _core in HUB_MODULE_MAP:
            for label in labels:
                with self.subTest(glob=glob, label=label):
                    self.assertIn(label, known)


@tag("core")
class EscalationTriggerTest(SimpleTestCase):
    def test_every_trigger_glob_escalates(self):
        samples = {
            "playwright_tests/conftest.py": "playwright_tests/conftest.py",
            "tests/fixtures.py": "tests/fixtures.py",
            "content/access.py": "content/access.py",
            "content/tier_config.py": "content/tier_config.py",
            "accounts/gating.py": "accounts/gating.py",
            "playwright_tests/test_access_control.py": "playwright_tests/test_access_control.py",
            "payments/tier_state.py": "payments/tier_state.py",
            "payments/stripe_links.py": "payments/stripe_links.py",
            "payments/services/*": "payments/services/webhook_handlers.py",
            "payments/views/*": "payments/views/checkout.py",
            "templates/includes/*": "templates/includes/header.html",
            "templates/_partials/*": "templates/_partials/messages.html",
            "templates/base.html": "templates/base.html",
            "website/*": "website/settings.py",
            "accounts/context_processors.py": "accounts/context_processors.py",
            "tailwind.config.js": "tailwind.config.js",
            "integrations/middleware.py": "integrations/middleware.py",
        }
        self.assertEqual(
            sorted(samples),
            sorted(glob for glob, _reason in ESCALATION_TRIGGERS),
            "the escalation table changed -- update this test and _docs/testing-guidelines.md",
        )
        for glob, sample in samples.items():
            with self.subTest(glob=glob):
                plan = plan_for([sample])
                self.assertEqual(plan.playwright, "full")
                self.assertTrue(plan.escalation_reasons)
                self.assertIn(PLAYWRIGHT_FULL_COMMAND, plan.commands())
                self.assertNotIn(PLAYWRIGHT_CORE_COMMAND, plan.commands())

    def test_every_trigger_file_still_exists_on_disk(self):
        for glob, _reason in ESCALATION_TRIGGERS:
            if "*" in glob:
                with self.subTest(glob=glob):
                    self.assertTrue((REPO_ROOT / glob[:-2]).is_dir(), f"{glob} no longer exists")
            else:
                with self.subTest(glob=glob):
                    self.assertTrue((REPO_ROOT / glob).is_file(), f"{glob} no longer exists")

    def test_escalation_reason_names_the_file(self):
        plan = plan_for(["playwright_tests/conftest.py"])
        self.assertEqual(plan.escalation_reasons, ["playwright_tests/conftest.py: shared fixtures"])


@tag("core")
class ReverseImportPatternTest(SimpleTestCase):
    def test_patterns_match_import_forms_and_quoted_paths(self):
        regex = re.compile("|".join(reverse_import_patterns("integrations.services.zoom")))
        for line in (
            "from integrations.services.zoom import create_meeting",
            "import integrations.services.zoom",
            "@patch('integrations.services.zoom.requests.post')",
            "from integrations.services import zoom",
        ):
            with self.subTest(line=line):
                self.assertRegex(line, regex)

    def test_patterns_do_not_match_a_longer_sibling_module(self):
        regex = re.compile("|".join(reverse_import_patterns("integrations.services.zoom")))
        self.assertNotRegex("from integrations.services.zoominfo import thing", regex)

    def test_reexporting_package_widens_the_search_to_the_parent(self):
        self.assertTrue(_parent_reexports("content.models", "article"))
        regex = re.compile("|".join(reverse_import_patterns("content.models.article")))
        self.assertRegex("from content.models import Article", regex)


@tag("core")
class ReverseImportGrepTest(SimpleTestCase):
    """Exercises the real ``git grep`` against the real tree."""

    def test_grep_finds_real_zoom_consumers(self):
        references = git_grep_references(["integrations.services.zoom"])
        found = set(references["integrations.services.zoom"])
        for expected in (
            "events/services/zoom_lifecycle.py",  # from integrations.services.zoom import ...
            "studio/views/events.py",  # from integrations.services.zoom import ...
            "integrations/tests/test_zoom.py",  # @patch('integrations.services.zoom...')
        ):
            self.assertIn(expected, found)

    def test_grep_returns_an_empty_list_for_an_unreferenced_module(self):
        # Assembled at runtime so this file does not itself contain the
        # quoted dotted path the grep looks for.
        missing = "integrations.services." + "no_such" + "_module_1468"
        references = git_grep_references([missing])
        self.assertEqual(references[missing], [])


@tag("core")
class RunCommandsTest(SimpleTestCase):
    def test_commands_drops_per_file_playwright_when_escalated(self):
        plan = Plan(base="origin/main")
        plan.extra_commands = [CORE_COMMAND, "uv run pytest playwright_tests/test_dashboard.py -v"]
        plan.playwright = "full"
        self.assertEqual(plan.commands(), [CORE_COMMAND, PLAYWRIGHT_FULL_COMMAND])

    def test_commands_run_django_then_extras_then_playwright(self):
        plan = Plan(base="origin/main")
        plan.django_command = "django"
        plan.extra_commands = [CORE_COMMAND]
        plan.playwright = "full"
        self.assertEqual(plan.commands(), ["django", CORE_COMMAND, PLAYWRIGHT_FULL_COMMAND])

    def test_worst_exit_code_is_forwarded(self):
        plan = Plan(base="origin/main")
        plan.django_command = "exit 0"
        plan.extra_commands = ["exit 3", "exit 1"]
        plan.playwright = "none"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_commands(plan), 3)

    def test_all_green_returns_zero(self):
        plan = Plan(base="origin/main")
        plan.django_command = "exit 0"
        plan.playwright = "none"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_commands(plan), 0)


@tag("core")
class CliTest(SimpleTestCase):
    def _run(self, *args):
        return subprocess.run(
            ["uv", "run", "python", "scripts/affected_tests.py", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_json_output_matches_the_documented_shape(self):
        completed = self._run("--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            sorted(payload),
            sorted(
                [
                    "base",
                    "files",
                    "django_labels",
                    "django_command",
                    "extra_commands",
                    "playwright",
                    "escalation_reasons",
                    "unmapped",
                    "notes",
                ]
            ),
        )
        self.assertIsInstance(payload["files"], list)
        self.assertIsInstance(payload["django_labels"], list)
        self.assertIsInstance(payload["extra_commands"], list)
        self.assertIn(payload["playwright"], {"core", "full", "none"})

    def test_bad_base_ref_exits_two(self):
        completed = self._run("--base", "refs/heads/definitely-not-a-branch-1468")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("error:", completed.stderr)


@tag("core")
class GuardedDocArtifactTest(SimpleTestCase):
    """Every doc artifact a Django test READS must map to that test's app.

    This is the guard that stops rule 1 from silently dropping a file with a
    real assertion over it (issue #1468 QA rounds 2 and 3: markdown under
    ``email_app/``, then ``skills/**``, then ``CLAUDE.md``). Docs that a test
    merely mentions -- a ``docs_url`` string in the settings registry, say --
    are excluded on purpose: renaming those cannot break the assertion.
    """

    TREES = ("_docs", "docs", "specs", "skills")
    TOP_LEVEL = ("CLAUDE.md", "AGENTS.md", "README.md")
    READ_CALL = re.compile(r"Path\(|open\(|read_text|read_bytes|\.exists\(|is_file\(|is_dir\(")
    # A top-level filename is only this repo's copy when it is anchored to the
    # repo root. A bare 'README.md' literal in content-sync tests means the
    # *content repo's* README, not ours.
    REPO_ANCHOR = re.compile(r"REPO_ROOT|BASE_DIR|PROJECT_ROOT|REPO_DIR")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        trees = "|".join(cls.TREES)
        cls.slash_re = re.compile(rf"""["']({trees})/([^"'\s]+)["']""")
        cls.chain_re = re.compile(rf"""["']({trees})["']\s*/\s*["']([^"'\s]+)["'](?:\s*/\s*["']([^"'\s]+)["'])?""")
        cls.top_level_re = re.compile(
            r"""["'](%s)["']""" % "|".join(name.replace(".", r"\.") for name in cls.TOP_LEVEL)
        )

    def _django_test_files(self):
        return django_test_files()

    def _artifacts_read_by_tests(self):
        """-> {artifact path: {app label of the test module reading it}}."""
        found: dict[str, set[str]] = {}
        for relative in self._django_test_files():
            owner = relative.split("/")[0]
            text = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if not self.READ_CALL.search(line):
                    continue
                paths = {f"{m.group(1)}/{m.group(2)}" for m in self.slash_re.finditer(line)}
                paths |= {"/".join(part for part in match.groups() if part) for match in self.chain_re.finditer(line)}
                if self.REPO_ANCHOR.search(line):
                    paths |= {m.group(1) for m in self.top_level_re.finditer(line)}
                for artifact in paths:
                    if (REPO_ROOT / artifact).exists():
                        found.setdefault(artifact, set()).add(owner)
        return found

    def test_scanner_finds_the_known_readers(self):
        # Fails loudly if the scanner itself stops working (e.g. a regex
        # change), which would otherwise make the guard below vacuous.
        found = self._artifacts_read_by_tests()
        self.assertIn("skills/ai-shipping-labs-member-api", found)
        self.assertIn("member_api", found["skills/ai-shipping-labs-member-api"])
        self.assertIn("_docs/design-system.md", found)
        for top_level in ("CLAUDE.md", "AGENTS.md", "README.md"):
            self.assertIn(top_level, found)
            self.assertIn(TESTS_PACKAGE, found[top_level])

    def test_every_read_artifact_selects_its_reader(self):
        for artifact, owners in sorted(self._artifacts_read_by_tests().items()):
            probe = artifact if (REPO_ROOT / artifact).is_file() else f"{artifact}/probe.md"
            labels = plan_for([probe]).django_labels
            for owner in sorted(owners):
                expected = TESTS_PACKAGE if owner == TESTS_PACKAGE else owner
                with self.subTest(artifact=artifact, owner=owner):
                    self.assertTrue(
                        expected in labels or any(label.startswith(f"{expected}.") for label in labels),
                        f"{artifact} is read by {owner} tests but the plan selects {labels}. "
                        f"Add it to CONTRACT_PATHS in scripts/affected_tests.py.",
                    )


# ---------------------------------------------------------------------------
# Rule 14: repo-wide guards (issue #1755)
#
# The defect class this section exists to kill: the map in
# ``scripts/affected_tests.py`` used to be maintained by hand *alongside* the
# checkers it selects, so the two drifted. Four instances were found the
# expensive way -- twice by a red Deploy Gates on main. The scanner below
# discovers tree-scanning checkers on disk and the tests assert the strong
# per-guard property: every path in a declared scan set selects that guard.
# ---------------------------------------------------------------------------

#: Trees whose scanners cannot be selected by the directory they live in.
REPO_WIDE_TREES: tuple[str, ...] = ("", "templates", "static", "static/js")

#: Suffixes a tree scan can plausibly guard, per tree. Used to turn a
#: discovered ``rglob('*')`` into concrete probe paths.
TREE_SUFFIXES = {
    "": (".py", ".html", ".js"),
    "templates": (".html",),
    "static": (".js",),
    "static/js": (".js",),
}

#: Representative changed paths per (tree, suffix). Deliberately paths whose
#: owning app does NOT own any repo-wide guard, so "selected" can never mean
#: "the app label happened to cover it" -- and deliberately including paths
#: inside the regions rule-14 rows exclude (test trees, migrations,
#: ``static/vendor``). A probe set that only samples the easy middle of a tree
#: cannot observe a row that is narrower than its checker, which is exactly how
#: the first cut of this guard stayed green over `studio.tests.test_admin_links`
#: (it reads `tests/**` and `playwright_tests/**`).
TREE_PROBES = {
    ("", ".py"): (
        "events/views/detail.py",
        "content/tests/test_probe_1755.py",
        "tests/test_probe_1755.py",
        "playwright_tests/test_probe_1755.py",
        "events/migrations/0042_probe_1755.py",
    ),
    ("", ".html"): (
        "templates/plans/my_plan_detail.html",
        "templates/community_base/public/base.html",
    ),
    ("", ".js"): ("static/js/video.js", "static/vendor/probe_1755.js"),
    ("templates", ".html"): (
        "templates/plans/my_plan_detail.html",
        "templates/community_base/public/base.html",
    ),
    ("static", ".js"): ("static/js/video.js", "static/vendor/probe_1755.js"),
    ("static/js", ".js"): ("static/js/video.js",),
}


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _join_path(base, extra):
    extra = extra.strip("/")
    if not extra:
        return base
    return f"{base}/{extra}" if base else extra


def _binding_key(node):
    """``ROOT``/``cls.base_dir``/``self.base_dir`` -> one lookup key."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in {"cls", "self"}:
        return f"@{node.attr}"
    return None


def _reaches_dunder_file(node):
    while True:
        if isinstance(node, ast.Call):
            node = node.args[0] if _call_name(node.func) in {"Path", "PurePath"} and node.args else node.func
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        else:
            break
    return isinstance(node, ast.Name) and node.id == "__file__"


def _is_repo_anchor(node):
    """``Path(__file__).resolve().parents[2]``, ``settings.BASE_DIR``, ..."""
    if isinstance(node, ast.Attribute) and node.attr == "BASE_DIR":
        return True
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
        return _reaches_dunder_file(node)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return _reaches_dunder_file(node)
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name in {"Path", "PurePath"} and len(node.args) == 1:
            return _is_repo_anchor(node.args[0])
        if name in {"resolve", "absolute"} and isinstance(node.func, ast.Attribute):
            return _is_repo_anchor(node.func.value)
    return False


def _literal_paths(node, env):
    """Evaluate to a set of literal relative paths, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value.strip("/")}
    if isinstance(node, ast.Call) and _call_name(node.func) in {"Path", "PurePath", "PosixPath"}:
        joined = None
        for arg in node.args:
            values = _literal_paths(arg, env)
            if values is None:
                return None
            joined = values if joined is None else {_join_path(a, b) for a in joined for b in values}
        return joined
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out = set()
        for element in node.elts:
            values = _literal_paths(element, env)
            if values is None:
                return None
            out |= values
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_paths(node.left, env)
        right = _literal_paths(node.right, env)
        return None if left is None or right is None else left | right
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)) and len(node.generators) == 1:
        return _literal_paths(node.generators[0].iter, env)
    if isinstance(node, ast.Call) and _call_name(node.func) in {"tuple", "list", "frozenset", "set", "sorted"}:
        return _literal_paths(node.args[0], env) if node.args else None
    if isinstance(node, ast.Starred):
        return _literal_paths(node.value, env)
    key = _binding_key(node)
    return env.get(("literal", key)) if key is not None else None


def _anchored_paths(node, env):
    """Evaluate to repo-relative roots, or None when not anchored at the repo."""
    if _is_repo_anchor(node):
        return {""}
    if isinstance(node, ast.Call) and _call_name(node.func) in {"Path", "PurePath"} and len(node.args) > 1:
        base = _anchored_paths(node.args[0], env)
        if base is None:
            return None
        for arg in node.args[1:]:
            extra = _literal_paths(arg, env)
            if extra is None:
                return None
            base = {_join_path(b, e) for b in base for e in extra}
        return base
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"resolve", "absolute"}:
        return _anchored_paths(node.func.value, env)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        base = _anchored_paths(node.left, env)
        extra = _literal_paths(node.right, env)
        if base is None or extra is None:
            return None
        return {_join_path(b, e) for b in base for e in extra}
    key = _binding_key(node)
    return env.get(("anchored", key)) if key is not None else None


def _bind(env, kind, key, value):
    if value and env.get((kind, key)) != value:
        env[(kind, key)] = value
        return True
    return False


def _collect_bindings(tree, env):
    """One pass over assignments, loop targets and call arguments."""
    changed = False
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for function in functions.values():
        # ``def producer_python_files(root=ROOT)`` -- the anchor arrives as a
        # parameter default, not from a call site. Missing this is how a
        # refactor hid `ROOT.rglob("*.py")` from the census in
        # `scripts/verify_tailwind_build.py`.
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        for argument, default in zip(positional[len(positional) - len(arguments.defaults):], arguments.defaults):
            changed |= _bind(env, "anchored", argument.arg, _anchored_paths(default, env))
            changed |= _bind(env, "literal", argument.arg, _literal_paths(default, env))
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
            if default is not None:
                changed |= _bind(env, "anchored", argument.arg, _anchored_paths(default, env))
                changed |= _bind(env, "literal", argument.arg, _literal_paths(default, env))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                key = _binding_key(target)
                if key is None:
                    continue
                changed |= _bind(env, "anchored", key, _anchored_paths(node.value, env))
                changed |= _bind(env, "literal", key, _literal_paths(node.value, env))
        elif isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
            changed |= _bind(env, "literal", node.target.id, _literal_paths(node.iter, env))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # A helper that globs on one of its parameters is only a repo-wide
            # scanner because of what its call sites pass in.
            target = functions.get(node.func.id)
            if target is None:
                continue
            params = [argument.arg for argument in target.args.args]
            for index, argument in enumerate(node.args[: len(params)]):
                changed |= _bind(env, "anchored", params[index], _anchored_paths(argument, env))
                changed |= _bind(env, "literal", params[index], _literal_paths(argument, env))
    return changed


def _enclosing_class(tree, target):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(child is target for child in ast.walk(node)):
            return node.name
    return None


def repo_wide_tree_scans(source, *, app_labels=APP_LABELS):
    """-> {(tree, glob pattern, enclosing class or None)} for one test module.

    A "tree scan" is a ``glob``/``rglob`` whose receiver resolves to the
    repository root, ``settings.BASE_DIR`` or a ``PROJECT_ROOT``-style anchor,
    over ``templates/``, ``static/`` or the root itself. A scan spelled as a
    list of roots that enumerates a majority of the project apps counts as a
    root scan too (``studio/tests/test_admin_links.py``).

    KNOWN BLIND SPOT -- discovery is a floor, not a census. This is a static
    AST resolver over two method names, run over ``CHECKER_TREES`` (Django test
    modules plus the non-test gates in ``scripts/``, management commands and
    ``asl_cli``). A repo-wide scan is invisible to it when it lives outside
    those trees, or when it is spelled any other way:

    * enumerated through a subprocess -- ``git ls-files`` (a real instance
      today: ``integrations/tests/test_google_analytics_loader.py``) or
      ``find``;
    * enumerated with ``os.walk`` / ``os.scandir`` / ``glob.glob`` instead of
      ``pathlib``;
    * rooted at a path this resolver cannot fold to a constant (a value read
      from settings at runtime, a root passed through more than one hop). It
      does fold module constants, ``cls``/``self`` attributes, one-hop call
      arguments and parameter defaults -- that last one only because a
      refactor to ``def producer_python_files(root=ROOT)`` hid a live
      ``ROOT.rglob('*.py')`` from this scanner.

    ``test_scanner_finds_the_known_scanners`` pins the scanners it does find so
    a resolver regression fails loudly, but a green run of this module is NOT
    evidence that every repo-wide checker has a ``REPO_WIDE_GUARDS`` row. When
    you add a checker that walks a tree by any other means, add its row by
    hand -- nothing here will remind you.
    """
    tree = ast.parse(source)
    env = {}
    for _ in range(5):
        if not _collect_bindings(tree, env):
            break
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"glob", "rglob"} or not node.args:
            continue
        pattern = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
        if not isinstance(pattern, str):
            continue
        roots = _anchored_paths(node.func.value, env)
        if not roots:
            continue
        enumerated_apps = {root.split("/")[0] for root in roots if root} & set(app_labels)
        if len(enumerated_apps) * 2 >= len(app_labels):
            roots = set(roots) | {""}
        prefix, _, leaf = pattern.rpartition("/")
        if any(character in prefix for character in "*?["):
            continue
        for root in roots:
            scanned = _join_path(root, prefix)
            if scanned in REPO_WIDE_TREES:
                found.add((scanned, leaf, _enclosing_class(tree, node)))
    return found


@functools.lru_cache(maxsize=1)
def discover_repo_wide_scanners():
    """-> {test module path: {(tree, pattern, class or None)}} across the tree.

    Cached: the scan AST-parses every Django test module, and several guards
    below ask for the same answer.
    """
    discovered = {}
    for relative in checker_candidate_files():
        if not _is_non_test_checker(relative) and not Path(relative).name.startswith("test_"):
            continue
        scans = repo_wide_tree_scans((REPO_ROOT / relative).read_text(encoding="utf-8", errors="ignore"))
        if scans:
            discovered[relative] = scans
    return discovered


def _guard_label_is_selected(module_label, class_name, labels):
    """True when ``labels`` explicitly runs the guard (never via test-core)."""
    for label in labels:
        if label == module_label or module_label.startswith(f"{label}."):
            return True
        if class_name is not None and label == f"{module_label}.{class_name}":
            return True
    return False


@tag("core")
class RepoWideScannerDiscoveryTest(SimpleTestCase):
    """The table cannot fall behind a newly added repo-wide lint (#1755)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.discovered = discover_repo_wide_scanners()

    def test_scanner_finds_the_known_scanners(self):
        # Positive control: a regex/resolver change must not make the guard
        # below vacuous by quietly discovering nothing.
        trees = {(module, tree) for module, scans in self.discovered.items() for tree, _pattern, _cls in scans}
        for expected in (
            ("accounts/tests/test_button_class_lint.py", "templates"),
            ("accounts/tests/test_template_date_vocabulary.py", "templates"),
            ("content/tests/test_container_widths.py", "templates"),
            ("content/tests/test_design_system_lint.py", "templates"),
            ("content/tests/test_internal_copy_lint.py", "templates"),
            ("content/tests/test_status_contrast_1279.py", "templates"),
            ("content/tests/test_template_comment_lint.py", "templates"),
            ("studio/tests/test_admin_links.py", "templates"),
            ("studio/tests/test_form_components.py", "templates"),
            ("tests/test_tailwind_build.py", "templates"),
            ("tests/test_tailwind_build.py", "static/js"),
            ("tests/test_tailwind_build.py", ""),
            ("email_app/tests/test_email_service_shim_1651.py", ""),
            ("jobs/tests/test_async_task_names.py", ""),
            ("tests/test_unreachable_legacy_templates_1543.py", ""),
            # The non-test half of the census. A resolver change that re-hides
            # this (a parameter default, say) must fail loudly.
            ("scripts/verify_tailwind_build.py", ""),
            ("scripts/verify_tailwind_build.py", "static/js"),
        ):
            with self.subTest(scanner=expected):
                self.assertIn(expected, trees)

    def test_source_scan_class_attribution_is_precise(self):
        # Class granularity is what lets a bundle-free lint be selected
        # without dragging in the assertions that read static/css/tailwind.css.
        classes = {cls for tree, _pattern, cls in self.discovered["tests/test_tailwind_build.py"]}
        self.assertEqual(classes, {"TailwindSourceScanTest"})

    def test_a_new_tree_scanner_without_a_table_entry_is_detected(self):
        # The shape the guard has to catch: a brand-new lint that walks every
        # template and is not in REPO_WIDE_GUARDS yet.
        scans = repo_wide_tree_scans(
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[1]\n"
            "class NewTemplateLintTest:\n"
            "    def test_x(self):\n"
            "        for path in (ROOT / 'templates').rglob('*.html'):\n"
            "            pass\n"
        )
        self.assertEqual(scans, {("templates", "*.html", "NewTemplateLintTest")})
        self.assertFalse(
            _guard_label_is_selected(
                "content.tests.test_new_template_lint",
                "NewTemplateLintTest",
                plan_for(["templates/plans/my_plan_detail.html"]).django_labels,
            )
        )

    def test_every_discovered_non_test_checker_has_a_row_naming_it(self):
        # A checker outside tests/** cannot be selected by a Django label, so
        # its row has to claim it by module path. Without this the census
        # simply skipped `scripts/verify_tailwind_build.py`.
        for module, scans in sorted(self.discovered.items()):
            if not _is_non_test_checker(module):
                continue
            rows = [guard for guard in REPO_WIDE_GUARDS if guard.checker == module]
            trees = sorted({tree or "<repo root>" for tree, _pattern, _cls in scans})
            with self.subTest(module=module):
                self.assertTrue(
                    rows,
                    f"{module} scans the whole {', '.join(trees)} tree but no REPO_WIDE_GUARDS row "
                    f"names it as its checker. Add a row with checker={module!r} in "
                    f"scripts/affected_tests.py (rule 14).",
                )
            for row in rows:
                for tree, pattern, _cls in sorted(scans, key=lambda scan: (scan[0], scan[1])):
                    suffix = Path(pattern).suffix
                    suffixes = (suffix,) if suffix else TREE_SUFFIXES[tree]
                    for probe in sorted({p for s in suffixes for p in TREE_PROBES.get((tree, s), ())}):
                        if row.matches(probe):
                            continue
                        with self.subTest(module=module, row=row.name, probe=probe):
                            self.assertIn(
                                row.name,
                                CHECKER_EVIDENCE,
                                f"row {row.name!r} narrows {module} away from {probe} with no "
                                f"evidence from the checker. Either widen the row in "
                                f"scripts/affected_tests.py or add evidence to CHECKER_EVIDENCE.",
                            )

    def test_every_row_with_a_checker_module_points_at_a_real_file(self):
        for guard in REPO_WIDE_GUARDS:
            if guard.checker:
                with self.subTest(row=guard.name):
                    self.assertTrue((REPO_ROOT / guard.checker).is_file(), guard.checker)
                    self.assertIn(guard.checker, self.discovered, f"{guard.checker} scans no tree")

    def test_every_discovered_tree_scanner_is_selected_for_the_tree_it_scans(self):
        for module, scans in sorted(self.discovered.items()):
            if _is_non_test_checker(module):
                continue  # covered by the two tests above
            module_label = module[: -len(".py")].replace("/", ".")
            for tree, pattern, class_name in sorted(scans, key=lambda scan: (scan[0], scan[1], scan[2] or "")):
                suffix = Path(pattern).suffix
                suffixes = (suffix,) if suffix else TREE_SUFFIXES[tree]
                for probe in sorted({probe for s in suffixes for probe in TREE_PROBES.get((tree, s), ())}):
                    labels = plan_for([probe]).django_labels
                    excluding = _rows_excluding(module_label, class_name, probe)
                    with self.subTest(module=module, tree=tree or "<repo root>", probe=probe):
                        if _guard_label_is_selected(module_label, class_name, labels):
                            continue
                        # Not selected: only legal when the row that owns this
                        # checker declares the probe outside its scan set AND
                        # that narrowing is backed by checker evidence.
                        self.assertTrue(
                            excluding,
                            f"{module} scans the whole {tree or 'repository root'} tree, but the plan for "
                            f"{probe} selects {labels}. Add {module_label} to REPO_WIDE_GUARDS in "
                            f"scripts/affected_tests.py (rule 14) -- do not annotate or narrow the lint.",
                        )
                        for row in excluding:
                            self.assertIn(
                                row,
                                CHECKER_EVIDENCE,
                                f"row {row!r} narrows {module_label} away from {probe} with no evidence "
                                f"from the checker. Either widen the row in scripts/affected_tests.py or "
                                f"add evidence to CHECKER_EVIDENCE in this file.",
                            )


# ---------------------------------------------------------------------------
# Exclusion evidence: a row may only be narrower than its tree when the checker
# itself says so.
#
# The first cut of rule 14 grouped six labels under one row and applied the
# Tailwind lint's exclusion set to all of them, so `studio.tests.test_admin_links`
# -- which explicitly scans `tests/**` and `playwright_tests/**` and pins their
# content hashes -- was declared narrower than it is. The drift had moved from
# "missing table entry" to "wrong table entry". These helpers close that: every
# declared exclusion is checked against the checker's own constant, enumerator
# or skip predicate, and a checker that offers none must be declared wide.
# ---------------------------------------------------------------------------


def _row(name):
    return next(guard for guard in REPO_WIDE_GUARDS if guard.name == name)


def _label_module(label):
    """``a.b.test_c.Klass`` -> (``a/b/test_c.py``, ``Klass``)."""
    parts = label.split(".")
    module = f"{'/'.join(parts)}.py"
    if (REPO_ROOT / module).is_file():
        return module, None
    return f"{'/'.join(parts[:-1])}.py", parts[-1]


def narrowing_rows():
    """Rows that do NOT select their checker somewhere inside the tree it scans.

    Narrowing is legal -- checkers really do skip test trees and build output --
    but only with evidence from the checker, which is what ``CHECKER_EVIDENCE``
    holds. The rule binds on "narrower than what the checker reads", however
    the narrowing is *expressed*, which is three ways, not one:

    * an ``excludes`` entry;
    * a glob set that omits a suffix the scanned tree contains;
    * positive globs that simply do not reach a family the checker reads. This
      is the one that bit `tailwind-producers` -- it declared two of the four
      families ``_producer_classes()`` scans and had no ``excludes`` key at
      all, so keying the rule on ``excludes`` left the narrowest row in the
      table exempt from it.

    A row with no labels cannot be checked against a discovered tree scanner at
    all (its checker is not a Django test module), so it always requires
    evidence.
    """
    discovered = discover_repo_wide_scanners()
    narrowing = {guard.name for guard in REPO_WIDE_GUARDS if guard.excludes or not guard.labels}
    for guard in REPO_WIDE_GUARDS:
        for label in guard.labels:
            module, _class_name = _label_module(label)
            for tree, pattern, _cls in discovered.get(module, ()):
                suffix = Path(pattern).suffix
                suffixes = (suffix,) if suffix else TREE_SUFFIXES[tree]
                for probe in {probe for s in suffixes for probe in TREE_PROBES.get((tree, s), ())}:
                    if not guard.matches(probe):
                        narrowing.add(guard.name)
    return narrowing


def _rows_excluding(module_label, class_name, path):
    """Rows that own ``module_label`` but declare ``path`` out of scope."""
    owning = []
    for guard in REPO_WIDE_GUARDS:
        for label in guard.labels:
            if label == module_label or label == f"{module_label}.{class_name}":
                owning.append(guard)
                break
    return [guard.name for guard in owning if not guard.matches(path)]


def _relative(path):
    return Path(path).resolve().relative_to(REPO_ROOT).as_posix()


def _files_read_by_the_source_scan(excluded_parts):
    """Repo-relative paths the Tailwind source lint reads, under ``excluded_parts``.

    Recording the reads (rather than the lint's verdict) is what turns "the
    module imports the shared constant" into "the lint obeys it": swap the
    constant and the set of files it opens has to move with it.
    """
    case = test_tailwind_build.TailwindSourceScanTest(
        "test_no_tailwind_utility_is_built_from_a_runtime_fragment"
    )
    seen: list[str] = []
    original = Path.read_text

    def recording(self, *args, **kwargs):
        try:
            seen.append(self.resolve().relative_to(REPO_ROOT).as_posix())
        except ValueError:  # pragma: no cover - a read from outside the repo
            pass
        return original(self, *args, **kwargs)

    with (
        mock.patch.object(test_tailwind_build, "SOURCE_SCAN_EXCLUDED_PARTS", excluded_parts),
        mock.patch.object(Path, "read_text", recording),
    ):
        try:
            case.test_no_tailwind_utility_is_built_from_a_runtime_fragment()
        except AssertionError:
            # Under a widened exclusion set the lint may legitimately find
            # offenders; only the set of files it opened matters here.
            pass
    return seen


def _tailwind_source_scan_evidence(testcase):
    """Evidence: the lint obeys the shared exclusion set, not a private copy."""
    row = _row("tailwind-source-scan")
    testcase.assertEqual(row.excludes, excluded_path_globs())
    testcase.assertIs(test_tailwind_build.SOURCE_SCAN_EXCLUDED_PARTS, SOURCE_SCAN_EXCLUDED_PARTS)
    for part in SOURCE_SCAN_EXCLUDED_PARTS:
        testcase.assertFalse(row.matches(f"content/{part}/probe_1755.py"))

    # Importing the constant is not using it: a lint that re-inlines the set
    # and keeps the import alive with a silenced linter would satisfy the
    # identity check above. So drive the lint twice and watch what it opens.
    baseline = _files_read_by_the_source_scan(SOURCE_SCAN_EXCLUDED_PARTS)
    testcase.assertGreater(len(baseline), 100, "the lint went vacuous")
    for relative in baseline:
        testcase.assertTrue(row.matches(relative), f"the lint reads {relative} but the row excludes it")

    loosened = tuple(
        part for part in SOURCE_SCAN_EXCLUDED_PARTS if part not in {"tests", "playwright_tests"}
    )
    widened = _files_read_by_the_source_scan(loosened)
    testcase.assertTrue(
        [path for path in widened if path.startswith(("tests/", "playwright_tests/"))],
        "the lint ignored the swapped exclusion set -- it is not using the shared constant",
    )


def _async_task_scan_evidence(testcase):
    """Evidence: the checker's own file iterator never yields an excluded path."""
    row = _row("async-task-name-scan")
    yielded = [_relative(path) for path in test_async_task_names._iter_python_files(REPO_ROOT)]
    testcase.assertGreater(len(yielded), 100, "the iterator went vacuous")
    for relative in yielded:
        testcase.assertTrue(row.matches(relative), f"{relative} is read but the row excludes it")
    # Non-vacuous in the other direction: the regions the row excludes really
    # are absent from what the checker reads.
    testcase.assertFalse(any(relative.startswith("tests/") for relative in yielded))
    testcase.assertTrue(any(relative.startswith("playwright_tests/") for relative in yielded))
    testcase.assertTrue(any("/migrations/" in relative for relative in yielded))


def _emailservice_scan_evidence(testcase):
    """Evidence: the checker's own skip predicate rejects every excluded path."""
    row = _row("emailservice-inventory-scan")
    for excluded in (
        "tests/test_probe_1755.py",
        "content/tests/test_probe_1755.py",
        "playwright_tests/test_probe_1755.py",
    ):
        testcase.assertFalse(row.matches(excluded))
        testcase.assertTrue(test_email_service_shim_1651._is_test_path(Path(excluded)))
    for scanned in ("events/views/detail.py", "events/migrations/0042_probe_1755.py"):
        testcase.assertTrue(row.matches(scanned))
        testcase.assertFalse(test_email_service_shim_1651._is_test_path(Path(scanned)))


#: Suffixes a checker reads that its row deliberately does not declare. One
#: entry, recorded rather than hidden: `studio/tests/test_admin_links.py` also
#: scans `.md` under `_docs/`, `docs/`, `specs/` and `README.md`, which collides
#: with rule 1 (docs are a no-test path) and needs a product call before rule 14
#: claims it. Anything else appearing here fails the test below.
UNDECLARED_ENUMERATED_SUFFIXES = {"admin-link-scan": {".md"}}


def _admin_link_scan_evidence(testcase):
    """Evidence: the checker's own file list shows which suffixes it reads.

    ``admin-link-scan`` declares ``*.py`` and ``*.html`` and therefore narrows
    the repo-root tree away from ``.js``. ``_scanned_files()`` is the proof:
    it reads .py, .html and .md, never .js.
    """
    row = _row("admin-link-scan")
    files = [relative.as_posix() for relative, _absolute in test_admin_links._scanned_files()]
    testcase.assertGreater(len(files), 100, "the scanner went vacuous")
    testcase.assertEqual([path for path in files if path.endswith(".js")], [])
    testcase.assertTrue(any(path.startswith("tests/") for path in files))
    testcase.assertTrue(any(path.startswith("playwright_tests/") for path in files))
    testcase.assertTrue(any("/migrations/" in path for path in files))
    for relative in files:
        if Path(relative).suffix in UNDECLARED_ENUMERATED_SUFFIXES["admin-link-scan"]:
            continue
        testcase.assertTrue(row.matches(relative), f"{relative} is read but the row excludes it")


def _tailwind_producer_evidence(testcase):
    """Evidence: the checker's own producer enumerator, all four families.

    ``scripts/verify_tailwind_build.py`` is not a Django test module, so the
    discovery scanner cannot see it -- this row's only binding is the checker's
    file list.
    """
    row = _row("tailwind-producers")
    files = [_relative(path) for path in verify_tailwind_build.producer_paths()]
    testcase.assertGreater(len(files), 50, "the producer enumerator went vacuous")
    for relative in files:
        testcase.assertTrue(
            row.matches(relative),
            f"verify_bundle() reads {relative} but the row's scan set excludes it. Widen the "
            f"row in scripts/affected_tests.py -- exclusions belong to the checker.",
        )
    # Non-vacuous per family: PRODUCER_FILES, studio/views, forms|widgets, static/js.
    for marker in ("content/models/project.py", "studio/views/", "/forms/", "static/js/"):
        testcase.assertTrue(
            any(marker in relative for relative in files),
            f"the producer enumerator no longer reaches {marker}",
        )


#: Every row that narrows its checker's reach must appear here with evidence.
CHECKER_EVIDENCE = {
    "tailwind-producers": _tailwind_producer_evidence,
    "admin-link-scan": _admin_link_scan_evidence,
    "async-task-name-scan": _async_task_scan_evidence,
    "emailservice-inventory-scan": _emailservice_scan_evidence,
    "tailwind-source-scan": _tailwind_source_scan_evidence,
}

def _enumerated_files():
    """Checkers that expose the set of files they read.

    The declared row has to cover every one of them -- this is what catches a
    row that is narrower than its checker without anyone having to think of
    the right probe path.
    """
    return {
        "tailwind-producers": [
            _relative(path) for path in verify_tailwind_build.producer_paths()
        ],
        "admin-link-scan": [
            relative.as_posix() for relative, _absolute in test_admin_links._scanned_files()
        ],
        "async-task-name-scan": [
            _relative(path) for path in test_async_task_names._iter_python_files(REPO_ROOT)
        ],
        "repo-wide-template-lints": [
            _relative(path)
            for path in test_design_system_lint.discover_templates(REPO_ROOT / "templates")
        ],
        # Both trees, from the lint's own enumerator: this is what binds the
        # row's `email_app/email_templates/*.md` glob to the files the lint
        # really opens, rather than to a glob someone believed was right.
        "template-comment-lint": [
            _relative(path) for path in test_template_comment_lint.discover_all_sources(REPO_ROOT)
        ],
    }



@tag("core")
class RepoWideGuardEvidenceTest(SimpleTestCase):
    """A row may not be narrower than the checker it stands for."""

    def test_a_command_only_row_always_requires_evidence(self):
        # Its checker is not a Django test module, so the discovery scanner
        # cannot see it -- the checker's own enumerator is the only binding.
        # Keying the evidence rule on the presence of an `excludes` key left
        # exactly this row exempt, and it was the narrowest one in the table.
        for guard in REPO_WIDE_GUARDS:
            if not guard.labels:
                with self.subTest(row=guard.name):
                    self.assertIn(guard.name, narrowing_rows())
                    self.assertIn(guard.name, CHECKER_EVIDENCE)

    def test_every_narrowing_row_has_checker_evidence(self):
        self.assertEqual(
            narrowing_rows(),
            set(CHECKER_EVIDENCE),
            "a row is narrower than the tree its checker scans, with no evidence from the "
            "checker; declare the row wide instead, or add the constant/enumerator/predicate "
            "evidence here",
        )
        self.assertEqual(
            {guard.name for guard in REPO_WIDE_GUARDS if guard.excludes} - set(CHECKER_EVIDENCE),
            set(),
            "a row declares excludes with no evidence",
        )

    def test_each_declared_exclusion_matches_its_checker(self):
        for name, evidence in sorted(CHECKER_EVIDENCE.items()):
            with self.subTest(row=name):
                evidence(self)

    def test_no_enumerable_checker_reads_a_file_its_row_excludes(self):
        for name, files in sorted(_enumerated_files().items()):
            row = _row(name)
            declared_suffixes = {Path(glob).suffix for glob in row.globs}
            undeclared = UNDECLARED_ENUMERATED_SUFFIXES.get(name, set())
            self.assertTrue(files, f"{name}'s enumerator went vacuous")
            seen_suffixes = set()
            for relative in files:
                suffix = Path(relative).suffix
                seen_suffixes.add(suffix)
                if suffix in undeclared:
                    continue
                with self.subTest(row=name, path=relative):
                    self.assertIn(
                        suffix,
                        declared_suffixes,
                        f"{name} reads {relative}, a suffix the row does not declare",
                    )
                    self.assertTrue(
                        row.matches(relative),
                        f"{name} reads {relative} but the row's scan set excludes it. Widen the "
                        f"row in scripts/affected_tests.py -- exclusions belong to the checker.",
                    )
            self.assertEqual(
                seen_suffixes - declared_suffixes,
                undeclared,
                f"{name} reads suffixes that are neither declared nor recorded: "
                f"{sorted(seen_suffixes - declared_suffixes - undeclared)}",
            )

    def test_the_evidence_guard_catches_a_row_narrower_than_its_checker(self):
        """Mutation control for the guard itself.

        Re-declare ``admin-link-scan`` with the Tailwind lint's exclusion set --
        literally the shape of this table's first cut, which is how a checker
        that reads ``tests/**`` and ``playwright_tests/**`` came to be declared
        as skipping them. The guard has to notice, both through the probe set
        and through the checker's own enumerator.
        """
        narrowed = tuple(
            dataclasses.replace(guard, excludes=excluded_path_globs())
            if guard.name == "admin-link-scan"
            else guard
            for guard in REPO_WIDE_GUARDS
        )
        with mock.patch(f"{__name__}.REPO_WIDE_GUARDS", narrowed):
            self.assertIn("admin-link-scan", narrowing_rows())
            with self.assertRaises(AssertionError):
                _admin_link_scan_evidence(self)
        # And unpatched, the real table is clean.
        self.assertNotIn("admin-link-scan", narrowing_rows() - set(CHECKER_EVIDENCE))

    def test_probe_paths_reach_inside_every_excluded_region(self):
        # A control that only samples the easy middle of a tree cannot observe
        # a row that is narrower than its checker.
        probes = {probe for paths in TREE_PROBES.values() for probe in paths}
        for region in (
            "content/tests/",
            "tests/",
            "playwright_tests/",
            "/migrations/",
            "static/vendor/",
        ):
            with self.subTest(region=region):
                self.assertTrue(any(region in probe for probe in probes), region)

    def test_rows_group_labels_only_when_the_checkers_share_a_scan_set(self):
        # Every label in a multi-label row must be a discovered scanner of the
        # same tree with no exclusions of its own; the seven pure-`templates/`
        # lints are the only group that qualifies today. The comment lint is
        # deliberately outside it -- it also reads
        # `email_app/email_templates/*.md`, so grouping it here would declare
        # seven checkers as reading a tree they never open.
        grouped = [guard for guard in REPO_WIDE_GUARDS if len(guard.labels) > 1]
        self.assertEqual([guard.name for guard in grouped], ["repo-wide-template-lints"])
        row = _row("repo-wide-template-lints")
        self.assertEqual(row.globs, ("templates/*.html",))
        self.assertEqual(row.excludes, ())
        discovered = discover_repo_wide_scanners()
        for label in row.labels:
            module = f"{label.replace('.', '/')}.py"
            with self.subTest(label=label):
                trees = {tree for tree, _pattern, _cls in discovered[module]}
                self.assertEqual(trees, {"templates"})

    def test_every_declared_label_is_a_discovered_tree_scanner(self):
        discovered = discover_repo_wide_scanners()
        for guard in REPO_WIDE_GUARDS:
            for label in guard.labels:
                parts = label.split(".")
                module = f"{'/'.join(parts)}.py"
                if not (REPO_ROOT / module).is_file():
                    module = f"{'/'.join(parts[:-1])}.py"
                with self.subTest(label=label):
                    self.assertIn(
                        module,
                        discovered,
                        f"{label} is declared as a repo-wide guard but is not a tree scanner",
                    )


@tag("core")
class RepoWideGuardTest(SimpleTestCase):
    """The declarative rule-14 table and the plans it produces."""

    def test_every_declared_glob_still_matches_something_on_disk(self):
        tracked = tracked_and_untracked_files()
        # Globs derived from a checker constant describe a family the checker
        # WOULD scan if it existed -- there is no `widgets/` directory today,
        # and that is not map rot. They are checked per row instead of per
        # glob, so the row as a whole still has to be alive.
        derived = set(TAILWIND_PRODUCER_GLOBS)
        for guard in REPO_WIDE_GUARDS:
            alive = False
            for glob in guard.globs:
                matched = any(fnmatch.fnmatchcase(path, glob) for path in tracked)
                alive = alive or matched
                if glob in derived:
                    continue
                with self.subTest(guard=guard.name, glob=glob):
                    self.assertTrue(matched, f"{glob} matches nothing on disk")
            with self.subTest(guard=guard.name):
                self.assertTrue(alive, f"{guard.name} matches nothing on disk at all")

    def test_every_declared_label_is_a_real_target(self):
        for guard in REPO_WIDE_GUARDS:
            for label in guard.labels:
                with self.subTest(guard=guard.name, label=label):
                    parts = label.split(".")
                    module = REPO_ROOT / Path(*parts).with_suffix(".py")
                    class_name = None
                    if not module.is_file():
                        class_name = parts[-1]
                        module = REPO_ROOT / Path(*parts[:-1]).with_suffix(".py")
                    self.assertTrue(module.is_file(), f"{label} has no module on disk")
                    if class_name is not None:
                        self.assertIn(
                            f"class {class_name}(",
                            module.read_text(encoding="utf-8"),
                            f"{label} names a class that no longer exists",
                        )

    def test_every_declared_command_is_a_real_make_target(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        for guard in REPO_WIDE_GUARDS:
            for command in guard.commands:
                with self.subTest(guard=guard.name, command=command):
                    self.assertTrue(command.startswith("make "))
                    self.assertRegex(makefile, rf"(?m)^{re.escape(command[len('make '):])}:")

    def test_every_entry_is_reachable_from_a_representative_path(self):
        samples = {
            "tailwind-producers": ("content/models/project.py", "studio/views/events.py"),
            "repo-wide-template-lints": (
                "templates/plans/my_plan_detail.html",
                "templates/community_base/public/base.html",
                "templates/includes/header.html",
            ),
            "template-comment-lint": (
                "templates/plans/my_plan_detail.html",
                "email_app/email_templates/welcome.md",
                "email_app/email_templates/event_registration_confirmation.md",
            ),
            "tailwind-source-scan": (
                "events/views/detail.py",
                "templates/plans/my_plan_detail.html",
                "static/js/video.js",
            ),
            "admin-link-scan": ("events/views/detail.py", "tests/test_probe_1755.py", "templates/x/y.html"),
            "legacy-template-reference-scan": (
                "events/views/detail.py",
                "playwright_tests/test_probe_1755.py",
                "static/vendor/probe_1755.js",
            ),
            "async-task-name-scan": ("events/views/detail.py", "playwright_tests/test_probe_1755.py"),
            "emailservice-inventory-scan": ("events/views/detail.py", "events/migrations/0042_probe.py"),
            "package-mail-boundary-scan": ("events/views/detail.py", "tests/test_probe_1755.py"),
        }
        self.assertEqual(sorted(samples), sorted(guard.name for guard in REPO_WIDE_GUARDS))
        for guard in REPO_WIDE_GUARDS:
            for sample in samples[guard.name]:
                plan = plan_for([sample])
                with self.subTest(guard=guard.name, sample=sample):
                    self.assertTrue(guard.matches(sample), f"{sample} is not in {guard.name}'s scan set")
                    for label in guard.labels:
                        self.assertTrue(
                            _guard_label_is_selected(label, None, plan.django_labels)
                            or any(existing.startswith(f"{label}.") for existing in plan.django_labels),
                            f"{sample} does not select {label}: {plan.django_labels}",
                        )
                    for command in guard.commands:
                        self.assertIn(command, plan.extra_commands)

    def test_the_plan_says_where_the_tree_scanning_labels_came_from(self):
        # An app-owned template edit goes from 1 label to 12 with nothing in
        # the output explaining it -- that is the case a developer would most
        # want explained, and it is the one that used to print no note at all.
        plan = plan_for(["templates/plans/my_plan_detail.html"])
        note = next(note for note in plan.notes if note.startswith("NOTE repo-wide-guards:"))
        self.assertIn("rule 14", note)
        self.assertIn(str(len(TEMPLATE_GUARD_LABELS)), note)
        self.assertIn("REPO_WIDE_GUARDS in scripts/affected_tests.py", note)
        self.assertLess(len(note), 200, "the note must not restate what the command already shows")

        # Extra commands are named, since they are not Django labels.
        js_note = next(
            note
            for note in plan_for(["static/js/video.js"]).notes
            if note.startswith("NOTE repo-wide-guards:")
        )
        self.assertIn(CHECK_TAILWIND_COMMAND, js_note)

        # Nothing to explain, nothing printed.
        self.assertEqual(
            [note for note in plan_for(["_docs/audits/x.md"]).notes if "repo-wide-guards" in note],
            [],
        )

    def test_template_guards_are_selected_by_label_not_by_the_core_tag(self):
        # Reachability through `make test-core` does not count: four of the
        # repo-wide template lints are not core-tagged, and the tagged ones
        # could lose the tag in an unrelated commit.
        plan = plan_for(["templates/plans/my_plan_detail.html"])
        self.assertNotIn(CORE_COMMAND, plan.extra_commands)
        for label in TEMPLATE_GUARD_LABELS:
            with self.subTest(label=label):
                self.assertTrue(
                    _guard_label_is_selected(label, None, plan.django_labels),
                    plan.django_labels,
                )

    def test_the_tailwind_source_scan_stops_at_its_own_exclusions(self):
        # This row is the only one entitled to the Tailwind exclusion set, and
        # it is imported from the lint's policy module rather than restated.
        label = "tests.test_tailwind_build.TailwindSourceScanTest"
        for path in (
            "content/tests/test_articles.py",
            "tests/test_health_check.py",
            "playwright_tests/test_dashboard.py",
            "events/migrations/0042_add_field.py",
        ):
            with self.subTest(path=path):
                self.assertNotIn(label, plan_for([path]).django_labels)
        self.assertIn(label, plan_for(["events/views/detail.py"]).django_labels)

    def test_test_files_still_select_the_checkers_that_read_them(self):
        # The other half of the same coin: three checkers DO read test trees,
        # so a test-file edit has to select them.
        for path in ("content/tests/test_articles.py", "tests/test_health_check.py"):
            with self.subTest(path=path):
                labels = plan_for([path]).django_labels
                for label in (
                    "studio.tests.test_admin_links",
                    "tests.test_unreachable_legacy_templates_1543",
                ):
                    self.assertIn(label, labels)

    def test_vendored_javascript_selects_only_the_repo_root_reference_scan(self):
        # static/vendor is outside the Tailwind lint's scan set but inside
        # test_unreachable_legacy_templates_1543's repo-root rglob.
        self.assertEqual(
            plan_for(["static/vendor/mermaid.min.js"]).django_labels,
            ["tests.test_unreachable_legacy_templates_1543"],
        )

    def test_producer_mapping_is_derived_from_the_checker(self):
        # The plan follows scripts/verify_tailwind_build.py's PRODUCER_FILES
        # with no second copy of the list here -- add a producer there and this
        # test covers it without being edited.
        for producer in PRODUCER_FILES:
            relative = Path(producer).resolve().relative_to(REPO_ROOT).as_posix()
            with self.subTest(producer=relative):
                self.assertIn(relative, TAILWIND_PRODUCER_GLOBS)
                self.assertIn(CHECK_TAILWIND_COMMAND, plan_for([relative]).extra_commands)
        # PRODUCER_FILES is one of four families. Iterating only that list is
        # how `studio/forms/*.py` and `static/js/*.js` went unnoticed, so drive
        # this off everything the checker actually reads.
        for path in verify_tailwind_build.producer_paths():
            relative = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
            with self.subTest(producer=relative):
                self.assertIn(CHECK_TAILWIND_COMMAND, plan_for([relative]).extra_commands)
        self.assertEqual(
            producer_globs([REPO_ROOT / "content/models/brand_new_producer.py"]),
            ("content/models/brand_new_producer.py",),
        )

    def test_the_producer_list_is_not_duplicated_in_the_helper(self):
        # The table holds exactly the derived list plus the one directory
        # family the checker adds in code (``_producer_classes()``), so there
        # is no second copy of the file list to drift.
        self.assertEqual(
            TAILWIND_PRODUCER_GLOBS,
            (
                *producer_globs(PRODUCER_FILES),
                f"{verify_tailwind_build.PRODUCER_VIEW_FAMILY}/*.py",
                *(f"*/{family}/*.py" for family in verify_tailwind_build.PRODUCER_PART_FAMILIES),
                *(f"{family}/*.py" for family in verify_tailwind_build.PRODUCER_PART_FAMILIES),
                f"{verify_tailwind_build.PRODUCER_JS_FAMILY}/*.js",
                "scripts/verify_tailwind_build.py",
            ),
            "every producer family must be derived from the checker's own constants",
        )
        producer_entry = next(guard for guard in REPO_WIDE_GUARDS if guard.name == "tailwind-producers")
        self.assertEqual(producer_entry.globs, TAILWIND_PRODUCER_GLOBS)
        derived = set(producer_globs(PRODUCER_FILES))
        for guard in REPO_WIDE_GUARDS:
            if guard.name == "tailwind-producers":
                continue
            with self.subTest(guard=guard.name):
                self.assertEqual(derived & set(guard.globs), set())

    def test_producer_edit_keeps_its_owning_app_and_expansion(self):
        # Rule 14 supplements; it must never consume the path.
        plan = plan_for(["content/models/project.py"], {"content.models.project": ["studio/views/projects.py"]})
        self.assertIn("content", plan.django_labels)
        self.assertIn("studio", plan.django_labels)
        self.assertIn(CHECK_TAILWIND_COMMAND, plan.extra_commands)

    def test_unmapped_paths_still_fail_closed(self):
        # Rule 13 is unchanged by rule 14.
        plan = plan_for(["weird/unknown.txt"])
        self.assertEqual(plan.unmapped, ["weird/unknown.txt"])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(plan.django_labels, [])

    def test_the_cli_option_set_is_exactly_the_documented_four(self):
        parser = build_arg_parser()
        options = sorted(option for action in parser._actions for option in action.option_strings)
        self.assertEqual(
            options,
            ["--base", "--help", "--include-untracked", "--json", "--no-include-untracked", "--run", "-h"],
        )

    def test_no_environment_variable_can_narrow_a_guard(self):
        probes = ["templates/plans/my_plan_detail.html", "content/models/project.py", "static/js/video.js"]
        baseline = plan_for(probes).to_dict()
        with mock.patch.dict(
            os.environ,
            {
                "AFFECTED_TESTS_SKIP_GUARDS": "1",
                "SKIP_REPO_WIDE_GUARDS": "tailwind-producers",
                "AFFECTED_TESTS_ALLOWLIST": "templates/*",
                "AFFECTED_TESTS_DISABLE": "repo-wide-template-lints",
            },
        ):
            self.assertEqual(plan_for(probes).to_dict(), baseline)

    def test_no_guard_declares_its_own_suppression_surface(self):
        # A guard is (globs, labels, commands, excludes) and nothing else: no
        # "enabled" switch, no "known gaps" list to opt out of.
        self.assertEqual(
            sorted(field.name for field in dataclasses.fields(REPO_WIDE_GUARDS[0])),
            ["checker", "commands", "excludes", "globs", "labels", "name"],
        )
        for guard in REPO_WIDE_GUARDS:
            with self.subTest(guard=guard.name):
                self.assertTrue(guard.labels or guard.commands, "a guard that selects nothing is a hole")

    def test_plan_construction_stays_fast_on_a_multi_file_diff(self):
        # The helper's whole premise is that it costs nothing to run. Bound is
        # generous because this box runs several agents at once.
        files = [f"{app}/views/page_{index}.py" for index, app in enumerate(APP_LABELS)]
        files += [f"templates/{app}/page.html" for app in APP_LABELS[:5]]
        started = time.perf_counter()
        build_plan(files)
        self.assertLess(time.perf_counter() - started, 2.0)


@tag("core")
class RepoWideGuardRegressionTest(SimpleTestCase):
    """One pin per recorded instance of the map/checker drift (#1755)."""

    def test_producer_docstring_edit_emits_the_tailwind_gate(self):
        """Origin: 6a489fdc / bdbb65a5 -- docstring prose that reads as a
        Tailwind class in a PRODUCER_FILES module. Deploy Gates failed with
        `compiled CSS is missing producer selectors`; the local plan selected
        only `content` and could not have caught it."""
        plan = plan_for(["content/models/project.py"])
        self.assertIn("content", plan.django_labels)
        self.assertEqual(plan.extra_commands, [CHECK_TAILWIND_COMMAND])

    def test_studio_view_edit_emits_the_tailwind_gate(self):
        """Origin: 6a489fdc / bdbb65a5 -- `_producer_classes()` also scans the
        `studio/views/*.py` directory family, which is not in PRODUCER_FILES."""
        plan = plan_for(["studio/views/events.py"])
        self.assertIn("studio", plan.django_labels)
        self.assertIn(CHECK_TAILWIND_COMMAND, plan.extra_commands)
        self.assertEqual(plan.unmapped, [])

    def test_skill_markdown_selects_the_tests_package(self):
        """Origin: #1735 -- `.agents/skills/**` fell through unmapped to a
        `WARN unmapped:` line plus `make test-core`, while the skill content
        guards live in `tests/`."""
        plan = plan_for([".agents/skills/ai-shipping-labs-users/SKILL.md"])
        self.assertEqual(plan.django_labels, [TESTS_PACKAGE])
        self.assertEqual(plan.unmapped, [])
        self.assertEqual(plan.notes, [])

    def test_shared_community_base_template_selects_every_template_guard(self):
        """Origin: #1758 -- a `templates/community_base/**` change selected 5 of
        the 9 template lints (and only through `make test-core`), so the tester
        had to run four lints out-of-plan to find out whether it was safe."""
        plan = plan_for(["templates/community_base/public/base.html"])
        for label in TEMPLATE_GUARD_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, plan.django_labels)
        note = next(note for note in plan.notes if note.startswith("NOTE template-fallback:"))
        for label in TEMPLATE_GUARD_LABELS:
            self.assertIn(label, note)

    def test_every_producer_family_emits_the_tailwind_gate(self):
        """Origin: PM acceptance on #1755 -- `_producer_classes()` reads four
        families (PRODUCER_FILES, `studio/views`, any `forms`/`widgets`
        directory, first-party `static/js`) and the first cut of this row
        declared two. `studio/forms/call_profiles.py` and
        `static/js/event-widget.js` produced plans with no `make check-tailwind`:
        the exact shape of 6a489fdc / bdbb65a5, one file family over."""
        for path in (
            "content/models/project.py",
            "studio/views/events.py",
            "studio/forms/call_profiles.py",
            "accounts/widgets/tier_picker.py",
            "static/js/event-widget.js",
            "scripts/verify_tailwind_build.py",
        ):
            with self.subTest(path=path):
                self.assertIn(CHECK_TAILWIND_COMMAND, plan_for([path]).extra_commands)
        for path in ("events/views/detail.py", "static/vendor/mermaid.min.js"):
            with self.subTest(path=path):
                self.assertNotIn(CHECK_TAILWIND_COMMAND, plan_for([path]).extra_commands)

    def test_first_party_javascript_selects_the_source_scan_guards(self):
        """Origin: found during grooming -- `static/**` selected zero Django
        labels, so a Tailwind-shaped `${}` fragment in first-party JS reached
        main with no local gate."""
        plan = plan_for(["static/js/video.js"])
        self.assertEqual(plan.django_labels, collapsed(*JAVASCRIPT_GUARD_LABELS))
        self.assertIsNotNone(plan.django_command)


@tag("core")
class AgentTreeSpellingTest(SimpleTestCase):
    """`.claude/skills` is a symlink to `.agents/skills` (#1735)."""

    SUFFIXES = (
        "skills/ai-shipping-labs-users/SKILL.md",
        "skills/ai-shipping-labs-events/SKILL.md",
        "skills/ai-shipping-labs-event-recaps/SKILL.md",
        "skills/execute/SKILL.md",
        "agents/software-engineer.md",
    )

    def test_both_spellings_produce_identical_plans(self):
        for suffix in self.SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    plan_for([f".agents/{suffix}"]).to_dict() | {"files": []},
                    plan_for([f".claude/{suffix}"]).to_dict() | {"files": []},
                )

    def test_focused_skill_contracts_still_win_over_the_broad_rule(self):
        for spelling in (".agents", ".claude"):
            for skill in ("ai-shipping-labs-events", "ai-shipping-labs-event-recaps"):
                with self.subTest(spelling=spelling, skill=skill):
                    plan = plan_for([f"{spelling}/skills/{skill}/SKILL.md"])
                    self.assertEqual(plan.django_labels, ["api.tests.test_events.EventsSkillDocSyncTest"])


@tag("core")
class ContractPathTest(SimpleTestCase):
    def test_focused_contracts_target_exact_django_test_modules(self):
        for glob, labels in FOCUSED_CONTRACT_PATHS:
            with self.subTest(glob=glob):
                self.assertTrue(labels)
                self.assertTrue(
                    all(
                        label.startswith("tests.test_")
                        or (label.split(".")[0] in APP_LABELS and ".tests.test_" in label)
                        for label in labels
                    )
                )

    def test_every_contract_label_is_a_real_target(self):
        known = set(APP_LABELS) | {TESTS_PACKAGE, "website"}
        for glob, labels in CONTRACT_PATHS:
            self.assertTrue(labels, f"{glob} maps to no labels")
            for label in labels:
                with self.subTest(glob=glob, label=label):
                    self.assertIn(label, known)

    def test_every_contract_doc_artifact_still_exists(self):
        # Only the doc-artifact half: CI surfaces like Procfile.dev are
        # covered by their own repo conventions, and globs are checked as dirs.
        for glob, _labels in CONTRACT_PATHS:
            if not glob.startswith(("_docs/", "docs/", "specs/", "skills/")):
                continue
            target = REPO_ROOT / (glob[:-2] if glob.endswith("/*") else glob)
            with self.subTest(glob=glob):
                self.assertTrue(target.exists(), f"{glob} no longer exists")


#: Command shapes that tell an agent to run everything. ``make test-core``,
#: ``make test-affected`` and ``make test-playwright-core`` are fine, so
#: ``make test`` only matches when not followed by a hyphen.
FULL_SUITE_COMMAND = re.compile(
    r"""
      uv\s+run\s+python\s+manage\.py\s+test\s*(?:--parallel|["'`)\n]|$)  # no labels
    | uv\s+run\s+python\s+-m\s+pytest\s+playwright_tests/
    | uv\s+run\s+pytest\s+playwright_tests/\s+-v
    | make\s+test-all
    | make\s+coverage
    | make\s+test(?![-\w])
    """,
    re.VERBOSE,
)

#: A line that forbids, defers, or labels a command as a CI gate is allowed to
#: name it. Only lines that read as an instruction to run it are failures.
#: The check is line-based, so keep a prohibition and the command it forbids on
#: the same line -- wrapping "Do not run ... (`make coverage`)" across two lines
#: reads as an instruction to the guard (and, arguably, to a skimming agent).
COMMAND_IS_PROHIBITED = re.compile(
    # "CI runs the full suite, so always run `make test` before pushing" is
    # exactly the README line this issue removed, so "CI runs" is NOT a
    # prohibition marker.
    r"do not|don't|never|no labels|CI-only|CI gate|exhaustive|"
    r"deferred|unless Alexey|not a substitute|not part of the local",
    re.IGNORECASE,
)

#: Files whose text is an instruction an agent will follow.
AGENT_FACING_FILES = (
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "_docs/PROCESS.md",
    ".claude/agents/software-engineer.md",
    ".claude/agents/tester.md",
    ".claude/agents/oncall-engineer.md",
    ".claude/agents/product-manager.md",
    ".claude/agents/designer.md",
    ".claude/skills/execute/SKILL.md",
)


def full_suite_instructions(text: str) -> list[str]:
    """Lines that instruct an agent to run a full suite, prohibitions aside."""
    offenders = []
    for line in text.splitlines():
        if COMMAND_IS_PROHIBITED.search(line):
            continue
        if FULL_SUITE_COMMAND.search(line):
            offenders.append(line.strip())
    return offenders


@tag("core")
class FullSuiteInstructionGuardTest(SimpleTestCase):
    """No agent-facing file may tell an agent to run everything.

    Issue #1468's root cause was exactly this: the tool was fine, but half a
    dozen prose instructions still said "run ALL tests". An inline Task prompt
    (``.claude/skills/execute/SKILL.md``) beats the agent-definition file it
    tells the subagent to read, so the skill has to be pinned too.
    """

    def test_no_agent_facing_file_instructs_a_full_suite_run(self):
        for relative in AGENT_FACING_FILES:
            path = REPO_ROOT / relative
            with self.subTest(file=relative):
                self.assertTrue(path.is_file(), f"{relative} is missing")
                offenders = full_suite_instructions(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    offenders,
                    [],
                    f"{relative} instructs a full-suite run; point it at "
                    f"`make test-affected` instead:\n  " + "\n  ".join(offenders),
                )

    def test_the_guard_would_catch_the_original_instructions(self):
        # The exact strings this issue removed -- proves the regex is not vacuous.
        for original in (
            "uv run python manage.py test --parallel     # Django tests (~1 min)",
            "uv run python -m pytest playwright_tests/   # E2E tests",
            "run ALL tests (uv run python manage.py test AND uv run pytest playwright_tests/ -v)",
            "CI runs the full suite, so always run `make test` before pushing.",
            "make test-all",
        ):
            with self.subTest(original=original):
                self.assertTrue(full_suite_instructions(original), original)

    def test_the_guard_allows_scoped_and_prohibitive_lines(self):
        for allowed in (
            "uv run python manage.py test {touched_app} --parallel 4",
            "make test-core",
            "make test-affected",
            "make test-playwright-core",
            "uv run pytest playwright_tests/test_dashboard.py -v",
            "- Do NOT run the full Django suite locally (`make test`, `make test-all`).",
            "make test            # CI gate: full Django unit/integration suite",
        ):
            with self.subTest(allowed=allowed):
                self.assertEqual(full_suite_instructions(allowed), [])


@tag("core")
class RotGuardTest(SimpleTestCase):
    def test_app_labels_match_installed_apps(self):
        project_apps = {config.label for config in apps.get_app_configs() if Path(config.path).parent == REPO_ROOT}
        self.assertEqual(set(APP_LABELS), project_apps)

    def test_makefile_exposes_test_affected(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        self.assertRegex(makefile, r"(?m)^test-affected:")
        self.assertIn("uv run python scripts/affected_tests.py --run", makefile)
        self.assertRegex(makefile, r"(?m)^\.PHONY:.*\btest-affected\b")

    def test_test_core_target_uses_bounded_parallelism(self):
        # make test-affected emits `make test-core` on its fail-closed and
        # hub-module paths, so the --parallel 4 guarantee has to hold there.
        makefile = (REPO_ROOT / "Makefile").read_text()
        recipe = re.search(r"(?m)^test-core:\n\t(.+)$", makefile)
        self.assertIsNotNone(recipe, "test-core target not found")
        self.assertIn("--parallel 4", recipe.group(1))

    def test_root_agent_instructions_delegate_to_the_canonical_helper_policy(self):
        # Claude loads the bootstrap while Codex-style agents load AGENTS.md
        # directly. Pin both halves of that explicit one-hop contract without
        # duplicating the canonical testing policy back into CLAUDE.md.
        claude_text = (REPO_ROOT / "CLAUDE.md").read_text()
        agents_text = (REPO_ROOT / "AGENTS.md").read_text()
        delegation_directives = [line.strip() for line in claude_text.splitlines() if line.lstrip().startswith("@")]
        self.assertEqual(
            delegation_directives,
            ["@AGENTS.md"],
            "CLAUDE.md must delegate exactly once to the root AGENTS.md",
        )
        self.assertIn("make test-affected", agents_text)
        self.assertIn("scripts/affected_tests.py", agents_text)

    def test_software_engineer_agent_does_not_mandate_the_full_local_suite(self):
        text = (REPO_ROOT / ".claude" / "agents" / "software-engineer.md").read_text()
        self.assertIn("make test-affected", text)
        self.assertNotIn("uv run python manage.py test --parallel", text)
        self.assertIn("Affected-tests plan:", text)

    def test_tester_agent_requires_the_helper(self):
        text = (REPO_ROOT / ".claude" / "agents" / "tester.md").read_text()
        self.assertIn("scripts/affected_tests.py", text)
        self.assertIn("make test-affected", text)

    def test_oncall_agent_uses_bounded_parallelism_and_scoped_playwright(self):
        text = (REPO_ROOT / ".claude" / "agents" / "oncall-engineer.md").read_text()
        self.assertIn("make test-affected", text)
        self.assertIn("--parallel 4", text)
        self.assertNotIn("uv run python manage.py test {touched_app} --parallel\n", text)

    def test_execute_skill_qa_prompt_uses_the_helper(self):
        # This inline Task prompt dominates tester.md, which it tells the
        # subagent to read -- so it has to carry the same instruction.
        text = (REPO_ROOT / ".claude" / "skills" / "execute" / "SKILL.md").read_text()
        self.assertIn("make test-affected", text)
        self.assertIn("scripts/affected_tests.py", text)
        self.assertNotIn("run ALL tests", text)

    def test_readme_points_at_the_affected_tests_helper(self):
        text = (REPO_ROOT / "README.md").read_text()
        self.assertIn("make test-affected", text)
        self.assertIn("scripts/affected_tests.py", text)
        self.assertNotIn("always run `make test` before pushing", text)

    def test_docs_document_the_helper(self):
        process = (REPO_ROOT / "_docs" / "PROCESS.md").read_text()
        self.assertIn("make test-affected", process)
        guidelines = (REPO_ROOT / "_docs" / "testing-guidelines.md").read_text()
        self.assertIn("## Affected-tests selection (`make test-affected`)", guidelines)
