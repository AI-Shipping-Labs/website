"""Self-tests for ``scripts/affected_tests.py`` (issue #1468).

The helper decides which tests an agent runs locally, so a silent regression
here would either re-create the 14,800-test local full-suite run it exists to
prevent, or -- worse -- silently drop the tests that cover a change. These
tests pin the ordered rule chain, the curated hub map, the reverse-import
expansion and its cap, the escalation table, and the fail-closed behaviour for
unmapped paths.

They also act as rot guards: the curated map keys must still exist on disk,
``APP_LABELS`` must still match ``INSTALLED_APPS``, and the agent definitions
must still point at ``make test-affected`` rather than the full local suite.
"""

import contextlib
import io
import json
import re
import subprocess
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase, tag

from scripts.affected_tests import (
    APP_LABELS,
    CONTRACT_PATHS,
    CORE_COMMAND,
    DJANGO_COMMAND_SUFFIX,
    ESCALATION_TRIGGERS,
    FOCUSED_CONTRACT_PATHS,
    HUB_MODULE_MAP,
    PLAYWRIGHT_CORE_COMMAND,
    PLAYWRIGHT_FULL_COMMAND,
    REVERSE_IMPORT_APP_CAP,
    TESTS_PACKAGE,
    Plan,
    _parent_reexports,
    build_plan,
    git_grep_references,
    is_no_test_path,
    reverse_import_patterns,
    run_commands,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Real ``git grep`` output for ``integrations/services/zoom.py`` at the time
#: this test was written. Used to pin the label mapping without making the
#: assertion depend on the current consumer set (the live grep is exercised
#: separately in ``ReverseImportGrepTest``).
ZOOM_REFERENCES = [
    'api/tests/test_event_zoom_sync.py',
    'api/views/events.py',
    'events/services/zoom_lifecycle.py',
    'events/tests/test_zoom_lifecycle.py',
    'integrations/tests/test_zoom.py',
    'integrations/views/zoom_webhook.py',
    'jobs/tasks/recording_upload.py',
    'playwright_tests/test_studio_series_create_zoom_859.py',
    'studio/views/events.py',
]


def plan_for(files, references=None):
    """Build a plan with a canned reverse-import expansion."""
    lookup = references or {}
    return build_plan(files, expander=lambda modules: {m: lookup.get(m, []) for m in modules})


@tag('core')
class RuleChainTest(SimpleTestCase):
    def test_app_source_targets_owning_app(self):
        plan = plan_for(['events/views/detail.py'])
        self.assertEqual(plan.django_labels, ['events'])
        self.assertEqual(
            plan.django_command,
            f'uv run python manage.py test events {DJANGO_COMMAND_SUFFIX}',
        )
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.playwright, 'core')

    def test_reverse_import_expansion_adds_consumer_apps(self):
        plan = plan_for(
            ['integrations/services/zoom.py'],
            {'integrations.services.zoom': ZOOM_REFERENCES},
        )
        self.assertEqual(plan.django_labels, ['api', 'events', 'integrations', 'jobs', 'studio'])
        self.assertNotIn(CORE_COMMAND, plan.extra_commands)

    def test_reverse_import_expansion_over_cap_substitutes_core(self):
        too_many = [f'{label}/views/thing.py' for label in APP_LABELS[:REVERSE_IMPORT_APP_CAP + 2]]
        plan = plan_for(['content/models/article.py'], {'content.models.article': too_many})
        self.assertEqual(plan.django_labels, ['content'], 'the owning app must survive the cap')
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(
            any(note.startswith('NOTE broad-impact:') for note in plan.notes),
            plan.notes,
        )

    def test_reverse_import_expansion_at_cap_is_not_substituted(self):
        at_cap = [f'{label}/views/thing.py' for label in APP_LABELS[:REVERSE_IMPORT_APP_CAP]]
        plan = plan_for(['content/models/article.py'], {'content.models.article': at_cap})
        self.assertNotIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(len(plan.django_labels), REVERSE_IMPORT_APP_CAP + 1)

    def test_reverse_import_hits_in_tests_package_target_the_test_module(self):
        plan = plan_for(
            ['voting/services/tally.py'],
            {'voting.services.tally': ['tests/test_health_check.py']},
        )
        self.assertEqual(plan.django_labels, ['tests.test_health_check', 'voting'])

    def test_playwright_references_are_a_note_not_an_escalation(self):
        plan = plan_for(
            ['integrations/services/zoom.py'],
            {'integrations.services.zoom': ZOOM_REFERENCES},
        )
        self.assertEqual(plan.playwright, 'core')
        self.assertTrue(any(note.startswith('NOTE playwright-refs:') for note in plan.notes))

    def test_docs_only_diff_requires_no_tests(self):
        plan = plan_for(['_docs/configuration.md', 'specs/04-content-articles.md',
                         '_docs/audits/2026-01-01-x.md'])
        self.assertTrue(plan.no_tests_required)
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.commands(), [])
        self.assertEqual(plan.playwright, 'none')

    def test_docs_do_not_suppress_code_in_the_same_diff(self):
        plan = plan_for(['_docs/configuration.md', 'voting/models/poll.py'])
        self.assertEqual(plan.django_labels, ['voting'])
        self.assertFalse(plan.no_tests_required)

    def test_agent_and_skill_markdown_is_not_a_no_test_path(self):
        # tests/ asserts the content of these files, so they map to `tests`.
        self.assertFalse(is_no_test_path('.claude/agents/software-engineer.md'))
        self.assertFalse(is_no_test_path('.claude/skills/x/SKILL.md'))
        self.assertTrue(is_no_test_path('_docs/configuration.md'))
        plan = plan_for(['.claude/agents/tester.md'])
        self.assertEqual(plan.django_labels, ['tests'])

    def test_only_unguarded_docs_and_top_level_markdown_short_circuit(self):
        for path in ('_docs/configuration.md', '_docs/audits/2026-01-01-x.md',
                     'specs/04-content-articles.md'):
            with self.subTest(path=path):
                self.assertTrue(is_no_test_path(path))
        for path in ('email_app/email_templates/welcome.md',
                     'integrations/services/ai_eval/README.md',
                     'asl_cli/README.md',
                     'skills/ai-shipping-labs-member-api/SKILL.md',
                     'docs/member-api/plans.md',
                     'CLAUDE.md',
                     'AGENTS.md',
                     'README.md',
                     '_docs/design-system.md'):
            with self.subTest(path=path):
                self.assertFalse(is_no_test_path(path))

    def test_email_template_markdown_maps_to_its_app(self):
        # email_app/email_templates/*.md are shipped email bodies asserted by
        # email_app tests -- an email-template-only diff must not report
        # "NO TESTS REQUIRED".
        plan = plan_for(['email_app/email_templates/welcome_paid.md'])
        self.assertFalse(plan.no_tests_required)
        self.assertEqual(plan.django_labels, ['email_app'])

    def test_readme_inside_an_app_maps_to_its_app(self):
        plan = plan_for(['integrations/services/ai_eval/README.md'])
        self.assertEqual(plan.django_labels, ['integrations'])

    def test_skills_markdown_maps_to_member_api(self):
        # member_api/tests/test_usage_docs_1112.py reads
        # skills/ai-shipping-labs-member-api/{SKILL,README,plans,books}.md.
        # The Playwright test over the same artifact is `local_only`, so the
        # core Playwright subset does NOT cover it -- the Django label is the
        # only real guard.
        for path in (
            'skills/ai-shipping-labs-member-api/SKILL.md',
            'skills/ai-shipping-labs-member-api/books.md',
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertFalse(plan.no_tests_required)
                self.assertEqual(plan.django_labels, ['member_api'])

    def test_member_api_guide_maps_to_member_api(self):
        plan = plan_for(['docs/member-api/plans.md'])
        self.assertEqual(plan.django_labels, ['member_api'])

    def test_agent_instruction_files_map_to_the_tests_package(self):
        # CLAUDE.md, AGENTS.md, _docs/PROCESS.md and
        # _docs/testing-guidelines.md all have rot guards in RotGuardTest
        # below, which lives in tests/. A diff touching only one of them must
        # therefore run `tests`, not print NO TESTS REQUIRED.
        for path in ('CLAUDE.md', 'AGENTS.md', 'README.md', '_docs/PROCESS.md'):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertFalse(plan.no_tests_required)
                self.assertEqual(plan.django_labels, ['tests'])
        guideline_plan = plan_for(['_docs/testing-guidelines.md'])
        self.assertEqual(guideline_plan.django_labels, ['tests.test_affected_tests'])

    def test_guarded_doc_artifacts_map_to_their_reading_app(self):
        for path, expected in (
            ('_docs/design-system.md', ['accounts', 'content']),
            ('_docs/product.md', ['content']),
            ('_docs/content.md', ['content']),
            ('_docs/openapi.json', ['api']),
            ('_docs/member-openapi.json', ['member_api']),
            ('_docs/integrations/zoom.md', ['integrations']),
            ('specs/07-events.md', ['content']),
        ):
            with self.subTest(path=path):
                self.assertEqual(plan_for([path]).django_labels, expected)

    def test_ci_and_script_paths_target_the_tests_package(self):
        plan = plan_for(['.github/workflows/deploy-dev.yml', 'scripts/watch-ci.py', 'Makefile'])
        self.assertEqual(plan.django_labels, ['tests'])

    def test_playwright_owner_inventory_contracts_target_exact_policy_tests(self):
        plan = plan_for([
            'scripts/playwright_owner_inventory.py',
            'scripts/playwright_owner_inventory_ceilings.py',
            'tests/playwright_owner_inventory_live.json',
            'tests/test_playwright_owner_inventory.py',
        ])
        self.assertEqual(plan.django_labels, ['tests.test_playwright_owner_inventory'])
        self.assertEqual(plan.unmapped, [])

    def test_affected_test_tool_and_guideline_target_their_exact_contract(self):
        plan = plan_for(['scripts/affected_tests.py', '_docs/testing-guidelines.md'])
        self.assertEqual(plan.django_labels, ['tests.test_affected_tests'])

    def test_website_paths_add_tests_website_and_core(self):
        plan = plan_for(['website/urls.py'])
        self.assertEqual(plan.django_labels, ['tests', 'website'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(plan.playwright, 'full')

    def test_dependency_manifest_is_a_soft_core_fallback(self):
        plan = plan_for(['uv.lock', 'pyproject.toml'])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        self.assertEqual(plan.playwright, 'core', 'manifests must not force full Playwright')
        self.assertTrue(any(note.startswith('NOTE dependency-manifest:') for note in plan.notes))

    def test_test_file_maps_to_its_exact_module_not_the_whole_app(self):
        plan = plan_for(['studio/tests/test_events.py'])
        self.assertEqual(plan.django_labels, ['studio.tests.test_events'])

    def test_test_helper_maps_to_the_app_tests_package(self):
        plan = plan_for(['studio/tests/factories.py'])
        self.assertEqual(plan.django_labels, ['studio.tests'])

    def test_top_level_test_module_maps_to_its_dotted_path(self):
        plan = plan_for(['tests/test_robots_txt.py'])
        self.assertEqual(plan.django_labels, ['tests.test_robots_txt'])

    def test_playwright_test_file_runs_that_file_plus_core(self):
        plan = plan_for(['playwright_tests/test_dashboard.py'])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, ['uv run pytest playwright_tests/test_dashboard.py -v'])
        self.assertEqual(plan.commands()[-1], PLAYWRIGHT_CORE_COMMAND)

    def test_full_playwright_supersedes_per_file_playwright_commands(self):
        plan = plan_for([
            'playwright_tests/conftest.py',      # escalation trigger
            'playwright_tests/test_dashboard.py',
            'playwright_tests/test_newsletter.py',
        ])
        self.assertEqual(plan.playwright, 'full')
        self.assertEqual(
            [c for c in plan.extra_commands if c.startswith('uv run pytest playwright_tests/')],
            [],
            'per-file Playwright commands must not survive escalation to the full suite',
        )
        self.assertEqual(plan.commands(), [PLAYWRIGHT_FULL_COMMAND])
        self.assertTrue(
            any(note.startswith('NOTE playwright-full-supersedes:') for note in plan.notes),
            plan.notes,
        )

    def test_per_file_playwright_commands_survive_a_core_plan(self):
        plan = plan_for(['playwright_tests/test_dashboard.py', 'playwright_tests/test_newsletter.py'])
        self.assertEqual(plan.playwright, 'core')
        self.assertEqual(
            plan.extra_commands,
            [
                'uv run pytest playwright_tests/test_dashboard.py -v',
                'uv run pytest playwright_tests/test_newsletter.py -v',
            ],
        )

    def test_asl_cli_runs_plain_pytest(self):
        plan = plan_for(['asl_cli/asl_cli/cli.py'])
        self.assertEqual(plan.extra_commands, ['uv run pytest asl_cli/tests'])
        self.assertIsNone(plan.django_command)

    def test_app_template_targets_its_app_and_core_playwright(self):
        plan = plan_for(['templates/plans/my_plan_detail.html'])
        self.assertEqual(plan.django_labels, ['plans'])
        self.assertEqual(plan.playwright, 'core')
        self.assertIn(PLAYWRIGHT_CORE_COMMAND, plan.commands())

    def test_shared_template_fragment_adds_content_core_and_full_playwright(self):
        plan = plan_for(['templates/includes/header.html'])
        self.assertEqual(plan.django_labels, ['content'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertEqual(plan.playwright, 'full')

    def test_unowned_template_dir_falls_back_to_core(self):
        plan = plan_for(['templates/legal/terms.html'])
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        self.assertTrue(any(note.startswith('NOTE template-fallback:') for note in plan.notes))

    def test_static_assets_only_need_core_playwright(self):
        plan = plan_for(['static/js/video.js'])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [])
        self.assertEqual(plan.commands(), [PLAYWRIGHT_CORE_COMMAND])

    def test_tailwind_config_escalates_to_full_playwright(self):
        plan = plan_for(['tailwind.config.js'])
        self.assertEqual(plan.playwright, 'full')
        self.assertEqual(plan.commands(), [PLAYWRIGHT_FULL_COMMAND])

    def test_single_app_migration_targets_only_that_app(self):
        plan = plan_for(['events/migrations/0042_add_field.py'])
        self.assertEqual(plan.django_labels, ['events'])
        self.assertEqual(plan.extra_commands, [])

    def test_multi_app_migrations_add_core(self):
        plan = plan_for([
            'events/migrations/0042_add_field.py',
            'jobs/migrations/0007_add_field.py',
        ])
        self.assertEqual(plan.django_labels, ['events', 'jobs'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(any(note.startswith('NOTE multi-app-migration:') for note in plan.notes))

    def test_unmapped_path_fails_closed_with_a_warning(self):
        plan = plan_for(['weird/unknown.txt'])
        self.assertEqual(plan.unmapped, ['weird/unknown.txt'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)
        self.assertTrue(
            any(note.startswith('WARN unmapped: weird/unknown.txt') for note in plan.notes),
            plan.notes,
        )

    def test_labels_are_deduplicated_and_collapsed(self):
        plan = plan_for(['studio/tests/test_events.py', 'studio/views/events.py'])
        self.assertEqual(plan.django_labels, ['studio'])

    def test_empty_diff_requires_no_tests(self):
        plan = plan_for([])
        self.assertTrue(plan.no_tests_required)

    def test_django_command_uses_bounded_parallelism_and_excludes_slow_tags(self):
        plan = plan_for(['voting/models/poll.py'])
        self.assertIn('--parallel 4', plan.django_command)
        self.assertIn('--exclude-tag=visual_regression', plan.django_command)
        self.assertIn('--exclude-tag=postgres_migration', plan.django_command)


@tag('core')
class HubModuleMapTest(SimpleTestCase):
    def test_integrations_config_targets_integrations_tests_and_core(self):
        plan = plan_for(['integrations/config.py'])
        self.assertEqual(plan.django_labels, ['integrations', 'tests'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_settings_registry_targets_integrations_tests_and_core(self):
        plan = plan_for(['integrations/settings_registry.py'])
        self.assertEqual(plan.django_labels, ['integrations', 'tests'])
        self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_access_control_hubs_target_content_accounts_core_and_full_playwright(self):
        for path in ('content/access.py', 'content/tier_config.py', 'accounts/gating.py'):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(plan.django_labels, ['accounts', 'content'])
                self.assertIn(CORE_COMMAND, plan.extra_commands)
                self.assertEqual(plan.playwright, 'full')

    def test_shared_fixtures_target_core_and_full_playwright(self):
        plan = plan_for(['tests/fixtures.py'])
        self.assertIsNone(plan.django_command)
        self.assertEqual(plan.extra_commands, [CORE_COMMAND])
        self.assertEqual(plan.playwright, 'full')

    def test_payments_hubs_target_payments_accounts_api_and_full_playwright(self):
        for path in (
            'payments/tier_state.py',
            'payments/stripe_links.py',
            'payments/services/webhook_handlers.py',
            'payments/views/checkout.py',
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(plan.django_labels, ['accounts', 'api', 'payments'])
                self.assertEqual(plan.playwright, 'full')

    def test_payments_tests_do_not_escalate(self):
        plan = plan_for(['payments/tests/test_stripe_webhook_observability.py'])
        self.assertEqual(plan.playwright, 'core')

    def test_accounts_hubs_target_accounts_and_core(self):
        for path in (
            'accounts/models/user.py',
            'accounts/auth.py',
            'accounts/adapters.py',
            'accounts/signals.py',
        ):
            with self.subTest(path=path):
                plan = plan_for([path])
                self.assertEqual(plan.django_labels, ['accounts'])
                self.assertIn(CORE_COMMAND, plan.extra_commands)

    def test_every_curated_map_key_still_exists_on_disk(self):
        for glob, _labels, _core in HUB_MODULE_MAP:
            with self.subTest(glob=glob):
                if glob.endswith('/*'):
                    target = REPO_ROOT / glob[:-2]
                    self.assertTrue(target.is_dir(), f'{glob} no longer exists')
                else:
                    self.assertTrue((REPO_ROOT / glob).is_file(), f'{glob} no longer exists')

    def test_every_curated_map_label_is_a_real_target(self):
        known = set(APP_LABELS) | {'tests', 'website'}
        for glob, labels, _core in HUB_MODULE_MAP:
            for label in labels:
                with self.subTest(glob=glob, label=label):
                    self.assertIn(label, known)


@tag('core')
class EscalationTriggerTest(SimpleTestCase):
    def test_every_trigger_glob_escalates(self):
        samples = {
            'playwright_tests/conftest.py': 'playwright_tests/conftest.py',
            'tests/fixtures.py': 'tests/fixtures.py',
            'content/access.py': 'content/access.py',
            'content/tier_config.py': 'content/tier_config.py',
            'accounts/gating.py': 'accounts/gating.py',
            'playwright_tests/test_access_control.py': 'playwright_tests/test_access_control.py',
            'payments/tier_state.py': 'payments/tier_state.py',
            'payments/stripe_links.py': 'payments/stripe_links.py',
            'payments/services/*': 'payments/services/webhook_handlers.py',
            'payments/views/*': 'payments/views/checkout.py',
            'templates/includes/*': 'templates/includes/header.html',
            'templates/_partials/*': 'templates/_partials/messages.html',
            'templates/base.html': 'templates/base.html',
            'website/*': 'website/settings.py',
            'accounts/context_processors.py': 'accounts/context_processors.py',
            'tailwind.config.js': 'tailwind.config.js',
            'integrations/middleware.py': 'integrations/middleware.py',
        }
        self.assertEqual(
            sorted(samples),
            sorted(glob for glob, _reason in ESCALATION_TRIGGERS),
            'the escalation table changed -- update this test and _docs/testing-guidelines.md',
        )
        for glob, sample in samples.items():
            with self.subTest(glob=glob):
                plan = plan_for([sample])
                self.assertEqual(plan.playwright, 'full')
                self.assertTrue(plan.escalation_reasons)
                self.assertIn(PLAYWRIGHT_FULL_COMMAND, plan.commands())
                self.assertNotIn(PLAYWRIGHT_CORE_COMMAND, plan.commands())

    def test_every_trigger_file_still_exists_on_disk(self):
        for glob, _reason in ESCALATION_TRIGGERS:
            if '*' in glob:
                with self.subTest(glob=glob):
                    self.assertTrue((REPO_ROOT / glob[:-2]).is_dir(), f'{glob} no longer exists')
            else:
                with self.subTest(glob=glob):
                    self.assertTrue((REPO_ROOT / glob).is_file(), f'{glob} no longer exists')

    def test_escalation_reason_names_the_file(self):
        plan = plan_for(['playwright_tests/conftest.py'])
        self.assertEqual(plan.escalation_reasons, ['playwright_tests/conftest.py: shared fixtures'])


@tag('core')
class ReverseImportPatternTest(SimpleTestCase):
    def test_patterns_match_import_forms_and_quoted_paths(self):
        regex = re.compile('|'.join(reverse_import_patterns('integrations.services.zoom')))
        for line in (
            'from integrations.services.zoom import create_meeting',
            'import integrations.services.zoom',
            "@patch('integrations.services.zoom.requests.post')",
            'from integrations.services import zoom',
        ):
            with self.subTest(line=line):
                self.assertRegex(line, regex)

    def test_patterns_do_not_match_a_longer_sibling_module(self):
        regex = re.compile('|'.join(reverse_import_patterns('integrations.services.zoom')))
        self.assertNotRegex('from integrations.services.zoominfo import thing', regex)

    def test_reexporting_package_widens_the_search_to_the_parent(self):
        self.assertTrue(_parent_reexports('content.models', 'article'))
        regex = re.compile('|'.join(reverse_import_patterns('content.models.article')))
        self.assertRegex('from content.models import Article', regex)


@tag('core')
class ReverseImportGrepTest(SimpleTestCase):
    """Exercises the real ``git grep`` against the real tree."""

    def test_grep_finds_real_zoom_consumers(self):
        references = git_grep_references(['integrations.services.zoom'])
        found = set(references['integrations.services.zoom'])
        for expected in (
            'events/services/zoom_lifecycle.py',   # from integrations.services.zoom import ...
            'studio/views/events.py',              # from integrations.services.zoom import ...
            'integrations/tests/test_zoom.py',     # @patch('integrations.services.zoom...')
        ):
            self.assertIn(expected, found)

    def test_grep_returns_an_empty_list_for_an_unreferenced_module(self):
        # Assembled at runtime so this file does not itself contain the
        # quoted dotted path the grep looks for.
        missing = 'integrations.services.' + 'no_such' + '_module_1468'
        references = git_grep_references([missing])
        self.assertEqual(references[missing], [])


@tag('core')
class RunCommandsTest(SimpleTestCase):
    def test_commands_drops_per_file_playwright_when_escalated(self):
        plan = Plan(base='origin/main')
        plan.extra_commands = [CORE_COMMAND, 'uv run pytest playwright_tests/test_dashboard.py -v']
        plan.playwright = 'full'
        self.assertEqual(plan.commands(), [CORE_COMMAND, PLAYWRIGHT_FULL_COMMAND])

    def test_commands_run_django_then_extras_then_playwright(self):
        plan = Plan(base='origin/main')
        plan.django_command = 'django'
        plan.extra_commands = [CORE_COMMAND]
        plan.playwright = 'full'
        self.assertEqual(plan.commands(), ['django', CORE_COMMAND, PLAYWRIGHT_FULL_COMMAND])

    def test_worst_exit_code_is_forwarded(self):
        plan = Plan(base='origin/main')
        plan.django_command = 'exit 0'
        plan.extra_commands = ['exit 3', 'exit 1']
        plan.playwright = 'none'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_commands(plan), 3)

    def test_all_green_returns_zero(self):
        plan = Plan(base='origin/main')
        plan.django_command = 'exit 0'
        plan.playwright = 'none'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_commands(plan), 0)


@tag('core')
class CliTest(SimpleTestCase):
    def _run(self, *args):
        return subprocess.run(
            ['uv', 'run', 'python', 'scripts/affected_tests.py', *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_json_output_matches_the_documented_shape(self):
        completed = self._run('--json')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(
            sorted(payload),
            sorted([
                'base', 'files', 'django_labels', 'django_command', 'extra_commands',
                'playwright', 'escalation_reasons', 'unmapped', 'notes',
            ]),
        )
        self.assertIsInstance(payload['files'], list)
        self.assertIsInstance(payload['django_labels'], list)
        self.assertIsInstance(payload['extra_commands'], list)
        self.assertIn(payload['playwright'], {'core', 'full', 'none'})

    def test_bad_base_ref_exits_two(self):
        completed = self._run('--base', 'refs/heads/definitely-not-a-branch-1468')
        self.assertEqual(completed.returncode, 2)
        self.assertIn('error:', completed.stderr)


@tag('core')
class GuardedDocArtifactTest(SimpleTestCase):
    """Every doc artifact a Django test READS must map to that test's app.

    This is the guard that stops rule 1 from silently dropping a file with a
    real assertion over it (issue #1468 QA rounds 2 and 3: markdown under
    ``email_app/``, then ``skills/**``, then ``CLAUDE.md``). Docs that a test
    merely mentions -- a ``docs_url`` string in the settings registry, say --
    are excluded on purpose: renaming those cannot break the assertion.
    """

    TREES = ('_docs', 'docs', 'specs', 'skills')
    TOP_LEVEL = ('CLAUDE.md', 'AGENTS.md', 'README.md')
    READ_CALL = re.compile(r'Path\(|open\(|read_text|read_bytes|\.exists\(|is_file\(|is_dir\(')
    # A top-level filename is only this repo's copy when it is anchored to the
    # repo root. A bare 'README.md' literal in content-sync tests means the
    # *content repo's* README, not ours.
    REPO_ANCHOR = re.compile(r'REPO_ROOT|BASE_DIR|PROJECT_ROOT|REPO_DIR')

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        trees = '|'.join(cls.TREES)
        cls.slash_re = re.compile(rf"""["']({trees})/([^"'\s]+)["']""")
        cls.chain_re = re.compile(
            rf"""["']({trees})["']\s*/\s*["']([^"'\s]+)["'](?:\s*/\s*["']([^"'\s]+)["'])?"""
        )
        cls.top_level_re = re.compile(r"""["'](%s)["']""" % '|'.join(
            name.replace('.', r'\.') for name in cls.TOP_LEVEL
        ))

    def _django_test_files(self):
        # -c -o includes untracked test modules: the SWE's new tests are
        # uncommitted when the tester runs this.
        listed = subprocess.run(
            ['git', 'ls-files', '-c', '-o', '--exclude-standard', '--', '*/tests/*.py', 'tests/*.py'],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        return sorted(set(listed))

    def _artifacts_read_by_tests(self):
        """-> {artifact path: {app label of the test module reading it}}."""
        found: dict[str, set[str]] = {}
        for relative in self._django_test_files():
            owner = relative.split('/')[0]
            text = (REPO_ROOT / relative).read_text(encoding='utf-8', errors='ignore')
            for line in text.splitlines():
                if not self.READ_CALL.search(line):
                    continue
                paths = {f'{m.group(1)}/{m.group(2)}' for m in self.slash_re.finditer(line)}
                paths |= {
                    '/'.join(part for part in match.groups() if part)
                    for match in self.chain_re.finditer(line)
                }
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
        self.assertIn('skills/ai-shipping-labs-member-api', found)
        self.assertIn('member_api', found['skills/ai-shipping-labs-member-api'])
        self.assertIn('_docs/design-system.md', found)
        for top_level in ('CLAUDE.md', 'AGENTS.md', 'README.md'):
            self.assertIn(top_level, found)
            self.assertIn(TESTS_PACKAGE, found[top_level])

    def test_every_read_artifact_selects_its_reader(self):
        for artifact, owners in sorted(self._artifacts_read_by_tests().items()):
            probe = artifact if (REPO_ROOT / artifact).is_file() else f'{artifact}/probe.md'
            labels = plan_for([probe]).django_labels
            for owner in sorted(owners):
                expected = TESTS_PACKAGE if owner == TESTS_PACKAGE else owner
                with self.subTest(artifact=artifact, owner=owner):
                    self.assertTrue(
                        expected in labels
                        or any(label.startswith(f'{expected}.') for label in labels),
                        f'{artifact} is read by {owner} tests but the plan selects {labels}. '
                        f'Add it to CONTRACT_PATHS in scripts/affected_tests.py.',
                    )


@tag('core')
class ContractPathTest(SimpleTestCase):
    def test_focused_contracts_target_exact_top_level_test_modules(self):
        for glob, labels in FOCUSED_CONTRACT_PATHS:
            with self.subTest(glob=glob):
                self.assertTrue(labels)
                self.assertTrue(all(label.startswith('tests.test_') for label in labels))

    def test_every_contract_label_is_a_real_target(self):
        known = set(APP_LABELS) | {TESTS_PACKAGE, 'website'}
        for glob, labels in CONTRACT_PATHS:
            self.assertTrue(labels, f'{glob} maps to no labels')
            for label in labels:
                with self.subTest(glob=glob, label=label):
                    self.assertIn(label, known)

    def test_every_contract_doc_artifact_still_exists(self):
        # Only the doc-artifact half: CI surfaces like Procfile.dev are
        # covered by their own repo conventions, and globs are checked as dirs.
        for glob, _labels in CONTRACT_PATHS:
            if not glob.startswith(('_docs/', 'docs/', 'specs/', 'skills/')):
                continue
            target = REPO_ROOT / (glob[:-2] if glob.endswith('/*') else glob)
            with self.subTest(glob=glob):
                self.assertTrue(target.exists(), f'{glob} no longer exists')


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
    'CLAUDE.md',
    'AGENTS.md',
    'README.md',
    '_docs/PROCESS.md',
    '.claude/agents/software-engineer.md',
    '.claude/agents/tester.md',
    '.claude/agents/oncall-engineer.md',
    '.claude/agents/product-manager.md',
    '.claude/agents/designer.md',
    '.claude/skills/execute/SKILL.md',
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


@tag('core')
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
                self.assertTrue(path.is_file(), f'{relative} is missing')
                offenders = full_suite_instructions(path.read_text(encoding='utf-8'))
                self.assertEqual(
                    offenders,
                    [],
                    f'{relative} instructs a full-suite run; point it at '
                    f'`make test-affected` instead:\n  ' + '\n  '.join(offenders),
                )

    def test_the_guard_would_catch_the_original_instructions(self):
        # The exact strings this issue removed -- proves the regex is not vacuous.
        for original in (
            'uv run python manage.py test --parallel     # Django tests (~1 min)',
            'uv run python -m pytest playwright_tests/   # E2E tests',
            'run ALL tests (uv run python manage.py test AND uv run pytest playwright_tests/ -v)',
            'CI runs the full suite, so always run `make test` before pushing.',
            'make test-all',
        ):
            with self.subTest(original=original):
                self.assertTrue(full_suite_instructions(original), original)

    def test_the_guard_allows_scoped_and_prohibitive_lines(self):
        for allowed in (
            'uv run python manage.py test {touched_app} --parallel 4',
            'make test-core',
            'make test-affected',
            'make test-playwright-core',
            'uv run pytest playwright_tests/test_dashboard.py -v',
            '- Do NOT run the full Django suite locally (`make test`, `make test-all`).',
            'make test            # CI gate: full Django unit/integration suite',
        ):
            with self.subTest(allowed=allowed):
                self.assertEqual(full_suite_instructions(allowed), [])


@tag('core')
class RotGuardTest(SimpleTestCase):
    def test_app_labels_match_installed_apps(self):
        project_apps = {
            config.label
            for config in apps.get_app_configs()
            if Path(config.path).parent == REPO_ROOT
        }
        self.assertEqual(set(APP_LABELS), project_apps)

    def test_makefile_exposes_test_affected(self):
        makefile = (REPO_ROOT / 'Makefile').read_text()
        self.assertRegex(makefile, r'(?m)^test-affected:')
        self.assertIn('uv run python scripts/affected_tests.py --run', makefile)
        self.assertRegex(makefile, r'(?m)^\.PHONY:.*\btest-affected\b')

    def test_test_core_target_uses_bounded_parallelism(self):
        # make test-affected emits `make test-core` on its fail-closed and
        # hub-module paths, so the --parallel 4 guarantee has to hold there.
        makefile = (REPO_ROOT / 'Makefile').read_text()
        recipe = re.search(r'(?m)^test-core:\n\t(.+)$', makefile)
        self.assertIsNotNone(recipe, 'test-core target not found')
        self.assertIn('--parallel 4', recipe.group(1))

    def test_root_agent_instructions_delegate_to_the_canonical_helper_policy(self):
        # Claude loads the bootstrap while Codex-style agents load AGENTS.md
        # directly. Pin both halves of that explicit one-hop contract without
        # duplicating the canonical testing policy back into CLAUDE.md.
        claude_text = (REPO_ROOT / 'CLAUDE.md').read_text()
        agents_text = (REPO_ROOT / 'AGENTS.md').read_text()
        delegation_directives = [
            line.strip()
            for line in claude_text.splitlines()
            if line.lstrip().startswith('@')
        ]
        self.assertEqual(
            delegation_directives,
            ['@AGENTS.md'],
            'CLAUDE.md must delegate exactly once to the root AGENTS.md',
        )
        self.assertIn('make test-affected', agents_text)
        self.assertIn('scripts/affected_tests.py', agents_text)

    def test_software_engineer_agent_does_not_mandate_the_full_local_suite(self):
        text = (REPO_ROOT / '.claude' / 'agents' / 'software-engineer.md').read_text()
        self.assertIn('make test-affected', text)
        self.assertNotIn('uv run python manage.py test --parallel', text)
        self.assertIn('Affected-tests plan:', text)

    def test_tester_agent_requires_the_helper(self):
        text = (REPO_ROOT / '.claude' / 'agents' / 'tester.md').read_text()
        self.assertIn('scripts/affected_tests.py', text)
        self.assertIn('make test-affected', text)

    def test_oncall_agent_uses_bounded_parallelism_and_scoped_playwright(self):
        text = (REPO_ROOT / '.claude' / 'agents' / 'oncall-engineer.md').read_text()
        self.assertIn('make test-affected', text)
        self.assertIn('--parallel 4', text)
        self.assertNotIn('uv run python manage.py test {touched_app} --parallel\n', text)

    def test_execute_skill_qa_prompt_uses_the_helper(self):
        # This inline Task prompt dominates tester.md, which it tells the
        # subagent to read -- so it has to carry the same instruction.
        text = (REPO_ROOT / '.claude' / 'skills' / 'execute' / 'SKILL.md').read_text()
        self.assertIn('make test-affected', text)
        self.assertIn('scripts/affected_tests.py', text)
        self.assertNotIn('run ALL tests', text)

    def test_readme_points_at_the_affected_tests_helper(self):
        text = (REPO_ROOT / 'README.md').read_text()
        self.assertIn('make test-affected', text)
        self.assertIn('scripts/affected_tests.py', text)
        self.assertNotIn('always run `make test` before pushing', text)

    def test_docs_document_the_helper(self):
        process = (REPO_ROOT / '_docs' / 'PROCESS.md').read_text()
        self.assertIn('make test-affected', process)
        guidelines = (REPO_ROOT / '_docs' / 'testing-guidelines.md').read_text()
        self.assertIn('## Affected-tests selection (`make test-affected`)', guidelines)
