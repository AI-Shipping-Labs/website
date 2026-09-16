"""Tests for the content sync of three-level curriculum nesting (issue #1674).

Covers:
- Nested module directories (module.yaml inside a module.yaml directory)
  sync into a parent Module + child submodule Modules.
- A module directory mixing submodule subdirectories with direct unit
  .md files is rejected with a SyncLog error naming the directory, and
  neither side is created.
- Two submodules under one module, each containing a unit whose slug is
  the same (e.g. ``section-overview``), both sync cleanly — the
  collision that plagued the flattened two-level shape disappears once
  the middle level is present (per the owner's naming-convention note).
- ``module.yaml``'s ``bonus:``/``available_after_days:`` keys.
- Unit frontmatter ``kind:``/``session_position:``/``is_bonus:`` keys.
- ``cohorts:`` YAML ``mode:`` key (self_paced, validation, uniqueness).

Uses the house ``make_sync_repo``/``sync_repo`` fixtures (same as
``integrations/tests/test_github_sync.py::SyncCoursesTest``).
"""

from django.test import TestCase

from content.models import Cohort, Module, Unit
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class NestedModuleSyncTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/nesting-courses',
            prefix='nesting-sync-',
        )

    def _write_base_course(self):
        self.repo.write_yaml(
            'buildcamp/course.yaml',
            {
                'title': 'Buildcamp',
                'slug': 'buildcamp-nest',
                'required_level': 0,
                'content_id': '11111111-1111-1111-1111-111111111111',
            },
        )

    def test_nested_submodule_directories_create_three_level_tree(self):
        self._write_base_course()
        self.repo.write_yaml(
            'buildcamp/02-deployment/module.yaml',
            {'title': 'Week 2: Deployment', 'sort_order': 2},
        )
        self.repo.write_yaml(
            'buildcamp/02-deployment/01-docker/module.yaml',
            {'title': 'Docker'},
        )
        self.repo.write_markdown(
            'buildcamp/02-deployment/01-docker/01-lesson.md',
            {'title': 'Docker basics'}, 'Docker body.\n',
        )
        self.repo.write_yaml(
            'buildcamp/02-deployment/02-kubernetes/module.yaml',
            {'title': 'Kubernetes'},
        )
        self.repo.write_markdown(
            'buildcamp/02-deployment/02-kubernetes/01-lesson.md',
            {'title': 'K8s basics'}, 'K8s body.\n',
        )

        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'), sync_log.errors)

        parent = Module.objects.get(course__slug='buildcamp-nest', slug='deployment')
        self.assertIsNone(parent.parent_id)
        children = list(parent.children.order_by('sort_order'))
        self.assertEqual([c.slug for c in children], ['docker', 'kubernetes'])
        for child in children:
            self.assertEqual(child.parent_id, parent.pk)
        docker_unit = Unit.objects.get(module__slug='docker', slug='lesson')
        self.assertEqual(docker_unit.title, 'Docker basics')
        self.assertEqual(docker_unit.module.parent_id, parent.pk)

    def test_mixed_directory_rejected_and_creates_neither_side(self):
        self._write_base_course()
        self.repo.write_yaml(
            'buildcamp/03-mixed/module.yaml',
            {'title': 'Mixed module'},
        )
        self.repo.write_yaml(
            'buildcamp/03-mixed/01-sub/module.yaml',
            {'title': 'Sub'},
        )
        self.repo.write_markdown(
            'buildcamp/03-mixed/01-sub/01-lesson.md',
            {'title': 'Sub lesson'}, 'Body.\n',
        )
        # Direct unit file alongside the submodule dir -> mixed content.
        self.repo.write_markdown(
            'buildcamp/03-mixed/01-stray-unit.md',
            {'title': 'Stray unit'}, 'Stray body.\n',
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertFalse(
            Module.objects.filter(course__slug='buildcamp-nest', slug='mixed-module').exists(),
        )
        self.assertFalse(
            Module.objects.filter(course__slug='buildcamp-nest', slug='sub').exists(),
        )
        self.assertFalse(Unit.objects.filter(slug='stray-unit').exists())
        self.assertFalse(Unit.objects.filter(slug='sub-lesson').exists())
        error_text = ' '.join(
            e.get('error', '') for e in (sync_log.errors or [])
        )
        self.assertIn('03-mixed', error_text)

    def test_same_unit_slug_across_two_submodules_syncs_cleanly(self):
        """The collision two flattened section-overview units caused
        disappears once each lives under its own submodule Module row."""
        self._write_base_course()
        self.repo.write_yaml(
            'buildcamp/04-topics/module.yaml', {'title': 'Topics'},
        )
        for sub_slug, title in (('01-rag', 'RAG'), ('02-agents', 'Agents')):
            self.repo.write_yaml(
                f'buildcamp/04-topics/{sub_slug}/module.yaml', {'title': title},
            )
            self.repo.write_markdown(
                f'buildcamp/04-topics/{sub_slug}/01-section-overview.md',
                {'title': 'Section overview'}, f'{title} overview body.\n',
            )

        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'), sync_log.errors)

        rag_module = Module.objects.get(slug='rag', course__slug='buildcamp-nest')
        agents_module = Module.objects.get(slug='agents', course__slug='buildcamp-nest')
        rag_unit = Unit.objects.get(module=rag_module, slug='section-overview')
        agents_unit = Unit.objects.get(module=agents_module, slug='section-overview')
        self.assertNotEqual(rag_unit.pk, agents_unit.pk)

    def test_module_yaml_bonus_and_available_after_days(self):
        self._write_base_course()
        self.repo.write_yaml(
            'buildcamp/05-bonus/module.yaml',
            {'title': 'Bonus module', 'bonus': True, 'available_after_days': 21},
        )
        self.repo.write_markdown(
            'buildcamp/05-bonus/01-extra.md', {'title': 'Extra'}, 'Body.\n',
        )
        sync_repo(self.source, self.repo)
        module = Module.objects.get(course__slug='buildcamp-nest', slug='bonus')
        self.assertTrue(module.is_bonus)
        self.assertEqual(module.available_after_days, 21)

    def test_module_yaml_bonus_absent_defaults_false(self):
        self._write_base_course()
        self.repo.write_yaml(
            'buildcamp/06-plain/module.yaml', {'title': 'Plain module'},
        )
        sync_repo(self.source, self.repo)
        module = Module.objects.get(course__slug='buildcamp-nest', slug='plain')
        self.assertFalse(module.is_bonus)
        self.assertIsNone(module.available_after_days)

    def test_third_level_module_directory_rejected_at_sync_layer(self):
        """Depth cap (max two levels of module) is enforced at SYNC time,
        not just model validation — a submodule dir that itself contains
        a further nested module.yaml is rejected, named, and creates
        nothing at the third level, while sibling modules still sync."""
        self._write_base_course()
        self.repo.write_yaml('buildcamp/08-week/module.yaml', {'title': 'Week'})
        self.repo.write_yaml(
            'buildcamp/08-week/01-topic/module.yaml', {'title': 'Topic'},
        )
        self.repo.write_yaml(
            'buildcamp/08-week/01-topic/01-too-deep/module.yaml',
            {'title': 'Too deep'},
        )
        self.repo.write_markdown(
            'buildcamp/08-week/01-topic/01-too-deep/01-lesson.md',
            {'title': 'Too deep lesson'}, 'Body.\n',
        )
        self.repo.write_yaml(
            'buildcamp/09-plain/module.yaml', {'title': 'Plain sibling'},
        )
        self.repo.write_markdown(
            'buildcamp/09-plain/01-lesson.md', {'title': 'Plain lesson'}, 'Body.\n',
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertFalse(
            Module.objects.filter(
                course__slug='buildcamp-nest', slug='too-deep',
            ).exists(),
        )
        self.assertFalse(Unit.objects.filter(title='Too deep lesson').exists())
        error_text = ' '.join(
            e.get('error', '') for e in (sync_log.errors or [])
        )
        self.assertIn('too-deep', error_text)
        plain_module = Module.objects.get(course__slug='buildcamp-nest', slug='plain')
        self.assertTrue(
            Unit.objects.filter(module=plain_module, title='Plain lesson').exists(),
        )
        self.assertTrue(
            Module.objects.filter(
                course__slug='buildcamp-nest', slug='topic', parent__isnull=False,
            ).exists(),
        )


class UnitKindFrontmatterSyncTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/kind-courses',
            prefix='kind-sync-',
        )
        self.repo.write_yaml(
            'kindcourse/course.yaml',
            {
                'title': 'Kind Course', 'slug': 'kind-course',
                'required_level': 0,
                'content_id': '22222222-2222-2222-2222-222222222222',
            },
        )
        self.repo.write_yaml(
            'kindcourse/01-week/module.yaml', {'title': 'Week 1'},
        )

    def test_kind_homework_routes_body_into_homework_field(self):
        self.repo.write_markdown(
            'kindcourse/01-week/01-hw.md',
            {'title': 'Homework 1', 'kind': 'homework'}, 'Do this.\n',
        )
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(module__course__slug='kind-course', slug='hw')
        self.assertEqual(unit.kind, 'homework')
        self.assertIn('Do this.', unit.homework)
        self.assertEqual(unit.body, '')

    def test_legacy_is_homework_sets_kind_homework(self):
        self.repo.write_markdown(
            'kindcourse/01-week/02-legacy-hw.md',
            {'title': 'Legacy homework', 'is_homework': True}, 'Legacy.\n',
        )
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(module__course__slug='kind-course', slug='legacy-hw')
        self.assertEqual(unit.kind, 'homework')

    def test_kind_event_with_session_position(self):
        self.repo.write_markdown(
            'kindcourse/01-week/03-live.md',
            {'title': 'Live Q&A', 'kind': 'event', 'session_position': 4},
            'Body.\n',
        )
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(module__course__slug='kind-course', slug='live')
        self.assertEqual(unit.kind, 'event')
        self.assertEqual(unit.session_position, 4)

    def test_kind_event_missing_session_position_fails_that_unit(self):
        self.repo.write_markdown(
            'kindcourse/01-week/04-broken-event.md',
            {'title': 'Broken event', 'kind': 'event'}, 'Body.\n',
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertFalse(
            Unit.objects.filter(slug='broken-event').exists(),
        )
        error_text = ' '.join(e.get('error', '') for e in (sync_log.errors or []))
        self.assertIn('04-broken-event.md', error_text)

    def test_kind_event_non_positive_session_position_fails_that_unit(self):
        self.repo.write_markdown(
            'kindcourse/01-week/05-bad-position.md',
            {'title': 'Bad position', 'kind': 'event', 'session_position': 0},
            'Body.\n',
        )
        sync_repo(self.source, self.repo)
        self.assertFalse(Unit.objects.filter(slug='bad-position').exists())

    def test_is_bonus_unit_frontmatter(self):
        self.repo.write_markdown(
            'kindcourse/01-week/06-bonus.md',
            {'title': 'Bonus unit', 'is_bonus': True}, 'Body.\n',
        )
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(module__course__slug='kind-course', slug='bonus')
        self.assertTrue(unit.is_bonus)

    def test_is_bonus_absent_defaults_false(self):
        self.repo.write_markdown(
            'kindcourse/01-week/07-plain.md',
            {'title': 'Plain unit'}, 'Body.\n',
        )
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(module__course__slug='kind-course', slug='plain')
        self.assertFalse(unit.is_bonus)


class CohortModeYamlSyncTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/cohort-mode-courses',
            prefix='cohort-mode-sync-',
        )

    def _write_course(self, cohorts):
        self.repo.write_yaml(
            'cmcourse/course.yaml',
            {
                'title': 'Cohort mode course', 'slug': 'cohort-mode-course',
                'required_level': 0,
                'content_id': '33333333-3333-3333-3333-333333333333',
                'cohorts': cohorts,
            },
        )

    def test_self_paced_entry_creates_dateless_cohort(self):
        self._write_course([
            {'key': 'self-paced', 'name': 'Self-paced', 'mode': 'self_paced'},
        ])
        sync_repo(self.source, self.repo)
        cohort = Cohort.objects.get(course__slug='cohort-mode-course', external_key='self-paced')
        self.assertEqual(cohort.mode, 'self_paced')
        self.assertIsNone(cohort.start_date)

    def test_self_paced_entry_with_dates_fails_sync(self):
        self._write_course([
            {
                'key': 'bad-self-paced', 'name': 'Bad', 'mode': 'self_paced',
                'start_date': '2026-09-21',
            },
        ])
        sync_repo(self.source, self.repo)
        self.assertFalse(
            Cohort.objects.filter(external_key='bad-self-paced').exists(),
        )

    def test_second_self_paced_entry_fails_sync(self):
        self._write_course([
            {'key': 'sp-1', 'name': 'SP 1', 'mode': 'self_paced'},
            {'key': 'sp-2', 'name': 'SP 2', 'mode': 'self_paced'},
        ])
        sync_repo(self.source, self.repo)
        self.assertEqual(
            Cohort.objects.filter(
                course__slug='cohort-mode-course', mode='self_paced',
            ).count(),
            1,
        )

    def test_default_mode_is_cohort(self):
        self._write_course([
            {
                'key': 'dated', 'name': 'Dated', 'start_date': '2026-09-21',
                'end_date': '2026-11-22',
            },
        ])
        sync_repo(self.source, self.repo)
        cohort = Cohort.objects.get(course__slug='cohort-mode-course', external_key='dated')
        self.assertEqual(cohort.mode, 'cohort')
