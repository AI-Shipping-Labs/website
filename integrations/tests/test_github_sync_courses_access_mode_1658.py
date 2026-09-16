"""GitHub sync tests for the issue #1658 access_mode course.yaml key.

Covers:
- absent key leaves access_mode='tier' (no regression for existing courses)
- access_mode/enroll_url/program_label sync from course.yaml when present
- unknown access_mode value fails the sync with a clear per-course error
- access_mode: entitlement with no enroll_url fails the sync
- access_mode: entitlement with required_level or default_unit_access below
  Basic fails the sync (QA follow-up: this is the exact combination shipped
  in the real ~/git/ai-buildcamp-course/course.yaml -- required_level: 0 +
  default_unit_access: registered -- which would otherwise make the
  entitlement gate a silent no-op; can_access() grants LEVEL_OPEN /
  LEVEL_REGISTERED content before the entitlement/tier branch ever runs)
- pure-helper coverage of _resolve_access_mode
"""

from django.test import TestCase

from content.models import Course
from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.courses import _resolve_access_mode
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class ResolveAccessModeHelperTest(TestCase):
    """Pure-helper tests: _resolve_access_mode validates and normalizes."""

    def test_absent_key_defaults_to_tier(self):
        self.assertEqual(
            _resolve_access_mode({}, 'courses/foo'), 'tier',
        )

    def test_named_tier_accepted(self):
        self.assertEqual(
            _resolve_access_mode({'access_mode': 'tier'}, 'courses/foo'), 'tier',
        )

    def test_named_entitlement_accepted_with_enroll_url(self):
        self.assertEqual(
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=20,
            ),
            'entitlement',
        )

    def test_case_insensitive(self):
        self.assertEqual(
            _resolve_access_mode(
                {'access_mode': 'ENTITLEMENT', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=20,
            ),
            'entitlement',
        )

    def test_unknown_value_raises(self):
        with self.assertRaises(GitHubSyncError) as cm:
            _resolve_access_mode({'access_mode': 'grant'}, 'courses/foo')
        msg = str(cm.exception)
        self.assertIn('grant', msg)
        self.assertIn('access_mode', msg)

    def test_entitlement_without_enroll_url_raises(self):
        with self.assertRaises(GitHubSyncError) as cm:
            _resolve_access_mode({'access_mode': 'entitlement'}, 'courses/foo')
        msg = str(cm.exception)
        self.assertIn('enroll_url', msg)

    def test_entitlement_with_blank_enroll_url_raises(self):
        with self.assertRaises(GitHubSyncError):
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': '   '},
                'courses/foo',
            )

    def test_entitlement_with_required_level_below_basic_raises(self):
        """QA follow-up: required_level defaults to 0 (LEVEL_OPEN) when the
        caller doesn't pass it, and 0 < Basic must be refused -- can_access()
        grants LEVEL_OPEN content before the entitlement branch ever runs.
        """
        with self.assertRaises(GitHubSyncError) as cm:
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=0,
            )
        msg = str(cm.exception)
        self.assertIn('required_level', msg)

    def test_entitlement_with_default_unit_required_level_below_basic_raises(self):
        """QA follow-up: default_unit_access is the field that actually
        controls per-lesson readability, and it can be set independently of
        required_level -- this is the exact hole a course-level-only check
        would miss.
        """
        with self.assertRaises(GitHubSyncError) as cm:
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=20,
                default_unit_required_level=5,
            )
        msg = str(cm.exception)
        self.assertIn('default_unit_access', msg)

    def test_entitlement_with_both_levels_basic_or_above_accepted(self):
        self.assertEqual(
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=20,
                default_unit_required_level=20,
            ),
            'entitlement',
        )

    def test_entitlement_with_default_unit_required_level_none_only_checks_required_level(self):
        """default_unit_required_level=None means units inherit
        required_level (legacy fallback) -- no separate value to validate.
        """
        self.assertEqual(
            _resolve_access_mode(
                {'access_mode': 'entitlement', 'enroll_url': 'https://maven.com/x'},
                'courses/foo',
                required_level=20,
                default_unit_required_level=None,
            ),
            'entitlement',
        )


class _SyncCourseFixture:
    """Minimal helper that writes a course/module/unit tree."""

    def __init__(self, repo, *, course_data=None):
        self.repo = repo
        self.course_data = course_data or {}

    def write(self):
        course_payload = {
            'title': 'Course 1658',
            'slug': 'course-1658',
            'description': 'desc',
            'instructor_name': 'I',
            'required_level': 20,
            'content_id': '33333333-3333-3333-3333-333333333333',
            **self.course_data,
        }
        self.repo.write_yaml('course-1658/course.yaml', course_payload)
        self.repo.write_yaml(
            'course-1658/01-module/module.yaml',
            {'title': 'Module', 'sort_order': 1},
        )
        self.repo.write_markdown(
            'course-1658/01-module/01-unit.md',
            {
                'title': 'Unit 1', 'sort_order': 1,
                'content_id': '44444444-4444-4444-4444-444444444444',
            },
            'Hello\n',
        )


class CourseAccessModeSyncTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/courses-1658', prefix='access-1658-',
        )

    def _sync(self, *, course_data=None):
        _SyncCourseFixture(self.repo, course_data=course_data).write()
        return sync_repo(self.source, self.repo)

    def test_absent_access_mode_leaves_tier_no_regression(self):
        self._sync()
        course = Course.objects.get(slug='course-1658')
        self.assertEqual(course.access_mode, 'tier')
        self.assertEqual(course.enroll_url, '')
        self.assertEqual(course.program_label, '')

    def test_entitlement_access_mode_synced_with_enroll_url_and_program_label(self):
        self._sync(course_data={
            'access_mode': 'entitlement',
            'enroll_url': 'https://maven.com/alexey-grigorev/from-rag-to-agents',
            'program_label': 'Maven',
        })
        course = Course.objects.get(slug='course-1658')
        self.assertEqual(course.access_mode, 'entitlement')
        self.assertEqual(
            course.enroll_url,
            'https://maven.com/alexey-grigorev/from-rag-to-agents',
        )
        self.assertEqual(course.program_label, 'Maven')

    def test_unknown_access_mode_fails_sync_with_clear_error(self):
        log = self._sync(course_data={'access_mode': 'grant'})
        self.assertFalse(Course.objects.filter(slug='course-1658').exists())
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join((e.get('error') or '') for e in (log.errors or []))
        self.assertIn('access_mode', errors_text)
        self.assertIn('grant', errors_text)

    def test_entitlement_without_enroll_url_fails_sync(self):
        log = self._sync(course_data={'access_mode': 'entitlement'})
        self.assertFalse(Course.objects.filter(slug='course-1658').exists())
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join((e.get('error') or '') for e in (log.errors or []))
        self.assertIn('enroll_url', errors_text)

    def test_entitlement_with_required_level_below_basic_fails_sync(self):
        log = self._sync(course_data={
            'required_level': 0,
            'access_mode': 'entitlement',
            'enroll_url': 'https://maven.com/x',
        })
        self.assertFalse(Course.objects.filter(slug='course-1658').exists())
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join((e.get('error') or '') for e in (log.errors or []))
        self.assertIn('required_level', errors_text)

    def test_entitlement_with_default_unit_access_below_basic_fails_sync(self):
        """A Basic+ required_level alone is not enough -- default_unit_access
        is the field that actually governs per-lesson readability and can be
        set independently. This proves a course-level-only check would have
        missed the real hole.
        """
        log = self._sync(course_data={
            'required_level': 20,
            'default_unit_access': 'registered',
            'access_mode': 'entitlement',
            'enroll_url': 'https://maven.com/x',
        })
        self.assertFalse(Course.objects.filter(slug='course-1658').exists())
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join((e.get('error') or '') for e in (log.errors or []))
        self.assertIn('default_unit_access', errors_text)

    def test_entitlement_matches_real_buildcamp_yaml_fails_sync(self):
        """QA regression: the exact combination shipped in the real
        ~/git/ai-buildcamp-course/course.yaml today -- required_level: 0 +
        default_unit_access: registered, both explicitly flagged in that
        file as placeholders "pending a real pricing/cohort-access
        decision". If access_mode: entitlement were added on top of that
        unchanged, the sync must refuse it rather than publish a course
        that renders "External course" copy while can_access() grants every
        anonymous or signed-in visitor full lesson access.
        """
        log = self._sync(course_data={
            'title': 'AI Engineering Buildcamp',
            'required_level': 0,
            'default_unit_access': 'registered',
            'access_mode': 'entitlement',
            'enroll_url': 'https://maven.com/alexey-grigorev/from-rag-to-agents',
            'program_label': 'Maven',
        })
        self.assertFalse(
            Course.objects.filter(
                slug='course-1658', access_mode='entitlement',
            ).exists(),
            'sync_content published an entitlement-mode course whose '
            'effective unit gating is below Basic -- can_access() grants '
            'this to every anonymous visitor, making the entitlement gate '
            'a complete no-op.',
        )
        self.assertEqual(log.status, 'partial')
        errors_text = ' '.join((e.get('error') or '') for e in (log.errors or []))
        self.assertIn('required_level', errors_text)

    def test_resync_after_removing_access_mode_reverts_to_tier(self):
        self._sync(course_data={
            'access_mode': 'entitlement',
            'enroll_url': 'https://maven.com/x',
        })
        course = Course.objects.get(slug='course-1658')
        self.assertEqual(course.access_mode, 'entitlement')

        self.repo.write_yaml(
            'course-1658/course.yaml',
            {
                'title': 'Course 1658',
                'slug': 'course-1658',
                'description': 'desc',
                'instructor_name': 'I',
                'required_level': 20,
                'content_id': '33333333-3333-3333-3333-333333333333',
            },
        )
        sync_repo(self.source, self.repo)
        course.refresh_from_db()
        self.assertEqual(course.access_mode, 'tier')
