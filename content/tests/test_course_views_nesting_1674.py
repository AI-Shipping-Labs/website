"""View-level tests for curriculum nesting (issue #1674).

Covers:
- /courses/<slug> renders a three-level syllabus with submodule
  accordions and Bonus badges.
- /courses/<slug>/<module_slug> for a parent module shows "Submodules",
  never "This module has no lessons yet.".
- Breadcrumbs on a submodule's unit/overview pages include the parent
  segment; two-level courses are unchanged.
- Cohort week dates render for a cohort learner, not for self-paced/
  anonymous viewers, and no unit is drip-locked for self-paced.
- The course page's "Active Cohorts" block excludes self-paced cohorts;
  api_cohort_enroll/unenroll reject a self-paced cohort id.
- GET /api/courses/<slug> syllabus payload carries parent_id/is_bonus/
  nested modules; unit entries carry kind/is_bonus/session_position.
- Backward compatibility: an existing two-level course renders the same
  key elements as before.
"""

import datetime
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course, Module, Unit

User = get_user_model()


class ThreeLevelCourseViewMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='learner@test.com', password='pw')
        cls.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-views', status='published',
            required_level=0,
        )
        cls.week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
            available_after_days=0,
        )
        cls.foundations = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations',
            sort_order=1, parent=cls.week1,
        )
        cls.bonus_sub = Module.objects.create(
            course=cls.course, title='Bonus topic', slug='bonus-topic',
            sort_order=2, parent=cls.week1, is_bonus=True,
        )
        cls.u_intro = Unit.objects.create(
            module=cls.foundations, title='Intro', slug='intro', sort_order=1,
        )
        cls.u_bonus_unit = Unit.objects.create(
            module=cls.foundations, title='Deep dive', slug='deep-dive',
            sort_order=2, is_bonus=True,
        )


class CourseDetailThreeLevelSyllabusTest(ThreeLevelCourseViewMixin, TestCase):
    def test_syllabus_shows_parent_and_submodule_accordions(self):
        self.client.login(email='learner@test.com', password='pw')
        response = self.client.get('/courses/buildcamp-views')
        self.assertContains(response, 'Week 1', status_code=200)
        self.assertContains(response, 'Foundations')
        self.assertContains(response, 'Bonus topic')
        self.assertContains(response, 'data-testid="syllabus-parent-module"')

    def test_optional_badge_appears_on_submodule_and_standalone_unit(self):
        self.client.login(email='learner@test.com', password='pw')
        response = self.client.get('/courses/buildcamp-views')
        self.assertContains(response, 'data-testid="syllabus-optional-badge"', count=1)
        self.assertContains(response, 'data-testid="syllabus-unit-optional-badge"', count=1)
        self.assertNotContains(response, 'data-testid="syllabus-bonus-divider"')

    def test_optional_parent_suppresses_descendant_badges(self):
        optional_module = Module.objects.create(
            course=self.course, title='Optional module', slug='optional-module',
            sort_order=3, is_bonus=True,
        )
        optional_child = Module.objects.create(
            course=self.course, parent=optional_module, title='Optional child',
            slug='optional-child', sort_order=1, is_bonus=True,
        )
        Unit.objects.create(
            module=optional_child, title='Optional unit', slug='optional-unit',
            sort_order=1, is_bonus=True,
        )
        response = self.client.get('/courses/buildcamp-views')
        self.assertContains(response, 'data-testid="syllabus-optional-badge"', count=2)
        self.assertContains(response, 'data-testid="syllabus-unit-optional-badge"', count=1)
        self.assertNotContains(response, 'data-testid="syllabus-bonus-divider"')


class ModuleOverviewParentTest(ThreeLevelCourseViewMixin, TestCase):
    def test_parent_module_overview_shows_submodules_section(self):
        response = self.client.get('/courses/buildcamp-views/week-1')
        self.assertContains(response, 'data-testid="module-submodule-list"', status_code=200)
        self.assertContains(response, 'Foundations')
        self.assertContains(response, 'Bonus topic')
        self.assertNotContains(response, 'This module has no lessons yet.')

    def test_submodule_overview_shows_lessons_section(self):
        response = self.client.get('/courses/buildcamp-views/week-1/foundations')
        self.assertContains(response, 'data-testid="module-lesson-list"', status_code=200)
        self.assertContains(response, 'Intro')

    def test_submodule_overview_breadcrumb_includes_parent(self):
        response = self.client.get('/courses/buildcamp-views/week-1/foundations')
        self.assertContains(response, 'data-testid="module-breadcrumb-parent"')
        self.assertContains(response, 'Week 1')


