"""Staff cohort API: ``GET /api/courses/<slug>/cohorts`` and
``PATCH /api/courses/<slug>/cohorts/<key>``.

Written after Cohort 4's sessions all showed "Not scheduled" in
production because the cohort was not linked to its office-hours series
and no API exposed cohorts.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from events.models import Event, EventSeries

User = get_user_model()


class CourseCohortsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Relative dates keep Cohort 4 current, so the series rule applies.
        cls.start = timezone.localdate() - datetime.timedelta(days=7)
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp', status='published',
        )
        module = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Session 1', slug='session-1',
            sort_order=1, kind='event', session_position=1,
        )
        cls.series = EventSeries.objects.create(
            name='Buildcamp office hours - Cohort 4',
            slug='buildcamp-office-hours-cohort-4',
            cadence='none', day_of_week=None, start_time=None,
        )
        Event.objects.create(
            title='Office hours 1', slug='oh-c4-1', event_series=cls.series,
            series_position=1, status='upcoming', published=True,
            start_datetime=timezone.now() + datetime.timedelta(days=1),
            origin='studio',
        )
        cls.duplicate = EventSeries.objects.create(
            name='[DELETE ME in Studio] duplicate',
            slug='buildcamp-office-hours-cohort-4-dup',
            cadence='none', day_of_week=None, start_time=None,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', mode='cohort',
            external_key='4',
            start_date=cls.start,
            end_date=cls.start + datetime.timedelta(days=56),
            event_series=cls.duplicate,
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.member)

    def _auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Token {(token or self.staff_token).key}'}

    def _patch(self, body, key='4', slug='ai-buildcamp'):
        return self.client.patch(
            f'/api/courses/{slug}/cohorts/{key}',
            data=json.dumps(body), content_type='application/json',
            **self._auth(),
        )

    def test_list_reports_series_counts_enrollments_and_warnings(self):
        response = self.client.get('/api/courses/ai-buildcamp/cohorts', **self._auth())
        [cohort] = response.json()['cohorts']
        self.assertEqual(cohort['external_key'], '4')
        self.assertEqual(cohort['start_date'], self.start.isoformat())
        self.assertEqual(cohort['enrollment_count'], 1)
        self.assertEqual(cohort['event_series'], {
            'id': self.duplicate.pk,
            'slug': 'buildcamp-office-hours-cohort-4-dup',
            'name': '[DELETE ME in Studio] duplicate',
            'event_count': 0,
            'published_event_count': 0,
        })
        self.assertEqual(
            [w['code'] for w in cohort['warnings']], ['empty_event_series'],
        )

    def test_missing_and_invalid_tokens_are_rejected(self):
        # Tokens can only be minted for staff, so an unknown key stands in
        # for every non-staff bearer.
        bogus = {'HTTP_AUTHORIZATION': 'Token not-a-real-key'}
        for headers in ({}, bogus):
            response = self.client.get('/api/courses/ai-buildcamp/cohorts', **headers)
            self.assertEqual(response.status_code, 401)
        response = self.client.patch(
            '/api/courses/ai-buildcamp/cohorts/4',
            data=json.dumps({'event_series': self.series.pk}),
            content_type='application/json', **bogus,
        )
        self.assertEqual(response.status_code, 401)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.duplicate.pk)

    def test_unknown_course_returns_404(self):
        response = self.client.get('/api/courses/nope/cohorts', **self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_course')

    def test_patch_links_series_by_slug_and_clears_warnings(self):
        response = self._patch({'event_series': 'buildcamp-office-hours-cohort-4'})
        body = response.json()
        self.assertEqual(body['event_series']['id'], self.series.pk)
        self.assertEqual(body['event_series']['published_event_count'], 1)
        self.assertEqual(body['warnings'], [])
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.series.pk)

    def test_patch_links_series_by_id_and_updates_dates(self):
        response = self._patch({
            'event_series': self.series.pk,
            'start_date': '2026-09-15',
            'end_date': '2026-11-10',
        }, key='4')
        self.assertEqual(
            (response.json()['start_date'], response.json()['end_date']),
            ('2026-09-15', '2026-11-10'),
        )
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.series.pk)
        self.assertEqual(self.cohort.start_date, datetime.date(2026, 9, 15))
        self.assertEqual(self.cohort.end_date, datetime.date(2026, 11, 10))

    def test_patch_refuses_to_clear_a_dated_cohorts_series(self):
        response = self._patch({'event_series': None})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'event_series_required')
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.duplicate.pk)

    def test_ended_cohort_can_be_unlinked_and_is_only_noted(self):
        Cohort.objects.filter(pk=self.cohort.pk).update(end_date=datetime.date(2020, 1, 31), start_date=datetime.date(2020, 1, 1))
        response = self._patch({'event_series': None})
        self.assertIsNone(response.json()['event_series'])
        self.assertEqual(
            [(w['code'], w['level']) for w in response.json()['warnings']],
            [('ended_without_event_series', 'info')],
        )

    def test_list_flags_an_unlinked_dated_cohort_as_an_error(self):
        Cohort.objects.filter(pk=self.cohort.pk).update(event_series=None)
        response = self.client.get('/api/courses/ai-buildcamp/cohorts', **self._auth())
        [cohort] = response.json()['cohorts']
        self.assertIsNone(cohort['event_series'])
        self.assertEqual(
            [(w['code'], w['level']) for w in cohort['warnings']],
            [('no_event_series', 'error')],
        )

    def test_patch_unknown_cohort_key_returns_404(self):
        response = self._patch({'event_series': self.series.pk}, key='9')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_cohort')

    def test_patch_unknown_series_returns_400_and_leaves_cohort(self):
        response = self._patch({'event_series': 'no-such-series'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'unknown_event_series')
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.event_series_id, self.duplicate.pk)

    def test_patch_rejects_unknown_fields_and_bad_dates(self):
        self.assertEqual(self._patch({'name': 'x'}).status_code, 422)
        self.assertEqual(self._patch({'start_date': '14/09/2026'}).status_code, 422)
        # Model validation: a dated cohort cannot lose its start date.
        response = self._patch({'start_date': None})
        self.assertEqual(response.status_code, 422)
        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.start_date, self.start)
