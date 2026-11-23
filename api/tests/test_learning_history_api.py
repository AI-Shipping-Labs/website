"""Complete retained course history, scoped and stable under new writes."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from analytics.models import UserActivity
from content.models import Cohort, CohortEnrollment
from content.models.course import Course, Module, Unit

User = get_user_model()


class LearningHistoryApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email='staff@history.test', is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name='history')
        cls.member = User.objects.create_user(email='student@history.test')
        cls.outsider = User.objects.create_user(email='outsider@history.test')
        cls.course = Course.objects.create(title='Course', slug='history')
        cls.module = Module.objects.create(course=cls.course, title='Bonus', slug='bonus', is_bonus=True)
        cls.child = Module.objects.create(course=cls.course, parent=cls.module, title='Child', slug='child')
        cls.unit = Unit.objects.create(module=cls.child, title='Lesson', slug='lesson')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Four', external_key='4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.member)
        cls.at = timezone.now() - timedelta(days=3)

    def _event(self, user=None, unit=None, at=None):
        if user is None:
            user = self.member
        if unit is None:
            unit = self.unit
        if at is None:
            at = self.at
        return UserActivity.objects.create(
            user=user, event_type='lesson_open', occurred_at=at,
            object_type='unit', object_id=str(unit.pk),
            target_url=f'/courses/history/{unit.module.slug}/{unit.slug}',
        )

    def _get(self, **params):
        return self.client.get('/api/courses/history/learning-activity', params,
                               HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_export_exceeds_100_without_duplicates_and_excludes_new_writes(self):
        expected = set()
        for _ in range(205):
            expected.add(self._event().id)
        first = self._get(limit=100).json()
        self.assertEqual(first['total_count'], 205)
        self.assertTrue(first['has_more'])
        self._event(at=timezone.now())
        rows = first['activities']
        second = self._get(limit=100, cursor=first['next_cursor']).json()
        third = self._get(limit=100, cursor=second['next_cursor']).json()
        rows += second['activities'] + third['activities']
        self.assertEqual(len(rows), 205)
        self.assertEqual({row['id'] for row in rows}, expected)
        self.assertIsNone(third['next_cursor'])
        self.assertEqual(third['total_count'], 205)

    def test_filters_and_inherited_optional_metadata(self):
        event = self._event()
        self._event(user=self.outsider)
        self._event(at=self.at - timedelta(days=10))
        self._event(at=self.at + timedelta(days=10))
        response = self._get(cohort='4', module=self.module.pk, unit=self.unit.pk,
                             email=self.member.email,
                             since=self.at.isoformat(), until=(self.at + timedelta(days=1)).isoformat())
        data = response.json()
        self.assertEqual([row['id'] for row in data['activities']], [event.id])
        self.assertTrue(data['activities'][0]['unit']['optional_module'])
        self.assertTrue(data['activities'][0]['unit']['is_bonus'])
        self.assertEqual(data['measurement'], 'lesson_open; not video playback or completion')
        self.assertIn('retention_days', data)

    def test_cursor_is_bound_to_filters_and_cannot_be_tampered(self):
        self._event()
        self._event()
        cursor = self._get(limit=1).json()['next_cursor']
        self.assertEqual(self._get(cursor=cursor, cohort='4').status_code, 422)
        self.assertEqual(self._get(cursor=cursor + 'bad').status_code, 422)

    def test_module_unit_and_course_filters_exclude_unrelated_openings(self):
        target = self._event()
        sibling = Unit.objects.create(module=self.child, title='Sibling', slug='sibling')
        sibling_event = self._event(unit=sibling)
        core = Module.objects.create(course=self.course, title='Core', slug='core')
        core_unit = Unit.objects.create(module=core, title='Core lesson', slug='core-lesson')
        self._event(unit=core_unit)
        other_course = Course.objects.create(title='Other', slug='other')
        other_module = Module.objects.create(course=other_course, title='Other', slug='other')
        other_unit = Unit.objects.create(module=other_module, title='Other', slug='other')
        foreign = self._event(unit=other_unit)
        foreign.target_url = '/courses/other/other/other'
        foreign.save(update_fields=['target_url'])
        scoped = self._get(module=self.module.pk).json()['activities']
        self.assertEqual({row['id'] for row in scoped}, {target.id, sibling_event.id})
        scoped = self._get(unit=self.unit.pk).json()['activities']
        self.assertEqual([row['id'] for row in scoped], [target.id])
        self.assertEqual(self._get().json()['total_count'], 3)

    def test_individual_bonus_in_core_is_distinct_from_optional_module(self):
        core = Module.objects.create(course=self.course, title='Core', slug='core')
        unit = Unit.objects.create(module=core, title='Extra', slug='extra', is_bonus=True)
        self._event(unit=unit)
        metadata = self._get().json()['activities'][0]['unit']
        self.assertTrue(metadata['is_bonus'])
        self.assertFalse(metadata['optional_module'])

    def test_recorded_course_takes_precedence_when_a_unit_moves(self):
        event = self._event()
        old_blank = self._event()
        old_blank.target_url = ''
        old_blank.save(update_fields=['target_url'])
        other = Course.objects.create(title='New course', slug='new-course')
        module = Module.objects.create(course=other, title='New module', slug='new-module')
        self.unit.module = module
        self.unit.save(update_fields=['module'])
        old_rows = self._get().json()['activities']
        self.assertEqual([row['id'] for row in old_rows], [event.id])
        response = self.client.get('/api/courses/new-course/learning-activity',
                                   HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.assertEqual([row['id'] for row in response.json()['activities']], [old_blank.id])
        self.assertEqual(self._get(unit=self.unit.pk).json()['activities'], [])

    def test_deleted_unit_keeps_persisted_course_opening(self):
        event = self._event()
        self.unit.delete()
        data = self._get().json()
        self.assertEqual(data['activities'][0]['id'], event.id)
        self.assertIsNone(data['activities'][0]['unit'])

    def test_auth_methods_and_invalid_filters(self):
        self.assertEqual(self.client.get('/api/courses/history/learning-activity').status_code, 401)
        self.assertEqual(self._get(cohort='missing').status_code, 404)
        for params in ({'module': '-1'}, {'since': 'invalid'}, {'since': '2026-11-01T00:00:00'}, {'limit': 'no'}):
            self.assertEqual(self._get(**params).status_code, 422)
        response = self.client.post('/api/courses/history/learning-activity',
                                    HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.assertEqual(response.status_code, 405)
