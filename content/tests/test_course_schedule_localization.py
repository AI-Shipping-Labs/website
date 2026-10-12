"""Issue #1795: one display-zone rule for course schedule timestamps.

A valid saved signed-in ``preferred_timezone`` wins and blocks client
localization; an empty or invalid stored value (and anonymous visitors)
gets the explicit server fallback plus ``schedule_can_localize=True`` so
the browser zone may rewrite the render on that first page load. The
underlying UTC instants never change: every converted label keeps a
semantic ``<time datetime="...Z">`` attribute, and DST boundaries convert
with the offset applicable at each instant.
"""

from datetime import date, datetime
from datetime import timezone as dt_timezone
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import Cohort, CohortEnrollment, Course, Homework, Module, Unit
from content.models.peer_review import CourseProject, PeerReview, ProjectSubmission
from content.services.course_schedule import schedule_timezone_name
from content.templatetags.schedule_tags import (
    schedule_datetime_local,
    schedule_instant,
)

DUE = datetime(2026, 10, 5, 23, 59, 59, tzinfo=dt_timezone.utc)
DUE_INSTANT_ATTR = 'datetime="2026-10-05T23:59:59Z"'
BERLIN_NOTICE = (
    'Deadline times are shown in '
    '<span data-schedule-zone-label>Europe/Berlin</span>.'
)


class ScheduleZonePolicyTest(TestCase):
    """Service-level zone decision and DST-true conversion."""

    @classmethod
    def setUpTestData(cls):
        cls.buildcamp = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0,
        )
        cls.other = Course.objects.create(
            title='Other', slug='other-course', status='published',
            required_level=0,
        )
        cls.user = get_user_model().objects.create_user(email='zone@example.com')

    def test_valid_saved_preference_wins(self):
        self.user.preferred_timezone = 'America/New_York'
        self.assertEqual(
            schedule_timezone_name(self.buildcamp, self.user), 'America/New_York',
        )

    def test_empty_or_invalid_preference_uses_buildcamp_fallback(self):
        self.user.preferred_timezone = ''
        self.assertEqual(
            schedule_timezone_name(self.buildcamp, self.user), 'Europe/Berlin',
        )
        self.user.preferred_timezone = 'Mars/Olympus'
        self.assertEqual(
            schedule_timezone_name(self.buildcamp, self.user), 'Europe/Berlin',
        )

    def test_non_buildcamp_falls_back_to_utc(self):
        self.user.preferred_timezone = ''
        self.assertEqual(schedule_timezone_name(self.other, self.user), 'UTC')

    def test_dst_boundary_instants_convert_per_zone(self):
        # 23:59:59Z before the DST change: already next day in Berlin.
        self.assertEqual(
            schedule_datetime_local(DUE, 'Europe/Berlin'), 'Oct 6, 2026 01:59',
        )
        self.assertEqual(
            schedule_datetime_local(DUE, 'America/New_York'), 'Oct 5, 2026 19:59',
        )
        # After the DST change the offsets shift by one hour in both zones.
        after = datetime(2026, 11, 2, 23, 59, 59, tzinfo=dt_timezone.utc)
        self.assertEqual(
            schedule_datetime_local(after, 'Europe/Berlin'), 'Nov 3, 2026 00:59',
        )
        self.assertEqual(
            schedule_datetime_local(after, 'America/New_York'), 'Nov 2, 2026 18:59',
        )
        self.assertEqual(schedule_instant(DUE), '2026-10-05T23:59:59Z')