class UnitDetailBreadcrumbTest(ThreeLevelCourseViewMixin, TestCase):
    def test_submodule_unit_breadcrumb_includes_parent_segment(self):
        self.client.login(email='learner@test.com', password='pw')
        response = self.client.get('/courses/buildcamp-views/week-1/foundations/intro')
        self.assertContains(response, 'data-testid="breadcrumb-parent-module"', status_code=200)
        self.assertContains(response, 'Week 1')


class TwoLevelCourseBreadcrumbUnchangedTest(TestCase):
    """Two-level courses show no extra breadcrumb segment (issue #1674)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='flat@test.com', password='pw')
        cls.course = Course.objects.create(
            title='Flat Course', slug='flat-course-views', status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson 1', slug='lesson-1', sort_order=1,
        )

    def test_unit_detail_has_no_parent_breadcrumb_segment(self):
        self.client.login(email='flat@test.com', password='pw')
        response = self.client.get('/courses/flat-course-views/module-1/lesson-1')
        self.assertNotContains(response, 'data-testid="breadcrumb-parent-module"', status_code=200)

    def test_module_overview_has_no_parent_breadcrumb_segment(self):
        response = self.client.get('/courses/flat-course-views/module-1')
        self.assertNotContains(response, 'data-testid="module-breadcrumb-parent"')

    def test_module_overview_shows_lessons_not_submodules(self):
        response = self.client.get('/courses/flat-course-views/module-1')
        self.assertContains(response, 'data-testid="module-lesson-list"')
        self.assertNotContains(response, 'data-testid="module-submodule-list"')

    def test_syllabus_accordion_has_no_extra_nesting_or_badges(self):
        self.client.login(email='flat@test.com', password='pw')
        response = self.client.get('/courses/flat-course-views')
        self.assertContains(response, 'data-testid="syllabus-module"')
        self.assertNotContains(response, 'data-testid="syllabus-parent-module"')
        self.assertNotContains(response, 'data-testid="module-bonus-badge"')


class CohortWeekDateDisplayTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Dated Course', slug='dated-course-views', status='published',
            required_level=0,
        )
        cls.week4 = Module.objects.create(
            course=cls.course, title='Week 4', slug='week-4', sort_order=1,
            available_after_days=21,
        )
        cls.week5 = Module.objects.create(
            course=cls.course, title='Week 5', slug='week-5', sort_order=2,
            available_after_days=28,
        )
        Unit.objects.create(
            module=cls.week4, title='U', slug='u', sort_order=1,
            available_after_days=21,
        )
        cls.cohort_user = User.objects.create_user(email='cohort@test.com', password='pw')
        cls.self_paced_user = User.objects.create_user(email='selfpaced@test.com', password='pw')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )
        CohortEnrollment.objects.create(user=cls.cohort_user, cohort=cls.cohort)

    def test_cohort_learner_sees_derived_date_range(self):
        self.client.login(email='cohort@test.com', password='pw')
        response = self.client.get('/courses/dated-course-views')
        self.assertContains(response, 'Oct 12')

    def test_self_paced_viewer_sees_no_date_range(self):
        self.client.login(email='selfpaced@test.com', password='pw')
        response = self.client.get('/courses/dated-course-views')
        self.assertNotContains(response, 'Oct 12')

    def test_self_paced_viewer_unit_is_not_drip_locked(self):
        self.client.login(email='selfpaced@test.com', password='pw')
        response = self.client.get('/courses/dated-course-views/week-4/u')
        self.assertNotContains(response, 'data-testid="drip-locked-card"', status_code=200)

    def test_anonymous_viewer_sees_no_date_range(self):
        response = self.client.get('/courses/dated-course-views')
        self.assertNotContains(response, 'Oct 12')


class SelfPacedCohortEnrollBlockTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Course', slug='self-paced-block-course', status='published',
            required_level=0,
        )
        cls.dated = Cohort.objects.create(
            course=cls.course, name='Dated Cohort', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.user = User.objects.create_user(email='member@test.com', password='pw')

    def test_active_cohorts_block_excludes_self_paced(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.get('/courses/self-paced-block-course')
        self.assertContains(response, 'Dated Cohort')
        self.assertNotContains(response, 'Self-paced')

    def test_api_cohort_enroll_rejects_self_paced_cohort(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            f'/api/courses/self-paced-block-course/cohorts/{self.self_paced.pk}/enroll',
        )
        self.assertEqual(response.status_code, 400)

    def test_api_cohort_unenroll_rejects_self_paced_cohort(self):
        self.client.login(email='member@test.com', password='pw')
        response = self.client.post(
            f'/api/courses/self-paced-block-course/cohorts/{self.self_paced.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 400)


class ApiSyllabusNestingTest(ThreeLevelCourseViewMixin, TestCase):
    def test_syllabus_payload_includes_nested_modules_and_bonus_flags(self):
        response = self.client.get('/api/courses/buildcamp-views')
        data = response.json()
        week1_entry = next(m for m in data['syllabus'] if m['title'] == 'Week 1')
        self.assertIsNone(week1_entry['parent_id'])
        self.assertFalse(week1_entry['is_bonus'])
        self.assertEqual(len(week1_entry['modules']), 2)
        bonus_entry = next(m for m in week1_entry['modules'] if m['title'] == 'Bonus topic')
        self.assertTrue(bonus_entry['is_bonus'])
        self.assertEqual(bonus_entry['parent_id'], week1_entry['id'])
        foundations_entry = next(m for m in week1_entry['modules'] if m['title'] == 'Foundations')
        bonus_unit_entry = next(
            u for u in foundations_entry['units'] if u['title'] == 'Deep dive'
        )
        self.assertTrue(bonus_unit_entry['is_bonus'])
        self.assertEqual(bonus_unit_entry['kind'], 'lesson')

    def test_event_unit_carries_session_position(self):
        event_unit = Unit.objects.create(
            module=self.foundations, title='Live Q&A', slug='live-qa',
            sort_order=3, kind='event', session_position=4,
        )
        response = self.client.get(f'/api/courses/buildcamp-views/units/{event_unit.pk}')
        data = response.json()
        self.assertEqual(data['kind'], 'event')
        self.assertEqual(data['session_position'], 4)

    def test_lesson_unit_omits_session_position_key(self):
        response = self.client.get(f'/api/courses/buildcamp-views/units/{self.u_intro.pk}')
        data = response.json()
        self.assertNotIn('session_position', data)


class TwoLevelApiBackwardCompatTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Flat API Course', slug='flat-api-course', status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        Unit.objects.create(
            module=cls.module, title='Lesson 1', slug='lesson-1', sort_order=1,
        )

    def test_leaf_module_has_no_modules_key_populated(self):
        response = self.client.get('/api/courses/flat-api-course')
        data = response.json()
        module_entry = data['syllabus'][0]
        self.assertIsNone(module_entry['parent_id'])
        self.assertFalse(module_entry['is_bonus'])
        self.assertNotIn('modules', module_entry)
        self.assertEqual(len(module_entry['units']), 1)
        self.assertEqual(module_entry['units'][0]['kind'], 'lesson')


class SubmoduleUrlDisambiguationTest(ThreeLevelCourseViewMixin, TestCase):
    """Issue #1674 (coordinator correction): the URL scheme must resolve
    a submodule unambiguously even though its slug is only unique among
    its own siblings, not course-wide — two submodules sharing a slug
    under different parents is now legal."""

    def test_module_get_absolute_url_top_level_is_unchanged_two_segment(self):
        self.assertEqual(self.week1.get_absolute_url(), '/courses/buildcamp-views/week-1')

    def test_module_get_absolute_url_submodule_embeds_parent(self):
        self.assertEqual(
            self.foundations.get_absolute_url(),
            '/courses/buildcamp-views/week-1/foundations',
        )

    def test_unit_get_absolute_url_under_submodule_embeds_parent(self):
        self.assertEqual(
            self.u_intro.get_absolute_url(),
            '/courses/buildcamp-views/week-1/foundations/intro',
        )

    def test_three_segment_path_resolves_submodule_overview_when_parent_has_children(self):
        response = self.client.get('/courses/buildcamp-views/week-1/foundations')
        self.assertContains(response, 'data-testid="module-lesson-list"', status_code=200)

    def test_four_segment_path_resolves_unit_under_submodule(self):
        self.client.login(email='learner@test.com', password='pw')
        response = self.client.get('/courses/buildcamp-views/week-1/foundations/intro')
        self.assertContains(response, 'Intro', status_code=200)

    def test_two_segment_path_no_longer_resolves_a_submodule_directly(self):
        """A submodule's canonical URL now requires the parent segment —
        the old ambiguous /courses/<course>/<submodule> shape 404s."""
        response = self.client.get('/courses/buildcamp-views/foundations')
        self.assertEqual(response.status_code, 404)

    def test_colliding_submodule_slugs_under_different_parents_both_resolve(self):
        """The exact real-world case this correction unblocks (and the
        tester independently verified manually): two submodules named
        "Homework" under different weeks resolve to distinct, correct
        content rather than colliding or crashing."""
        week2 = Module.objects.create(
            course=self.course, title='Week 2', slug='week-2', sort_order=2,
        )
        week1_hw = Module.objects.create(
            course=self.course, title='Homework', slug='homework',
            sort_order=1, parent=self.week1,
            overview='Week 1 homework overview.',
        )
        week2_hw = Module.objects.create(
            course=self.course, title='Homework', slug='homework',
            sort_order=1, parent=week2,
            overview='Week 2 homework overview.',
        )
        Unit.objects.create(
            module=week1_hw, title='Week 1 assignment', slug='assignment', sort_order=1,
        )
        Unit.objects.create(
            module=week2_hw, title='Week 2 assignment', slug='assignment', sort_order=1,
        )

        response1 = self.client.get('/courses/buildcamp-views/week-1/homework')
        response2 = self.client.get('/courses/buildcamp-views/week-2/homework')

        self.assertContains(response1, 'Week 1 homework overview.', status_code=200)
        self.assertNotContains(response1, 'Week 2 homework overview.')
        self.assertContains(response1, 'Week 1 assignment')
        self.assertNotContains(response1, 'Week 2 assignment')

        self.assertContains(response2, 'Week 2 homework overview.', status_code=200)
        self.assertNotContains(response2, 'Week 1 homework overview.')
        self.assertContains(response2, 'Week 2 assignment')
        self.assertNotContains(response2, 'Week 1 assignment')


class TwoLevelCourseUrlUnchangedTest(TestCase):
    """Existing two-level (published, indexed) course URLs must not
    change at all — the buildcamp is live with paying students."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Flat Course', slug='flat-course-url', status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Lesson 1', slug='lesson-1', sort_order=1,
        )
        cls.user = User.objects.create_user(email='flat-url@test.com', password='pw')

    def test_module_url_unchanged(self):
        self.assertEqual(
            self.module.get_absolute_url(), '/courses/flat-course-url/module-1',
        )

    def test_unit_url_unchanged(self):
        self.assertEqual(
            self.unit.get_absolute_url(),
            '/courses/flat-course-url/module-1/lesson-1',
        )

    def test_module_overview_page_still_resolves(self):
        response = self.client.get('/courses/flat-course-url/module-1')
        self.assertContains(response, 'Module 1', status_code=200)

    def test_unit_detail_page_still_resolves(self):
        self.client.login(email='flat-url@test.com', password='pw')
        response = self.client.get('/courses/flat-course-url/module-1/lesson-1')
        self.assertContains(response, 'Lesson 1', status_code=200)


