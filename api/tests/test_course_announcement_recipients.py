"""Recipient preview + publish guard for course announcements (intake f4645f36).

The course mirrors ai-buildcamp: dated cohorts 3 and 4 plus a self-paced
cohort, all active. A Cohort 4 announcement must resolve to Cohort 4's
active enrollments only; the preview must send nothing; a publish without a
cohort or with a stale previewed count must be refused.
"""

import datetime
import json
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Token
from content.models import Cohort, CohortEnrollment, Course, Enrollment
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import (
    EMAIL_TYPE,
    send_course_announcement_email,
)

User = get_user_model()

ENQUEUE = 'content.services.course_announcements.enqueue_course_announcement_email'
SEND_MAIL = 'email_app.package_mail.send_package_mail'


class AnnouncementRecipientPreviewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='preview-staff@test.com', password='pw', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='preview')
        cls.course = Course.objects.create(
            title='AI Buildcamp', slug='ai-buildcamp-preview', status='published',
        )
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
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', external_key='self-paced',
            mode='self_paced',
        )

        def member(email, cohort, *, active=True, unenrolled=False):
            user = User.objects.create_user(
                email=email, password='pw', is_active=active,
            )
            Enrollment.objects.create(
                user=user, course=cls.course,
                unenrolled_at=timezone.now() if unenrolled else None,
            )
            if cohort is not None:
                CohortEnrollment.objects.create(user=user, cohort=cohort)
            return user

        cls.cohort4_active = [
            member(f'c4-{i}@test.com', cls.cohort4) for i in range(3)
        ]
        # In Cohort 4 but must not be emailed.
        member('c4-unenrolled@test.com', cls.cohort4, unenrolled=True)
        member('c4-inactive@test.com', cls.cohort4, active=False)
        # Other cohorts and course-only enrollments: never Cohort 4 recipients.
        member('c3-a@test.com', cls.cohort3)
        member('c3-b@test.com', cls.cohort3)
        member('sp-a@test.com', cls.self_paced)
        member('no-cohort@test.com', None)

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def _draft(self, cohort):
        return CourseAnnouncement.objects.create(
            course=self.course, cohort=cohort, author=self.staff,
            title='Demo Day moved', body='Wednesday 9 Dec.',
        )

    def _preview(self, announcement, **query):
        return self.client.get(
            reverse('api_course_announcement_recipients', kwargs={
                'slug': self.course.slug, 'announcement_id': announcement.pk,
            }),
            data=query,
            **self._auth(),
        )

    def _patch(self, announcement, payload):
        return self.client.patch(
            reverse('api_course_announcement_detail', kwargs={
                'slug': self.course.slug, 'announcement_id': announcement.pk,
            }),
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(),
        )

    def test_cohort_4_preview_counts_cohort_4_active_enrollments_only(self):
        draft = self._draft(self.cohort4)

        response = self._preview(draft)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['recipient_count'], 3)
        self.assertEqual(data['cohort'], {
            'id': self.cohort4.pk, 'external_key': '4', 'name': 'Cohort 4',
        })
        # Whole course (active users, still enrolled): 3 + 2 + 1 + 1 = 7.
        self.assertEqual(data['course_active_enrollment_count'], 7)
        self.assertEqual(data['course_cohort_count'], 3)
        self.assertTrue(data['will_email'])
        self.assertIsNone(data['blocked_reason'])

    def test_cohort_4_publish_emails_exactly_the_previewed_people(self):
        draft = self._draft(self.cohort4)
        previewed = self._preview(draft).json()['recipient_count']

        with self.captureOnCommitCallbacks(execute=False):
            response = self._patch(draft, {
                'status': 'published', 'expected_recipient_count': previewed,
            })
        self.assertEqual(response.status_code, 200)
        result = send_course_announcement_email(draft.pk)

        self.assertEqual(result['sent'], previewed)
        keys = set(
            EmailDelivery.objects.filter(purpose=EMAIL_TYPE)
            .values_list('idempotency_key', flat=True)
        )
        self.assertEqual(keys, {
            f'course-announcement:{draft.pk}:{user.pk}'
            for user in self.cohort4_active
        })

    def test_preview_sends_nothing_and_changes_nothing(self):
        draft = self._draft(self.cohort4)
        before = CourseAnnouncement.objects.values().get(pk=draft.pk)

        with patch(ENQUEUE) as enqueue, patch(SEND_MAIL) as send_mail:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self._preview(draft)
                self._preview(draft, cohort='3')

        enqueue.assert_not_called()
        send_mail.assert_not_called()
        self.assertEqual(callbacks, [])
        self.assertEqual(CourseAnnouncement.objects.values().get(pk=draft.pk), before)
        self.assertFalse(EmailDelivery.objects.exists())

    def test_preview_cohort_override_does_not_store_it(self):
        draft = self._draft(None)

        data = self._preview(draft, cohort='3').json()

        self.assertEqual(data['recipient_count'], 2)
        self.assertEqual(data['cohort']['name'], 'Cohort 3')
        self.assertIsNone(data['stored_cohort_id'])
        draft.refresh_from_db()
        self.assertIsNone(draft.cohort_id)

    def test_preview_without_cohort_is_blocked_and_counts_zero(self):
        data = self._preview(self._draft(None)).json()

        self.assertEqual(data['recipient_count'], 0)
        self.assertIsNone(data['cohort'])
        self.assertFalse(data['will_email'])
        self.assertEqual(data['blocked_reason'], 'no_cohort')

    def test_preview_of_published_announcement_reports_no_email(self):
        draft = self._draft(self.cohort4)
        CourseAnnouncement.objects.filter(pk=draft.pk).update(
            status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
            email_queued_at=timezone.now(),
        )

        data = self._preview(draft).json()

        self.assertFalse(data['will_email'])
        self.assertEqual(data['blocked_reason'], 'already_emailed')

    def test_preview_unknown_cohort_is_422(self):
        response = self._preview(self._draft(self.cohort4), cohort='99')

        self.assertEqual(response.status_code, 422)

    def test_no_cohort_publish_on_multi_cohort_course_is_refused(self):
        draft = self._draft(None)

        with patch(ENQUEUE) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._patch(draft, {'status': 'published'})

        self.assertEqual(response.status_code, 422)
        self.assertIn('cohort', response.json()['error'].lower())
        enqueue.assert_not_called()
        draft.refresh_from_db()
        self.assertEqual(draft.status, CourseAnnouncement.STATUS_DRAFT)
        self.assertIsNone(draft.email_queued_at)

    def test_stale_expected_count_refuses_publish_and_sends_nothing(self):
        draft = self._draft(self.cohort4)

        with patch(ENQUEUE) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._patch(draft, {
                    'status': 'published', 'expected_recipient_count': 7,
                })

        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body['code'], 'recipient_count_mismatch')
        self.assertEqual(body['details'], {'expected': 7, 'actual': 3})
        enqueue.assert_not_called()
        draft.refresh_from_db()
        self.assertEqual(draft.status, CourseAnnouncement.STATUS_DRAFT)

    def test_matching_expected_count_publishes_and_queues_once(self):
        draft = self._draft(None)

        with patch(ENQUEUE) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self._patch(draft, {
                    'status': 'published',
                    'cohort': '4',
                    'expected_recipient_count': 3,
                })

        self.assertEqual(response.status_code, 200)
        enqueue.assert_called_once_with(draft.pk)
        draft.refresh_from_db()
        self.assertEqual(draft.cohort_id, self.cohort4.pk)

    def test_expected_count_alone_is_nothing_to_update(self):
        draft = self._draft(self.cohort4)

        response = self._patch(draft, {'expected_recipient_count': 3})

        self.assertEqual(response.status_code, 422)

    def test_expected_count_must_be_a_non_negative_integer(self):
        draft = self._draft(self.cohort4)

        for bad in (-1, '3', True, 2.5):
            response = self._patch(draft, {
                'status': 'published', 'expected_recipient_count': bad,
            })
            self.assertEqual(response.status_code, 422, bad)
        draft.refresh_from_db()
        self.assertEqual(draft.status, CourseAnnouncement.STATUS_DRAFT)

    def test_preview_requires_staff_token(self):
        draft = self._draft(self.cohort4)

        response = self.client.get(reverse(
            'api_course_announcement_recipients',
            kwargs={'slug': self.course.slug, 'announcement_id': draft.pk},
        ))

        self.assertIn(response.status_code, (401, 403))
