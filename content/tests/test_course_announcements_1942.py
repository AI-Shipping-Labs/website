"""Course announcements member tab, model, and email-once (issue #1942)."""

import datetime
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from content.models import Course, Enrollment
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import (
    EMAIL_TYPE,
    save_announcement,
    send_course_announcement_email,
    show_announcements_tab,
)
from email_app.hooks import preference_resolver
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)

User = get_user_model()


class CourseAnnouncementModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Dev Tools', slug='ai-dev-tools-1942', status='published',
        )
        cls.author = User.objects.create_user(
            email='author-1942@test.com', password='pw', is_staff=True,
        )

    def test_defaults_to_draft_without_publish_or_email_timestamps(self):
        row = CourseAnnouncement.objects.create(
            course=self.course, title='Office hours', body='See you there.',
            author=self.author,
        )
        self.assertEqual(row.status, CourseAnnouncement.STATUS_DRAFT)
        self.assertIsNone(row.published_at)
        self.assertIsNone(row.email_queued_at)
        self.assertEqual(row.course, self.course)

    def test_author_survives_user_delete(self):
        row = CourseAnnouncement.objects.create(
            course=self.course, title='Keep row', body='body',
            author=self.author,
        )
        self.author.delete()
        row.refresh_from_db()
        self.assertIsNone(row.author)


class CourseAnnouncementMemberAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Open tools', slug='open-tools-1942',
            status='published', required_level=0,
        )
        cls.learner = User.objects.create_user(
            email='premium-1942@test.com', password='pw',
            email_verified=True, first_name='Pat',
        )
        cls.outsider = User.objects.create_user(
            email='free-1942@test.com', password='pw',
            email_verified=True,
        )
        cls.staff = User.objects.create_user(
            email='admin-1942@test.com', password='pw',
            is_staff=True, email_verified=True,
        )
        Enrollment.objects.create(user=cls.learner, course=cls.course)
        cls.published = CourseAnnouncement.objects.create(
            course=cls.course,
            title='Week 2 office hours moved',
            body='**Tomorrow** we meet at 18:00 CET.',
            author=cls.staff,
            status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )
        cls.draft = CourseAnnouncement.objects.create(
            course=cls.course,
            title='Secret draft',
            body='Not ready.',
            author=cls.staff,
            status=CourseAnnouncement.STATUS_DRAFT,
        )

    def test_tab_visible_for_enrolled_and_staff_only(self):
        self.assertTrue(show_announcements_tab(self.learner, self.course))
        self.assertTrue(show_announcements_tab(self.staff, self.course))
        self.assertFalse(show_announcements_tab(self.outsider, self.course))

    def test_anonymous_redirects_to_login(self):
        url = reverse('course_announcements', kwargs={'slug': self.course.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_unenrolled_signed_in_gets_404(self):
        self.client.force_login(self.outsider)
        url = reverse('course_announcements', kwargs={'slug': self.course.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'Enrolled only', status_code=404)
        self.assertNotContains(response, self.published.title, status_code=404)

    def test_unenrolled_home_omits_tab(self):
        self.client.force_login(self.outsider)
        response = self.client.get(
            reverse('course_home', kwargs={'slug': self.course.slug}),
        )
        self.assertNotContains(response, 'course-tab-announcements')
        self.assertContains(response, 'Syllabus')

    def test_enrolled_learner_sees_published_markdown_only(self):
        self.client.force_login(self.learner)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        self.assertContains(response, 'Week 2 office hours moved')
        self.assertContains(response, '<strong>Tomorrow</strong>')
        self.assertNotContains(response, '**Tomorrow**')
        self.assertNotContains(response, 'Secret draft')
        self.assertContains(response, 'course-tab-announcements')
        self.assertContains(response, 'Syllabus')
        self.assertContains(response, 'Pat')

    def test_staff_preview_without_enrollment(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        self.assertContains(response, 'Week 2 office hours moved')
        self.assertNotContains(response, 'Secret draft')

    def test_empty_state_copy(self):
        self.published.delete()
        self.draft.delete()
        self.client.force_login(self.learner)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        self.assertContains(response, 'No announcements yet')
        self.assertContains(
            response,
            'When the instructor posts an update, it will show up here.',
        )
        self.assertContains(response, 'data-testid="course-announcements-empty"')

    def test_staff_empty_state_links_to_studio(self):
        self.published.delete()
        self.draft.delete()
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        self.assertContains(response, 'Write an announcement')
        self.assertContains(
            response,
            reverse(
                'studio_course_announcement_list',
                kwargs={'course_id': self.course.pk},
            ),
        )

    def test_newest_published_first(self):
        older = timezone.now() - datetime.timedelta(days=1)
        CourseAnnouncement.objects.create(
            course=self.course, title='Older note', body='old',
            author=self.staff, status=CourseAnnouncement.STATUS_PUBLISHED,
            published_at=older,
        )
        self.published.title = 'Newer note'
        self.published.save(update_fields=['title'])
        self.client.force_login(self.learner)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        html = response.content.decode()
        self.assertLess(html.index('Newer note'), html.index('Older note'))

    def test_unpublished_course_bounces_to_landing_404(self):
        self.course.status = 'draft'
        self.course.save(update_fields=['status'])
        self.client.force_login(self.learner)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug}),
        )
        self.assertEqual(response.status_code, 404)

    def test_keeps_cohort_query_on_tab_links(self):
        from content.models import Cohort, CohortEnrollment

        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4', external_key='4',
            start_date=today, end_date=today + datetime.timedelta(days=30),
        )
        CohortEnrollment.objects.create(user=self.learner, cohort=cohort)
        self.client.force_login(self.learner)
        response = self.client.get(
            reverse('course_announcements', kwargs={'slug': self.course.slug})
            + '?cohort=4',
        )
        self.assertContains(
            response,
            f"/courses/{self.course.slug}/home?cohort=4",
        )
        self.assertContains(
            response,
            f"/courses/{self.course.slug}/home/syllabus?cohort=4",
        )