class SyllabusIncludeContextLeakRegressionTest(TestCase):
    """Issue #1674 (tester-caught defect): the recursive
    ``content/_syllabus_module.html`` include must not leak top-level
    -only context (``counter``, ``module_week_range``) into submodule
    renders — a plain ``{% include %}`` passes the whole calling context
    down by default, so every submodule inherited the parent's position
    chip and, worse, its derived week date range."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='leak-regression-course', status='published',
            required_level=0,
        )
        cls.week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
            available_after_days=21,
        )
        cls.docker = Module.objects.create(
            course=cls.course, title='Docker', slug='docker', sort_order=1,
            parent=cls.week1,
        )
        cls.kubernetes = Module.objects.create(
            course=cls.course, title='Kubernetes', slug='kubernetes', sort_order=2,
            parent=cls.week1,
        )
        cls.extra_tools = Module.objects.create(
            course=cls.course, title='Extra Tools', slug='extra-tools', sort_order=3,
            parent=cls.week1,
        )
        cls.user = User.objects.create_user(email='leak@test.com', password='pw')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort', mode='cohort',
            start_date=datetime.date(2026, 9, 21), end_date=datetime.date(2026, 11, 22),
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort)

    def test_position_chip_renders_exactly_once_for_the_top_level_module(self):
        self.client.login(email='leak@test.com', password='pw')
        response = self.client.get('/courses/leak-regression-course')
        self.assertContains(
            response, 'data-testid="syllabus-module-position-chip"',
            count=1, status_code=200,
        )

    def test_top_level_bonus_has_no_position_chip(self):
        Module.objects.create(
            course=self.course, title='Bonus', slug='bonus', sort_order=2,
            is_bonus=True,
        )
        self.client.login(email='leak@test.com', password='pw')
        response = self.client.get('/courses/leak-regression-course')
        self.assertNotContains(response, 'module-bonus-badge', status_code=200)
        self.assertContains(
            response, 'data-testid="syllabus-module-position-chip"',
            count=1, status_code=200,
        )

    def test_week_date_range_renders_exactly_once_for_the_top_level_module(self):
        """Not once per submodule — with a dated cohort, Week 1's derived
        range must not repeat under Docker/Kubernetes/Extra Tools."""
        self.client.login(email='leak@test.com', password='pw')
        response = self.client.get('/courses/leak-regression-course')
        self.assertContains(
            response, 'data-testid="syllabus-module-week-range"',
            count=1, status_code=200,
        )


class _SyllabusChipParser(HTMLParser):
    """Read position chips from individual syllabus summary elements."""

    def __init__(self):
        super().__init__()
        self.positions = []
        self._summary_position = None
        self._in_summary = False
        self._in_chip = False

    def handle_starttag(self, tag, attrs):
        testid = dict(attrs).get('data-testid')
        if tag == 'summary' and testid == 'syllabus-module-summary':
            self._in_summary = True
            self._summary_position = None
        elif self._in_summary and testid == 'syllabus-module-position-chip':
            self._in_chip = True

    def handle_data(self, data):
        if self._in_chip and data.strip():
            self._summary_position = int(data.strip())

    def handle_endtag(self, tag):
        if tag == 'span' and self._in_chip:
            self._in_chip = False
        elif tag == 'summary' and self._in_summary:
            self.positions.append(self._summary_position)
            self._in_summary = False


class SyllabusBuildcampPositionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for slug in ('ai-buildcamp', 'other-course'):
            course = Course.objects.create(
                title=slug, slug=slug, status='published', required_level=0,
            )
            for position, (title, module_slug, is_bonus) in enumerate((
                ('Course Logistics', 'logistics', False),
                ('Foundations', 'week-1', False),
                ('RAG in practice', 'week-2', False),
                ('Bonus', 'bonus', True),
            )):
                Module.objects.create(
                    course=course, title=title, slug=module_slug,
                    sort_order=position, is_bonus=is_bonus,
                )

    def _positions(self, slug):
        response = self.client.get(f'/courses/{slug}')
        parser = _SyllabusChipParser()
        parser.feed(response.content.decode())
        return parser.positions

    def test_buildcamp_logistics_and_bonus_are_unnumbered(self):
        self.assertEqual(self._positions('ai-buildcamp'), [None, None, None, None])
        response = self.client.get('/courses/ai-buildcamp')
        self.assertContains(response, 'Week 1 · ')
        self.assertContains(response, 'Week 2 · ')
        self.assertContains(response, 'data-testid="syllabus-optional-group">Optional</h3>')
        self.assertNotContains(response, 'data-testid="syllabus-optional-badge"')

    def test_other_courses_keep_their_position_numbers(self):
        self.assertEqual(self._positions('other-course'), [1, 2, 3, None])

    def test_buildcamp_capstone_card_does_not_repeat_week_label(self):
        course = Course.objects.get(slug='ai-buildcamp')
        Module.objects.create(
            course=course, title='Capstone Project', slug='capstone-project',
            sort_order=7,
        )
        response = self.client.get('/courses/ai-buildcamp')
        self.assertContains(response, 'Capstone Project')
        self.assertNotContains(response, 'Week 7 · ')
