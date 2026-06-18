"""Tests for ``GET /api/users/<email>/activity`` (issue #1054)."""

from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from analytics.models import UserActivity
from crm.models import CRMRecord

User = get_user_model()


class UserActivityApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='activity-api-staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='activity-api')
        cls.member = User.objects.create_user(
            email='activity-api-member@test.com',
            password='pw',
            first_name='Activity',
            last_name='Member',
        )
        cls.non_staff = User.objects.create_user(
            email='activity-api-nonstaff@test.com',
            password='pw',
        )
        cls.non_staff_token = Token(
            key='activity-api-non-staff-token',
            user=cls.non_staff,
            name='legacy-member-token',
        )
        Token.objects.bulk_create([cls.non_staff_token])

        from content.models import Course
        from events.models import Event

        cls.course = Course.objects.create(
            title='API Context Course',
            slug='api-context-course',
            status='published',
        )
        cls.event = Event.objects.create(
            title='API Context Event',
            slug='api-context-event',
            start_datetime=timezone.now() + timezone.timedelta(days=2),
            status='upcoming',
        )
        UserActivity.objects.all().delete()

    def setUp(self):
        UserActivity.objects.all().delete()
        CRMRecord.objects.all().delete()

    def _auth(self, token=None):
        token = token or self.token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _url(self, email=None):
        return f'/api/users/{email or self.member.email}/activity'

    def _add(
        self,
        event_type,
        label,
        *,
        minutes_ago=0,
        target_url='',
        object_type='',
        object_id='',
    ):
        return UserActivity.objects.create(
            user=self.member,
            event_type=event_type,
            label=label,
            target_url=target_url,
            object_type=object_type,
            object_id=object_id,
            occurred_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
        )

    def test_returns_filtered_activity_context_with_crm_summary(self):
        record = CRMRecord.objects.create(
            user=self.member,
            status='active',
            persona='Sam - Technical Professional',
        )
        self._add(
            UserActivity.EVENT_COURSE_ENROLL,
            'Enrolled in API Context Course',
            object_type='course',
            object_id=self.course.slug,
            minutes_ago=2,
        )
        self._add(
            UserActivity.EVENT_EVENT_REGISTER,
            'Registered for API Context Event',
            target_url=f'/studio/events/{self.event.pk}/edit',
            object_type='event',
            object_id=self.event.slug,
            minutes_ago=1,
        )

        response = self.client.get(
            f'{self._url()}?category=events&limit=5',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['user']['email'], self.member.email)
        self.assertEqual(
            body['crm_record'],
            {
                'id': record.pk,
                'status': 'active',
                'persona': 'Sam - Technical Professional',
            },
        )
        self.assertEqual(body['total_count'], 1)
        self.assertEqual(body['limit'], 5)
        self.assertFalse(body['has_more'])
        self.assertEqual(body['category_counts']['all'], 2)
        self.assertEqual(len(body['activities']), 1)
        item = body['activities'][0]
        self.assertEqual(item['event_type'], UserActivity.EVENT_EVENT_REGISTER)
        self.assertEqual(item['category'], 'events')
        self.assertEqual(item['label'], 'Registered for API Context Event')
        self.assertEqual(item['object_type'], 'event')
        self.assertEqual(item['object_id'], self.event.slug)
        self.assertEqual(item['target_url'], self.event.get_absolute_url())
        self.assertFalse(item['is_upgrade_marker'])

    def test_clamps_limit_to_100_and_reports_has_more(self):
        for i in range(105):
            self._add(
                UserActivity.EVENT_RESOURCE_VIEW,
                f'Viewed article {i}',
                minutes_ago=i,
            )

        response = self.client.get(
            f'{self._url()}?limit=999',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['limit'], 100)
        self.assertEqual(body['total_count'], 105)
        self.assertTrue(body['has_more'])
        self.assertEqual(len(body['activities']), 100)

    def test_since_filters_activity(self):
        self._add(UserActivity.EVENT_SIGNUP, 'Old signup', minutes_ago=120)
        self._add(UserActivity.EVENT_EMAIL_CLICK, 'Recent click', minutes_ago=5)
        since = (timezone.now() - timezone.timedelta(minutes=30)).isoformat()

        response = self.client.get(
            f'{self._url()}?since={quote(since)}',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total_count'], 1)
        self.assertEqual(
            response.json()['activities'][0]['event_type'],
            UserActivity.EVENT_EMAIL_CLICK,
        )

    def test_invalid_filters_return_structured_422(self):
        response = self.client.get(
            f'{self._url()}?category=unknown',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'validation_error')
        self.assertEqual(response.json()['details']['field'], 'category')

        response = self.client.get(
            f'{self._url()}?since=not-a-date',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['details']['field'], 'since')

    def test_unknown_user_returns_user_not_found(self):
        response = self.client.get(
            self._url('nobody@test.com'),
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'user_not_found')

    def test_requires_staff_token(self):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {'error': 'Authentication token required'},
        )

        response = self.client.get(
            self._url(),
            **self._auth(self.non_staff_token),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'Invalid token'})

    def test_no_write_endpoint_for_arbitrary_activity(self):
        response = self.client.post(
            self._url(),
            data='{}',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(UserActivity.objects.count(), 0)
