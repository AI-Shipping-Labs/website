"""Direct sync-parser tests for curriculum nesting (issue #1674).

Companion to ``integrations/tests/test_course_nesting_sync_1674.py``,
which goes through the full ``sync_content_source``/package orchestration
(the house pattern, but it needs real GitHub App credentials to resolve —
unavailable in a sandboxed dev environment). This file calls
``_sync_single_course`` directly under a local ``checkout_scope`` (the
same no-credentials-needed local-directory path
``integrations/tests/test_course_unit_lookup.py`` already uses for
``_build_course_unit_lookup``), so the nested-module/mixed-content/kind
-parsing logic is verified without any external dependency.
"""

import os
import shutil
import tempfile
import uuid

import yaml
from django.test import TestCase

from content.models import Cohort, Module, Unit
from content.sync_parsers.checkout_view import checkout_scope
from content.sync_parsers.families.courses import _sync_single_course


def _fresh_stats():
    return {
        'created': 0, 'updated': 0, 'unchanged': 0, 'deleted': 0,
        'errors': [], 'items_detail': [],
    }


class _FakeSource:
    repo_name = 'AI-Shipping-Labs/direct-sync-courses'


class DirectSyncFixtureBase(TestCase):
    """Writes a course tree to a real temp dir and syncs it directly."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix='direct-sync-1674-')
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        self.course_dir = os.path.join(self.temp_dir, 'buildcamp')
        os.makedirs(self.course_dir)

    def _write_yaml(self, rel_path, data):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            yaml.safe_dump(data, f)

    def _write_markdown(self, rel_path, frontmatter, body):
        full = os.path.join(self.course_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        frontmatter = dict(frontmatter)
        frontmatter.setdefault('content_id', str(uuid.uuid4()))
        with open(full, 'w') as f:
            f.write('---\n')
            yaml.safe_dump(frontmatter, f)
            f.write('---\n')
            f.write(body)

    def _write_course_yaml(self, **overrides):
        data = {
            'title': 'Buildcamp',
            'slug': 'buildcamp-direct',
            'required_level': 0,
            'content_id': str(uuid.uuid4()),
        }
        data.update(overrides)
        self._write_yaml('course.yaml', data)

    def _sync(self):
        stats = _fresh_stats()
        with checkout_scope(self.temp_dir):
            _sync_single_course(
                self.course_dir, self.temp_dir, _FakeSource(),
                'deadbeef', stats, set(), set(),
            )
        return stats


class NestedModuleDirectSyncTest(DirectSyncFixtureBase):
    def test_nested_submodule_directories_create_three_level_tree(self):
        self._write_course_yaml()
        self._write_yaml(
            '02-deployment/module.yaml',
            {'title': 'Week 2: Deployment', 'sort_order': 2},
        )
        self._write_yaml('02-deployment/01-docker/module.yaml', {'title': 'Docker'})
        self._write_markdown(
            '02-deployment/01-docker/01-lesson.md',
            {'title': 'Docker basics'}, 'Docker body.\n',
        )
        self._write_yaml('02-deployment/02-kubernetes/module.yaml', {'title': 'Kubernetes'})
        self._write_markdown(
            '02-deployment/02-kubernetes/01-lesson.md',
            {'title': 'K8s basics'}, 'K8s body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        parent = Module.objects.get(course__slug='buildcamp-direct', slug='deployment')
        self.assertIsNone(parent.parent_id)
        children = list(parent.children.order_by('sort_order'))
        self.assertEqual([c.slug for c in children], ['docker', 'kubernetes'])
        for child in children:
            self.assertEqual(child.parent_id, parent.pk)
        docker_unit = Unit.objects.get(module__slug='docker', slug='lesson')
        self.assertEqual(docker_unit.title, 'Docker basics')

    def test_mixed_directory_rejected_and_creates_neither_side(self):
        self._write_course_yaml()
        self._write_yaml('03-mixed/module.yaml', {'title': 'Mixed module'})
        self._write_yaml('03-mixed/01-sub/module.yaml', {'title': 'Sub'})
        self._write_markdown(
            '03-mixed/01-sub/01-lesson.md', {'title': 'Sub lesson'}, 'Body.\n',
        )
        self._write_markdown(
            '03-mixed/01-stray-unit.md', {'title': 'Stray unit'}, 'Stray body.\n',
        )

        stats = self._sync()

        self.assertFalse(
            Module.objects.filter(course__slug='buildcamp-direct', slug='mixed-module').exists(),
        )
        self.assertFalse(
            Module.objects.filter(course__slug='buildcamp-direct', slug='sub').exists(),
        )
        self.assertFalse(Unit.objects.filter(slug='stray-unit').exists())
        self.assertFalse(Unit.objects.filter(slug='sub-lesson').exists())
        error_text = ' '.join(e.get('error', '') for e in stats['errors'])
        self.assertIn('03-mixed', error_text)

    def test_same_unit_slug_across_two_submodules_syncs_cleanly(self):
        """Two submodules under one module, each with a unit slug of
        ``section-overview``, both sync cleanly — the collision that
        plagued the flattened two-level shape disappears once the middle
        level is present."""
        self._write_course_yaml()
        self._write_yaml('04-topics/module.yaml', {'title': 'Topics'})
        for sub_slug, title in (('01-rag', 'RAG'), ('02-agents', 'Agents')):
            self._write_yaml(f'04-topics/{sub_slug}/module.yaml', {'title': title})
            self._write_markdown(
                f'04-topics/{sub_slug}/01-section-overview.md',
                {'title': 'Section overview'}, f'{title} overview body.\n',
            )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        rag_module = Module.objects.get(slug='rag', course__slug='buildcamp-direct')
        agents_module = Module.objects.get(slug='agents', course__slug='buildcamp-direct')
        rag_unit = Unit.objects.get(module=rag_module, slug='section-overview')
        agents_unit = Unit.objects.get(module=agents_module, slug='section-overview')
        self.assertNotEqual(rag_unit.pk, agents_unit.pk)

    def test_module_yaml_bonus_and_available_after_days(self):
        self._write_course_yaml()
        self._write_yaml(
            '05-bonus/module.yaml',
            {'title': 'Bonus module', 'bonus': True, 'available_after_days': 21},
        )
        self._write_markdown('05-bonus/01-extra.md', {'title': 'Extra'}, 'Body.\n')

        self._sync()
        module = Module.objects.get(course__slug='buildcamp-direct', slug='bonus')
        self.assertTrue(module.is_bonus)
        self.assertEqual(module.available_after_days, 21)

    def test_module_yaml_bonus_absent_defaults_false(self):
        self._write_course_yaml()
        self._write_yaml('06-plain/module.yaml', {'title': 'Plain module'})

        self._sync()
        module = Module.objects.get(course__slug='buildcamp-direct', slug='plain')
        self.assertFalse(module.is_bonus)
        self.assertIsNone(module.available_after_days)

    def test_third_level_module_directory_rejected_at_sync_layer(self):
        """Depth cap (max two levels of module) is enforced at SYNC time,
        not just by model validation on a hand-built instance — a
        submodule directory that itself contains a further nested
        module.yaml is rejected, named, and does not create the
        third-level row, while sibling well-formed modules still sync."""
        self._write_course_yaml()
        self._write_yaml('08-week/module.yaml', {'title': 'Week'})
        self._write_yaml('08-week/01-topic/module.yaml', {'title': 'Topic'})
        # Third level: a module.yaml nested inside the submodule dir.
        self._write_yaml(
            '08-week/01-topic/01-too-deep/module.yaml', {'title': 'Too deep'},
        )
        self._write_markdown(
            '08-week/01-topic/01-too-deep/01-lesson.md',
            {'title': 'Too deep lesson'}, 'Body.\n',
        )
        # A well-formed sibling course elsewhere in the tree must still sync.
        self._write_yaml('09-plain/module.yaml', {'title': 'Plain sibling'})
        self._write_markdown(
            '09-plain/01-lesson.md', {'title': 'Plain lesson'}, 'Body.\n',
        )

        stats = self._sync()

        self.assertFalse(
            Module.objects.filter(
                course__slug='buildcamp-direct', slug='too-deep',
            ).exists(),
        )
        self.assertFalse(Unit.objects.filter(title='Too deep lesson').exists())
        error_text = ' '.join(e.get('error', '') for e in stats['errors'])
        self.assertIn('too-deep', error_text)
        # The sibling top-level module still synced despite the rejection.
        plain_module = Module.objects.get(
            course__slug='buildcamp-direct', slug='plain',
        )
        self.assertTrue(
            Unit.objects.filter(module=plain_module, title='Plain lesson').exists(),
        )
        # The submodule itself (level 2, valid) still synced correctly.
        self.assertTrue(
            Module.objects.filter(
                course__slug='buildcamp-direct', slug='topic', parent__isnull=False,
            ).exists(),
        )

    def test_submodule_slug_repeated_across_different_parents_syncs_cleanly(self):
        """Issue #1674 grooming correction: real Maven content repeats a
        "Homework" submodule slug under several weeks — module slug
        uniqueness is scoped per sibling group, not course-wide."""
        self._write_course_yaml()
        for week_dir, week_title in (('08-week1', 'Week 1'), ('09-week3', 'Week 3')):
            self._write_yaml(f'{week_dir}/module.yaml', {'title': week_title})
            self._write_yaml(f'{week_dir}/01-homework/module.yaml', {'title': 'Homework'})
            self._write_markdown(
                f'{week_dir}/01-homework/01-assignment.md',
                {'title': 'Assignment'}, 'Body.\n',
            )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])

        week1 = Module.objects.get(course__slug='buildcamp-direct', slug='week1')
        week3 = Module.objects.get(course__slug='buildcamp-direct', slug='week3')
        hw1 = Module.objects.get(parent=week1, slug='homework')
        hw3 = Module.objects.get(parent=week3, slug='homework')
        self.assertNotEqual(hw1.pk, hw3.pk)
        self.assertTrue(Unit.objects.filter(module=hw1, slug='assignment').exists())
        self.assertTrue(Unit.objects.filter(module=hw3, slug='assignment').exists())

    def test_submodule_reparented_when_source_changes_two_level_to_three_level(self):
        """A previously two-level (flat) module that gains submodule
        subdirectories on a later sync becomes a parent, and its old
        direct units are swept as stale (issue #1674 transition case)."""
        self._write_course_yaml()
        self._write_yaml('07-transition/module.yaml', {'title': 'Transition'})
        self._write_markdown(
            '07-transition/01-old-unit.md', {'title': 'Old unit'}, 'Body.\n',
        )
        self._sync()
        self.assertTrue(Unit.objects.filter(slug='old-unit').exists())

        # Remove the old unit file; add a submodule instead.
        os.remove(os.path.join(self.course_dir, '07-transition/01-old-unit.md'))
        self._write_yaml('07-transition/01-new-sub/module.yaml', {'title': 'New sub'})
        self._write_markdown(
            '07-transition/01-new-sub/01-lesson.md', {'title': 'New lesson'}, 'Body.\n',
        )

        stats = self._sync()
        self.assertEqual(stats['errors'], [])
        parent = Module.objects.get(course__slug='buildcamp-direct', slug='transition')
        self.assertFalse(Unit.objects.filter(slug='old-unit').exists())
        self.assertEqual(list(parent.children.values_list('slug', flat=True)), ['new-sub'])


class UnitKindFrontmatterDirectSyncTest(DirectSyncFixtureBase):
    def setUp(self):
        super().setUp()
        self._write_course_yaml(slug='kind-course-direct')
        self._write_yaml('01-week/module.yaml', {'title': 'Week 1'})

    def test_kind_homework_routes_body_into_homework_field(self):
        self._write_markdown(
            '01-week/01-hw.md', {'title': 'Homework 1', 'kind': 'homework'}, 'Do this.\n',
        )
        self._sync()
        unit = Unit.objects.get(module__course__slug='kind-course-direct', slug='hw')
        self.assertEqual(unit.kind, 'homework')
        self.assertIn('Do this.', unit.homework)
        self.assertEqual(unit.body, '')

    def test_legacy_is_homework_sets_kind_homework(self):
        self._write_markdown(
            '01-week/02-legacy-hw.md', {'title': 'Legacy homework', 'is_homework': True},
            'Legacy.\n',
        )
        self._sync()
        unit = Unit.objects.get(module__course__slug='kind-course-direct', slug='legacy-hw')
        self.assertEqual(unit.kind, 'homework')

    def test_kind_event_with_session_position(self):
        self._write_markdown(
            '01-week/03-live.md',
            {'title': 'Live Q&A', 'kind': 'event', 'session_position': 4}, 'Body.\n',
        )
        self._sync()
        unit = Unit.objects.get(module__course__slug='kind-course-direct', slug='live')
        self.assertEqual(unit.kind, 'event')
        self.assertEqual(unit.session_position, 4)

    def test_kind_event_missing_session_position_fails_that_unit(self):
        self._write_markdown(
            '01-week/04-broken-event.md', {'title': 'Broken event', 'kind': 'event'}, 'Body.\n',
        )
        stats = self._sync()
        self.assertFalse(Unit.objects.filter(slug='broken-event').exists())
        error_text = ' '.join(e.get('error', '') for e in stats['errors'])
        self.assertIn('04-broken-event.md', error_text)

    def test_kind_event_non_positive_session_position_fails_that_unit(self):
        self._write_markdown(
            '01-week/05-bad-position.md',
            {'title': 'Bad position', 'kind': 'event', 'session_position': 0}, 'Body.\n',
        )
        self._sync()
        self.assertFalse(Unit.objects.filter(slug='bad-position').exists())

    def test_invalid_kind_value_fails_that_unit(self):
        self._write_markdown(
            '01-week/06-invalid-kind.md', {'title': 'Invalid kind', 'kind': 'quiz'}, 'Body.\n',
        )
        self._sync()
        self.assertFalse(Unit.objects.filter(slug='invalid-kind').exists())

    def test_is_bonus_unit_frontmatter(self):
        self._write_markdown(
            '01-week/07-bonus.md', {'title': 'Bonus unit', 'is_bonus': True}, 'Body.\n',
        )
        self._sync()
        unit = Unit.objects.get(module__course__slug='kind-course-direct', slug='bonus')
        self.assertTrue(unit.is_bonus)

    def test_is_bonus_absent_defaults_false(self):
        self._write_markdown('01-week/08-plain.md', {'title': 'Plain unit'}, 'Body.\n')
        self._sync()
        unit = Unit.objects.get(module__course__slug='kind-course-direct', slug='plain')
        self.assertFalse(unit.is_bonus)


class CohortModeYamlDirectSyncTest(DirectSyncFixtureBase):
    def test_self_paced_entry_creates_dateless_cohort(self):
        self._write_course_yaml(
            slug='cohort-mode-direct',
            cohorts=[{'key': 'self-paced', 'name': 'Self-paced', 'mode': 'self_paced'}],
        )
        self._sync()
        cohort = Cohort.objects.get(course__slug='cohort-mode-direct', external_key='self-paced')
        self.assertEqual(cohort.mode, 'self_paced')
        self.assertIsNone(cohort.start_date)

    def test_self_paced_entry_with_dates_fails_sync(self):
        self._write_course_yaml(
            slug='cohort-mode-direct-2',
            cohorts=[{
                'key': 'bad-self-paced', 'name': 'Bad', 'mode': 'self_paced',
                'start_date': '2026-09-21',
            }],
        )
        self._sync()
        self.assertFalse(Cohort.objects.filter(external_key='bad-self-paced').exists())

    def test_second_self_paced_entry_fails_sync(self):
        self._write_course_yaml(
            slug='cohort-mode-direct-3',
            cohorts=[
                {'key': 'sp-1', 'name': 'SP 1', 'mode': 'self_paced'},
                {'key': 'sp-2', 'name': 'SP 2', 'mode': 'self_paced'},
            ],
        )
        self._sync()
        self.assertEqual(
            Cohort.objects.filter(
                course__slug='cohort-mode-direct-3', mode='self_paced',
            ).count(),
            1,
        )

    def test_default_mode_is_cohort(self):
        self._write_course_yaml(
            slug='cohort-mode-direct-4',
            cohorts=[{
                'key': 'dated', 'name': 'Dated', 'start_date': '2026-09-21',
                'end_date': '2026-11-22',
            }],
        )
        self._sync()
        cohort = Cohort.objects.get(course__slug='cohort-mode-direct-4', external_key='dated')
        self.assertEqual(cohort.mode, 'cohort')
