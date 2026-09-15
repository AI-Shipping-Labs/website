"""Studio cohort list/edit pages (issue #1660).

``Cohort`` was Django-admin-inline only before this issue. Covers the new
``/studio/courses/<course_id>/cohorts/`` list page, the inline create
form, and the ``/studio/courses/<course_id>/cohorts/<cohort_id>/edit``
edit page — including setting/clearing the new ``event_series`` link.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import Cohort, Course
from events.models import EventSeries

User = get_user_model()


class StaffMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='studio-cohort-staff@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='studio-cohort-staff@test.com', password='pass')


class CohortsListPageTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Studio Cohort Course', slug='studio-cohort-course-1660',
            status='published',
        )

    def _url(self):
        return f'/studio/courses/{self.course.pk}/cohorts/'

    def test_empty_state_when_no_cohorts(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'No cohorts for this course yet.')

    def test_lists_existing_cohorts_with_pagination_context(self):
        Cohort.objects.create(
            course=self.course, name='Cohort A',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
        )
        response = self.client.get(self._url())
        self.assertContains(response, 'Cohort A')
        self.assertContains(response, 'data-testid="cohort-row"')
        self.assertIn('show_pager', response.context)

    def test_non_staff_cannot_access(self):
        User.objects.create_user(
            email='non-staff-cohort@test.com', password='pass',
        )
        self.client.logout()
        self.client.login(email='non-staff-cohort@test.com', password='pass')
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)


class CohortCreateViewTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Create Cohort Course', slug='create-cohort-course-1660',
            status='published',
        )
        cls.series = EventSeries.objects.create(
            name='Office Hours 1660', slug='office-hours-create-1660',
            cadence='none', day_of_week=None, start_time=None,
            visibility='hidden',
        )

    def test_create_cohort_with_event_series_link(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/cohorts/create',
            {
                'name': 'September 2026',
                'start_date': '2026-09-01',
                'end_date': '2026-12-01',
                'event_series_id': str(self.series.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        cohort = Cohort.objects.get(course=self.course, name='September 2026')
        self.assertEqual(cohort.event_series_id, self.series.pk)

    def test_create_cohort_without_event_series(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/cohorts/create',
            {
                'name': 'No Series Cohort',
                'start_date': '2026-09-01',
                'end_date': '2026-12-01',
                'event_series_id': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        cohort = Cohort.objects.get(course=self.course, name='No Series Cohort')
        self.assertIsNone(cohort.event_series)

    def test_create_requires_name_and_dates(self):
        before = Cohort.objects.count()
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/cohorts/create',
            {'name': '', 'start_date': '', 'end_date': ''},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cohort.objects.count(), before)


class CohortEditViewTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.course = Course.objects.create(
            title='Edit Cohort Course', slug='edit-cohort-course-1660',
            status='published',
        )
        cls.series = EventSeries.objects.create(
            name='Edit Series 1660', slug='edit-series-1660',
            cadence='none', day_of_week=None, start_time=None,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Editable Cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
        )

    def _url(self):
        return (
            f'/studio/courses/{self.course.pk}/cohorts/{self.cohort.pk}/edit'
        )

    def test_get_renders_form_with_current_values(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'Editable Cohort')
        self.assertContains(response, 'data-testid="cohort-edit-form"')

    def test_setting_event_series_link_persists(self):
        response = self.client.post(self._url(), {
            'name': 'Editable Cohort',
            'start_date': '2026-09-01',
            'end_date': '2026-12-01',
            'is_active': 'on',
            'max_participants': '',
            'event_series_id': str(self.series.pk),
        })
        self.assertEqual(response.status_code, 302)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.series.pk)

    def test_reopening_edit_page_shows_saved_series_selected(self):
        self.cohort.event_series = self.series
        self.cohort.save()
        response = self.client.get(self._url())
        self.assertContains(
            response,
            f'<option value="{self.series.pk}" selected>',
        )

    def test_clearing_event_series_link_persists(self):
        self.cohort.event_series = self.series
        self.cohort.save()
        response = self.client.post(self._url(), {
            'name': 'Editable Cohort',
            'start_date': '2026-09-01',
            'end_date': '2026-12-01',
            'is_active': 'on',
            'max_participants': '',
            'event_series_id': '',
        })
        self.assertEqual(response.status_code, 302)
        self.cohort.refresh_from_db()
        self.assertIsNone(self.cohort.event_series)

    def test_max_participants_and_is_active_persist(self):
        response = self.client.post(self._url(), {
            'name': 'Editable Cohort',
            'start_date': '2026-09-01',
            'end_date': '2026-12-01',
            'max_participants': '25',
            'event_series_id': '',
        })
        self.assertEqual(response.status_code, 302)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.max_participants, 25)
        self.assertFalse(self.cohort.is_active)


class CourseEditPageLinksToCohortsTest(StaffMixin, TestCase):
    def test_course_edit_page_links_to_cohorts_list(self):
        course = Course.objects.create(
            title='Linked Course', slug='linked-course-1660',
            status='published',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'data-testid="panel-manage-cohorts"')
        self.assertContains(
            response, f'/studio/courses/{course.pk}/cohorts/',
        )
