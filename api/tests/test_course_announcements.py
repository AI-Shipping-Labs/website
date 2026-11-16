"""Staff API for course announcements (issue #1942)."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Token
from content.models import Cohort, Course, Enrollment
from content.models.course_announcement import CourseAnnouncement

User = get_user_model()


class CourseAnnouncementApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='api-admin-1942@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='api-member-1942@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='s')
        cls.course = Course.objects.create(
            title='API tools', slug='api-tools-1942', status='draft',
        )
        Enrollment.objects.create(user=cls.member, course=cls.course)
        start = datetime.date(2026, 9, 21)
        cls.cohort3 = Cohort.objects.create(
            course=cls.course, name='Cohort 3', external_key='3',
            start_date=start - datetime.timedelta(days=160),
            end_date=start - datetime.timedelta(days=100),
        )
        cls.cohort4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=start, end_date=start + datetime.timedelta(days=60),
        )
        other = Course.objects.create(
            title='Other', slug='other-api-1942', status='draft',
        )
        cls.foreign_cohort = Cohort.objects.create(
            course=other, name='Foreign', external_key='9',
            start_date=start, end_date=start + datetime.timedelta(days=60),
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _collection(self):
        return reverse(
            'api_course_announcements_collection',
            kwargs={'slug': self.course.slug},
        )

    def _detail(self, announcement_id):
        return reverse(
            'api_course_announcement_detail',
            kwargs={
                'slug': self.course.slug,
                'announcement_id': announcement_id,
            },
        )

    def _post(self, payload):
        return self.client.post(
            self._collection(),
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(),
        )

    def _patch(self, announcement_id, payload):
        return self.client.patch(
            self._detail(announcement_id),
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(),
        )

    def test_unpublished_course_is_addressable(self):
        response = self.client.get(
            reverse(
                'api_course_announcements_collection',
                kwargs={'slug': self.course.slug},
            ),
            **self._auth(),
        )
        self.assertEqual(response.json()['announcements'], [])

    def test_create_list_get_patch_and_email_once(self):
        collection = reverse(
            'api_course_announcements_collection',
            kwargs={'slug': self.course.slug},
        )
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                created = self.client.post(
                    collection,
                    data=json.dumps({
                        'title': 'Week 2 office hours moved',
                        'body': '**Tomorrow** we meet at 18:00 CET.',
                        'cohort': '4',
                        'status': 'published',
                    }),
                    content_type='application/json',
                    **self._auth(),
                )
            payload = created.json()
            enqueue.assert_called_once_with(payload['id'])
        self.assertEqual(created.status_code, 201)
        self.assertEqual(payload['status'], 'published')
        self.assertEqual(payload['author_email'], self.staff.email)
        self.assertEqual(payload['cohort'], {
            'id': self.cohort4.pk, 'external_key': '4', 'name': 'Cohort 4',
        })
        self.assertIsNotNone(payload['published_at'])
        announcement_id = payload['id']

        listed = self.client.get(collection, **self._auth())
        self.assertEqual(len(listed.json()['announcements']), 1)

        detail = reverse(
            'api_course_announcement_detail',
            kwargs={
                'slug': self.course.slug,
                'announcement_id': announcement_id,
            },
        )
        fetched = self.client.get(detail, **self._auth())
        self.assertEqual(fetched.json()['title'], 'Week 2 office hours moved')

        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            patched = self.client.patch(
                detail,
                data=json.dumps({
                    'title': 'Updated title',
                    'body': 'Updated body',
                }),
                content_type='application/json',
                **self._auth(),
            )
            enqueue.assert_not_called()
        self.assertEqual(patched.json()['title'], 'Updated title')
        self.assertEqual(patched.json()['status'], 'published')

        deleted = self.client.delete(detail, **self._auth())
        self.assertEqual(deleted.status_code, 405)
        self.assertEqual(
            deleted.json()['code'],
            'course_announcement_delete_not_available',
        )
        self.assertTrue(
            CourseAnnouncement.objects.filter(pk=announcement_id).exists(),
        )

    def test_patch_first_publish_queues_email_once(self):
        collection = reverse(
            'api_course_announcements_collection',
            kwargs={'slug': self.course.slug},
        )
        created = self.client.post(
            collection,
            data=json.dumps({
                'title': 'Draft note',
                'body': 'Hold for later.',
                'cohort_id': self.cohort4.pk,
            }),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()['status'], 'draft')
        detail = reverse(
            'api_course_announcement_detail',
            kwargs={
                'slug': self.course.slug,
                'announcement_id': created.json()['id'],
            },
        )
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                published = self.client.patch(
                    detail,
                    data=json.dumps({'status': 'published'}),
                    content_type='application/json',
                    **self._auth(),
                )
            enqueue.assert_called_once_with(created.json()['id'])
        self.assertEqual(published.json()['status'], 'published')
        self.assertIsNotNone(published.json()['published_at'])

    def test_create_requires_a_cohort_of_this_course(self):
        missing = self._post({'title': 'No cohort', 'body': 'x'})
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(missing.json()['details']['field'], 'cohort')
        unknown = self._post({'title': 'Bad', 'body': 'x', 'cohort': '7'})
        self.assertEqual(unknown.status_code, 422)
        foreign = self._post({
            'title': 'Bad', 'body': 'x', 'cohort_id': self.foreign_cohort.pk,
        })
        self.assertEqual(foreign.status_code, 422)
        self.assertEqual(foreign.json()['details']['field'], 'cohort_id')
        both = self._post({
            'title': 'Bad', 'body': 'x',
            'cohort': '4', 'cohort_id': self.cohort4.pk,
        })
        self.assertEqual(both.status_code, 422)
        self.assertFalse(CourseAnnouncement.objects.exists())

    def test_draft_cohort_can_change_published_cohort_is_locked(self):
        created = self._post({'title': 'Note', 'body': 'x', 'cohort': '3'})
        announcement_id = created.json()['id']
        moved = self._patch(announcement_id, {'cohort': '4'})
        self.assertEqual(moved.json()['cohort']['external_key'], '4')
        with self.captureOnCommitCallbacks(execute=False):
            self._patch(announcement_id, {'status': 'published'})
        locked = self._patch(announcement_id, {'cohort': '3'})
        self.assertEqual(locked.status_code, 422)
        self.assertEqual(
            CourseAnnouncement.objects.get(pk=announcement_id).cohort,
            self.cohort4,
        )

    def test_publish_without_cohort_is_rejected(self):
        legacy = CourseAnnouncement.objects.create(
            course=self.course, title='Legacy', body='x',
        )
        response = self._patch(legacy.pk, {'status': 'published'})
        self.assertEqual(response.status_code, 422)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, CourseAnnouncement.STATUS_DRAFT)

    def test_list_filters_by_cohort_key(self):
        self._post({'title': 'For three', 'body': 'x', 'cohort': '3'})
        self._post({'title': 'For four', 'body': 'x', 'cohort': '4'})
        response = self.client.get(
            self._collection(), {'cohort': '4'}, **self._auth(),
        )
        self.assertEqual(
            [row['title'] for row in response.json()['announcements']],
            ['For four'],
        )
        unknown = self.client.get(
            self._collection(), {'cohort': 'nope'}, **self._auth(),
        )
        self.assertEqual(unknown.status_code, 422)

    def test_anonymous_and_non_staff_cannot_write(self):
        collection = reverse(
            'api_course_announcements_collection',
            kwargs={'slug': self.course.slug},
        )
        anon = self.client.get(collection)
        self.assertEqual(anon.status_code, 401)
        member = self.client.post(
            collection,
            data=json.dumps({'title': 'Nope', 'body': 'x'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Token not-a-staff-token',
        )
        self.assertEqual(member.status_code, 401)
        self.assertFalse(CourseAnnouncement.objects.exists())

    def test_unknown_course_or_id_is_404(self):
        missing = self.client.get(
            reverse(
                'api_course_announcements_collection',
                kwargs={'slug': 'no-such-course'},
            ),
            **self._auth(),
        )
        self.assertEqual(missing.status_code, 404)
        detail = self.client.get(
            reverse(
                'api_course_announcement_detail',
                kwargs={
                    'slug': self.course.slug,
                    'announcement_id': 999999,
                },
            ),
            **self._auth(),
        )
        self.assertEqual(detail.status_code, 404)