class ScheduleZoneRenderingTest(TestCase):
    """Page-level: fallback HTML and localize flag per viewer state."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0, peer_review_enabled=True,
        )
        cls.week = Module.objects.create(
            course=cls.course, title='Foundation', slug='foundation',
            sort_order=1, available_after_days=0,
        )
        cls.topic = Module.objects.create(
            course=cls.course, parent=cls.week, title='First topic',
            slug='first-topic', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.topic, title='Homework 1', slug='homework-1',
            sort_order=1, kind='homework', content_id=uuid4(),
        )
        cls.c4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        Homework.objects.create(
            cohort=cls.c4, content_id=cls.unit.content_id, slug='hw1',
            title='Homework 1', due_date=DUE,
        )
        cls.attempt = CourseProject.objects.create(
            course=cls.course, cohort=cls.c4, module=cls.topic,
            slug='attempt-4', title='Attempt 4',
            submission_due_at=DUE, review_due_at=DUE,
        )
        user_model = get_user_model()
        cls.saved = user_model.objects.create_user(
            email='saved@example.com', password='pw',
        )
        cls.saved.preferred_timezone = 'America/New_York'
        cls.saved.save()
        cls.empty = user_model.objects.create_user(
            email='empty@example.com', password='pw',
        )
        cls.invalid = user_model.objects.create_user(
            email='invalid@example.com', password='pw',
        )
        cls.invalid.preferred_timezone = 'Not/AZone'
        cls.invalid.save()
        for user in (cls.saved, cls.empty, cls.invalid):
            CohortEnrollment.objects.create(user=user, cohort=cls.c4)

    def _detail(self):
        return self.client.get('/courses/ai-buildcamp?view=overview')

    def test_saved_preference_renders_new_york_and_blocks_localization(self):
        self.client.force_login(self.saved)
        response = self._detail()
        self.assertContains(response, 'Oct 5, 2026 19:59')
        self.assertEqual(response.context['schedule_timezone'], 'America/New_York')
        self.assertFalse(response.context['schedule_can_localize'])
        html = response.content.decode()
        self.assertNotIn('data-localize="true"', html)
        self.assertIn(DUE_INSTANT_ATTR, html)
        self.assertIn('Shown in your timezone.', html)

    def test_first_visit_renders_explicit_berlin_fallback_and_localize_flag(self):
        self.client.force_login(self.empty)
        response = self._detail()
        self.assertContains(response, 'Oct 6, 2026 01:59')
        self.assertEqual(response.context['schedule_timezone'], 'Europe/Berlin')
        self.assertTrue(response.context['schedule_can_localize'])
        html = response.content.decode()
        self.assertIn('data-localize="true"', html)
        self.assertIn(DUE_INSTANT_ATTR, html)
        self.assertIn(BERLIN_NOTICE, html)

    def test_invalid_stored_preference_falls_back_safely(self):
        self.client.force_login(self.invalid)
        response = self._detail()
        self.assertContains(response, BERLIN_NOTICE)
        self.assertEqual(response.context['schedule_timezone'], 'Europe/Berlin')
        self.assertTrue(response.context['schedule_can_localize'])

    def test_anonymous_preview_gets_fallback_plus_localize_flag(self):
        response = self._detail()
        self.assertContains(response, 'Oct 6, 2026 01:59')
        self.assertTrue(response.context['schedule_can_localize'])
        html = response.content.decode()
        self.assertIn('data-localize="true"', html)
        self.assertIn(BERLIN_NOTICE, html)

    def test_module_overview_exposes_localize_flag(self):
        self.client.force_login(self.empty)
        response = self.client.get('/courses/ai-buildcamp/foundation')
        # The module work rows carry the same UTC instant inside a
        # localizable schedule root, so they rewrite with the syllabus.
        self.assertContains(
            response,
            '<time datetime="2026-10-05T23:59:59+00:00" data-schedule-instant'
            ' data-schedule-format="short">',
        )
        self.assertTrue(response.context['schedule_can_localize'])
        self.assertContains(response, 'data-localize="true"')

    def test_attempt_submit_header_shares_the_schedule_zone_rule(self):
        self.client.force_login(self.empty)
        url = reverse(
            'course_project_submit',
            kwargs={'slug': 'ai-buildcamp', 'attempt_slug': 'attempt-4'},
        )
        response = self.client.get(url)
        self.assertContains(response, DUE_INSTANT_ATTR)
        self.assertEqual(response.context['schedule_timezone'], 'Europe/Berlin')
        self.assertTrue(response.context['schedule_can_localize'])
        html = response.content.decode()
        self.assertIn('data-localize="true"', html)
        self.assertIn('Oct 6, 2026 01:59', html)
        self.assertIn('data-schedule-zone-label', html)

    def test_homework_reader_localizes_due_line_on_first_visit(self):
        self.client.force_login(self.empty)
        response = self.client.get(self.unit.get_absolute_url())
        self.assertContains(response, DUE_INSTANT_ATTR)
        self.assertTrue(response.context['schedule_can_localize'])
        html = response.content.decode()
        self.assertIn('data-localize="true"', html)
        self.assertIn(
            '<time datetime="2026-10-05T23:59:59Z" data-schedule-instant'
            ' data-schedule-format="zone">'
            'October 6, 2026, 01:59 Europe/Berlin</time>', html)
        self.assertIn(BERLIN_NOTICE, html)

    def test_homework_reader_keeps_saved_preference_and_blocks_localization(self):
        self.client.force_login(self.saved)
        response = self.client.get(self.unit.get_absolute_url())
        self.assertContains(
            response, 'October 5, 2026, 19:59 America/New_York')
        html = response.content.decode()
        self.assertNotIn('data-localize="true"', html)
        self.assertIn('Shown in your timezone.', html)

    def test_attempt_submit_header_uses_saved_preference_without_localizing(self):
        self.client.force_login(self.saved)
        url = reverse(
            'course_project_submit',
            kwargs={'slug': 'ai-buildcamp', 'attempt_slug': 'attempt-4'},
        )
        response = self.client.get(url)
        self.assertContains(
            response,
            '<time datetime="2026-10-05T23:59:59Z" data-schedule-instant>'
            'Oct 5, 2026 19:59</time> '
            '<span data-schedule-zone-label>America/New_York</span>',
        )
        self.assertNotContains(response, 'data-localize="true"')

    def _peer_review_pages(self, user):
        """Reviews dashboard and assigned review form for ``user``."""
        author = get_user_model().objects.create_user(email=f'author-{user.pk}@example.com')
        CohortEnrollment.objects.create(user=author, cohort=self.c4)
        authored = ProjectSubmission.objects.create(
            user=author, course=self.course, course_project=self.attempt,
            cohort=self.c4, project_url='https://github.com/example/a',
        )
        ProjectSubmission.objects.create(
            user=user, course=self.course, course_project=self.attempt,
            cohort=self.c4, project_url='https://github.com/example/b',
        )
        PeerReview.objects.create(submission=authored, reviewer=user)
        self.client.force_login(user)
        kwargs = {'slug': 'ai-buildcamp', 'attempt_slug': 'attempt-4'}
        dashboard = self.client.get(reverse('course_project_reviews', kwargs=kwargs))
        form = self.client.get(reverse(
            'course_project_review_form',
            kwargs={**kwargs, 'submission_id': authored.pk},
        ))
        return dashboard, form

    def test_review_pages_render_explicit_fallback_on_first_visit(self):
        dashboard, form = self._peer_review_pages(self.invalid)
        header = (
            '<time datetime="2026-10-05T23:59:59Z" data-schedule-instant>'
            'Oct 6, 2026 01:59</time> '
            '<span data-schedule-zone-label>Europe/Berlin</span>'
        )
        for response in (dashboard, form):
            self.assertContains(response, header)
            self.assertContains(response, BERLIN_NOTICE)
            self.assertContains(response, 'data-localize="true"')
            self.assertNotContains(response, 'Shown in your timezone.')
            self.assertNotContains(response, '23:59 UTC')

    def test_review_pages_use_saved_preference_without_localizing(self):
        dashboard, form = self._peer_review_pages(self.saved)
        header = (
            '<time datetime="2026-10-05T23:59:59Z" data-schedule-instant>'
            'Oct 5, 2026 19:59</time> '
            '<span data-schedule-zone-label>America/New_York</span>'
        )
        for response in (dashboard, form):
            self.assertContains(response, header)
            self.assertNotContains(response, 'data-localize="true"')


class CohortSwitchAcrossDstTest(TestCase):
    """Each owned cohort shows only its own deadlines, DST-correct."""

    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published',
            required_level=0,
        )
        week = Module.objects.create(
            course=course, title='Foundation', slug='foundation',
            sort_order=1, available_after_days=0,
        )
        unit = Unit.objects.create(
            module=week, title='Homework 1', slug='homework-1',
            sort_order=1, kind='homework', content_id=uuid4(),
        )
        before = Cohort.objects.create(
            course=course, name='Cohort 4', external_key='4',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        after = Cohort.objects.create(
            course=course, name='Cohort 5', external_key='5',
            start_date=date(2026, 10, 26), end_date=date(2026, 12, 20),
        )
        Homework.objects.create(
            cohort=before, content_id=unit.content_id, slug='hw1-c4',
            title='Homework 1', due_date=DUE,
        )
        Homework.objects.create(
            cohort=after, content_id=unit.content_id, slug='hw1-c5',
            title='Homework 1',
            due_date=datetime(2026, 11, 2, 23, 59, 59, tzinfo=dt_timezone.utc),
        )
        cls.user = get_user_model().objects.create_user(
            email='two-cohorts@example.com', password='pw',
        )
        cls.user.preferred_timezone = 'America/New_York'
        cls.user.save()
        for cohort in (before, after):
            CohortEnrollment.objects.create(user=cls.user, cohort=cohort)

    def _deadline(self, key):
        self.client.force_login(self.user)
        return self.client.get(f'/courses/ai-buildcamp?view=overview&cohort={key}')

    def test_cohort_before_dst_change(self):
        response = self._deadline('4')
        self.assertContains(
            response,
            '<time datetime="2026-10-05T23:59:59Z" data-schedule-instant>'
            'Oct 5, 2026 19:59</time>',
        )
        self.assertNotContains(response, '2026-11-02T23:59:59Z')

    def test_cohort_after_dst_change(self):
        response = self._deadline('5')
        self.assertContains(
            response,
            '<time datetime="2026-11-02T23:59:59Z" data-schedule-instant>'
            'Nov 2, 2026 18:59</time>',
        )
        self.assertNotContains(response, '2026-10-05T23:59:59Z')