@override_settings(SES_ENABLED=False)
class CourseAnnouncementEmailOnceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Email course', slug='email-course-1942', status='published',
        )
        cls.staff = User.objects.create_user(
            email='staff-email-1942@test.com', password='pw', is_staff=True,
        )
        cls.learner = User.objects.create_user(
            email='learner-email-1942@test.com', password='pw',
            is_active=True, first_name='Lee',
        )
        cls.unsubscribed = User.objects.create_user(
            email='unsub-email-1942@test.com', password='pw',
            is_active=True,
        )
        cls.unsubscribed.unsubscribed = True
        cls.unsubscribed.save(update_fields=['unsubscribed'])
        cls.inactive = User.objects.create_user(
            email='inactive-email-1942@test.com', password='pw',
            is_active=False,
        )
        cls.unenrolled = User.objects.create_user(
            email='gone-email-1942@test.com', password='pw',
            is_active=True,
        )
        Enrollment.objects.create(user=cls.learner, course=cls.course)
        Enrollment.objects.create(user=cls.unsubscribed, course=cls.course)
        Enrollment.objects.create(user=cls.inactive, course=cls.course)
        Enrollment.objects.create(
            user=cls.unenrolled, course=cls.course,
            unenrolled_at=timezone.now(),
        )

    def _publish(self, **kwargs):
        row = CourseAnnouncement(
            course=self.course,
            title=kwargs.get('title', 'Project deadline extended'),
            body=kwargs.get('body', 'Submit by Friday.'),
            author=self.staff,
        )
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            queued = save_announcement(
                row,
                title=row.title,
                body=row.body,
                status=CourseAnnouncement.STATUS_PUBLISHED,
                author=self.staff,
            )
        self.assertTrue(queued)
        self.assertEqual(len(callbacks), 1)
        return row, callbacks

    def test_first_publish_enqueues_after_commit_not_in_request(self):
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            row, callbacks = self._publish()
            enqueue.assert_not_called()
            callbacks[0]()
            enqueue.assert_called_once_with(row.pk)

    def test_worker_emails_active_enrolled_once_including_unsubscribed(self):
        row, _callbacks = self._publish()
        result = send_course_announcement_email(row.pk)
        self.assertEqual(result['status'], 'queued')
        row.refresh_from_db()
        self.assertIsNotNone(row.email_queued_at)
        keys = set(
            EmailDelivery.objects.filter(purpose=EMAIL_TYPE)
            .values_list('idempotency_key', flat=True)
        )
        self.assertIn(f'course-announcement:{row.pk}:{self.learner.pk}', keys)
        self.assertIn(
            f'course-announcement:{row.pk}:{self.unsubscribed.pk}', keys,
        )
        self.assertNotIn(
            f'course-announcement:{row.pk}:{self.inactive.pk}', keys,
        )
        self.assertNotIn(
            f'course-announcement:{row.pk}:{self.unenrolled.pk}', keys,
        )
        self.assertNotIn(
            f'course-announcement:{row.pk}:{self.staff.pk}', keys,
        )
        self.assertEqual(result['sent'], 2)

        second = send_course_announcement_email(row.pk)
        self.assertEqual(second['reason'], 'already_queued')
        self.assertEqual(
            EmailDelivery.objects.filter(purpose=EMAIL_TYPE).count(), 2,
        )

    def test_edit_unpublish_republish_do_not_requeue(self):
        row, callbacks = self._publish()
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            save_announcement(
                row, title='Updated title', body='Updated body',
                status=CourseAnnouncement.STATUS_PUBLISHED,
            )
            enqueue.assert_not_called()
            save_announcement(
                row, title='Updated title', body='Updated body',
                status=CourseAnnouncement.STATUS_DRAFT,
            )
            save_announcement(
                row, title='Updated title', body='Updated body',
                status=CourseAnnouncement.STATUS_PUBLISHED,
            )
            enqueue.assert_not_called()
        callbacks[0]()
        send_course_announcement_email(row.pk)
        count = EmailDelivery.objects.filter(purpose=EMAIL_TYPE).count()
        send_course_announcement_email(row.pk)
        self.assertEqual(
            EmailDelivery.objects.filter(purpose=EMAIL_TYPE).count(), count,
        )

    def test_unenroll_before_job_skips_recipient(self):
        row, _callbacks = self._publish()
        enrollment = Enrollment.objects.get(
            user=self.learner, course=self.course, unenrolled_at__isnull=True,
        )
        enrollment.unenrolled_at = timezone.now()
        enrollment.save(update_fields=['unenrolled_at'])
        send_course_announcement_email(row.pk)
        self.assertFalse(
            EmailDelivery.objects.filter(
                idempotency_key=f'course-announcement:{row.pk}:{self.learner.pk}',
            ).exists(),
        )

    def test_transactional_unsubscribed_does_not_suppress(self):
        self.assertEqual(
            classify_email_type(EMAIL_TYPE), EMAIL_KIND_TRANSACTIONAL,
        )
        self.assertIsNone(
            preference_resolver(
                purpose=EMAIL_TYPE,
                category=None,
                to=self.unsubscribed.email,
                user=self.unsubscribed,
            ),
        )

    def test_durable_context_is_empty(self):
        row, _callbacks = self._publish()
        send_course_announcement_email(row.pk)
        delivery = EmailDelivery.objects.get(
            idempotency_key=f'course-announcement:{row.pk}:{self.learner.pk}',
        )
        self.assertEqual(delivery.context_data, {})
        self.assertEqual(
            delivery.related_object_type, 'content.courseannouncement',
        )
        self.assertEqual(delivery.related_object_id, str(row.pk))

    def test_per_recipient_failure_does_not_abort_rest(self):
        from email_app.package_mail import send_package_mail as real_send

        row, _callbacks = self._publish()

        def _fail_one(user, template_name, context=None, **kwargs):
            if user.pk == self.learner.pk:
                raise RuntimeError('boom')
            return real_send(user, template_name, context, **kwargs)

        with patch(
            'email_app.package_mail.send_package_mail',
            side_effect=_fail_one,
        ):
            result = send_course_announcement_email(row.pk)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['sent'], 1)
        self.assertTrue(
            EmailDelivery.objects.filter(
                idempotency_key=(
                    f'course-announcement:{row.pk}:{self.unsubscribed.pk}'
                ),
            ).exists(),
        )
