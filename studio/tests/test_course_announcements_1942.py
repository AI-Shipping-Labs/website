"""Studio course announcement authoring (issue #1942)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Enrollment
from content.models.course_announcement import CourseAnnouncement
from content.services.course_announcements import publish_confirm_message

User = get_user_model()


class StudioCourseAnnouncementTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='studio-admin-1942@test.com', password='pw',
            is_staff=True, is_superuser=True,
        )
        cls.member = User.objects.create_user(
            email='studio-member-1942@test.com', password='pw',
        )
        cls.course = Course.objects.create(
            title='Studio tools', slug='studio-tools-1942', status='published',
        )
        Enrollment.objects.create(user=cls.member, course=cls.course)
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today, end_date=today + datetime.timedelta(days=60),
        )
        cls.old_cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 3', external_key='3',
            start_date=today - datetime.timedelta(days=200),
            end_date=today - datetime.timedelta(days=140),
        )
        CohortEnrollment.objects.create(user=cls.member, cohort=cls.cohort)
        old_member = User.objects.create_user(
            email='studio-old-1942@test.com', password='pw',
        )
        Enrollment.objects.create(user=old_member, course=cls.course)
        CohortEnrollment.objects.create(user=old_member, cohort=cls.old_cohort)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_workflows_panel_links_to_list(self):
        response = self.client.get(
            reverse('studio_course_edit', kwargs={'course_id': self.course.pk}),
        )
        self.assertContains(response, 'Manage announcements')
        self.assertContains(
            response,
            reverse(
                'studio_course_announcement_list',
                kwargs={'course_id': self.course.pk},
            ),
        )

    def test_list_create_edit_and_confirm_copy(self):
        list_url = reverse(
            'studio_course_announcement_list',
            kwargs={'course_id': self.course.pk},
        )
        response = self.client.get(list_url)
        self.assertContains(response, 'New announcement')
        # Two learners are enrolled in the course, one per cohort: the
        # confirm counts only the target cohort's.
        confirm = publish_confirm_message(self.cohort)
        self.assertIn('1 enrolled learners of Cohort 4', confirm)

        create_url = reverse(
            'studio_course_announcement_create',
            kwargs={'course_id': self.course.pk},
        )
        get_create = self.client.get(create_url)
        self.assertContains(get_create, publish_confirm_message(None))
        self.assertContains(get_create, 'data-confirm=')
        self.assertContains(get_create, 'formaction=')
        self.assertContains(
            get_create,
            f'<option value="{self.cohort.pk}" selected>Cohort 4</option>',
            html=True,
        )

        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                post = self.client.post(create_url, {
                    'title': 'Project deadline extended',
                    'body': 'Submit by Friday.',
                    'cohort_id': str(self.cohort.pk),
                })
            enqueue.assert_not_called()
        self.assertRedirects(post, list_url)
        row = CourseAnnouncement.objects.get(course=self.course)
        self.assertEqual(row.status, CourseAnnouncement.STATUS_DRAFT)
        self.assertEqual(row.author, self.staff)
        self.assertEqual(row.cohort, self.cohort)

        list_after = self.client.get(list_url)
        self.assertContains(list_after, 'Project deadline extended')
        self.assertContains(list_after, 'Draft')
        self.assertContains(list_after, 'Not sent')
        self.assertContains(list_after, confirm)

        publish_url = reverse(
            'studio_course_announcement_publish',
            kwargs={
                'course_id': self.course.pk,
                'announcement_id': row.pk,
            },
        )
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                published = self.client.post(publish_url, {
                    'title': row.title, 'body': row.body,
                })
            enqueue.assert_called_once_with(row.pk)
        self.assertRedirects(published, list_url)
        row.refresh_from_db()
        self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)
        self.assertIsNotNone(row.published_at)

        edit_url = reverse(
            'studio_course_announcement_edit',
            kwargs={
                'course_id': self.course.pk,
                'announcement_id': row.pk,
            },
        )
        edit_get = self.client.get(edit_url)
        self.assertNotContains(edit_get, 'data-confirm=')
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            # The cohort select is disabled once published; a forged value
            # must not move the announcement to another cohort.
            saved = self.client.post(edit_url, {
                'title': 'Updated title',
                'body': 'Updated body',
                'cohort_id': str(self.old_cohort.pk),
            })
            enqueue.assert_not_called()
        self.assertRedirects(saved, list_url)
        row.refresh_from_db()
        self.assertEqual(row.title, 'Updated title')
        self.assertEqual(row.body, 'Updated body')
        self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)
        self.assertEqual(row.cohort, self.cohort)

    def test_create_without_cohort_shows_error(self):
        create_url = reverse(
            'studio_course_announcement_create',
            kwargs={'course_id': self.course.pk},
        )
        response = self.client.post(create_url, {
            'title': 'No cohort', 'body': 'x', 'cohort_id': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'Choose the cohort this announcement is for.',
        )
        self.assertFalse(CourseAnnouncement.objects.exists())

    def test_edit_form_publish_persists_changed_title_and_body(self):
        row = CourseAnnouncement.objects.create(
            course=self.course,
            cohort=self.cohort,
            title='Old title',
            body='Old body',
            author=self.staff,
            status=CourseAnnouncement.STATUS_DRAFT,
        )
        publish_url = reverse(
            'studio_course_announcement_publish',
            kwargs={
                'course_id': self.course.pk,
                'announcement_id': row.pk,
            },
        )
        list_url = reverse(
            'studio_course_announcement_list',
            kwargs={'course_id': self.course.pk},
        )
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                published = self.client.post(publish_url, {
                    'title': 'Changed on publish',
                    'body': 'New body from form',
                })
            enqueue.assert_called_once_with(row.pk)
        self.assertRedirects(published, list_url)
        row.refresh_from_db()
        self.assertEqual(row.title, 'Changed on publish')
        self.assertEqual(row.body, 'New body from form')
        self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)

    def test_create_as_published_confirms_and_queues(self):
        create_url = reverse(
            'studio_course_announcement_create',
            kwargs={'course_id': self.course.pk},
        )
        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    create_url + '?publish=1',
                    {
                        'title': 'Live now',
                        'body': 'Hello class.',
                        'cohort_id': str(self.cohort.pk),
                        'publish': '1',
                    },
                )
            enqueue.assert_called_once_with(
                CourseAnnouncement.objects.get(title='Live now').pk,
            )
        self.assertRedirects(
            response,
            reverse(
                'studio_course_announcement_list',
                kwargs={'course_id': self.course.pk},
            ),
        )
        row = CourseAnnouncement.objects.get(title='Live now')
        self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)

    def test_non_staff_forbidden(self):
        self.client.force_login(self.member)
        response = self.client.get(
            reverse(
                'studio_course_announcement_list',
                kwargs={'course_id': self.course.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_is_forensic_only(self):
        from django.contrib.admin.sites import site

        from content.admin.course_announcement import CourseAnnouncementAdmin

        self.assertIn(CourseAnnouncement, site._registry)
        model_admin = CourseAnnouncementAdmin(CourseAnnouncement, site)
        request = type('R', (), {'user': self.staff})()
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
