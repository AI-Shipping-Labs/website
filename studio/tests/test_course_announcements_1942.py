"""Studio course announcement authoring (issue #1942)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import Course, Enrollment
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
        confirm = publish_confirm_message(self.course)
        self.assertIn('1 enrolled learners', confirm)

        create_url = reverse(
            'studio_course_announcement_create',
            kwargs={'course_id': self.course.pk},
        )
        get_create = self.client.get(create_url)
        self.assertContains(get_create, confirm)
        self.assertContains(get_create, 'data-confirm=')
        self.assertContains(get_create, 'formaction=')

        with patch(
            'content.services.course_announcements.enqueue_course_announcement_email',
        ) as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                post = self.client.post(create_url, {
                    'title': 'Project deadline extended',
                    'body': 'Submit by Friday.',
                })
            enqueue.assert_not_called()
        self.assertRedirects(post, list_url)
        row = CourseAnnouncement.objects.get(course=self.course)
        self.assertEqual(row.status, CourseAnnouncement.STATUS_DRAFT)
        self.assertEqual(row.author, self.staff)

        list_after = self.client.get(list_url)
        self.assertContains(list_after, 'Project deadline extended')
        self.assertContains(list_after, 'Draft')
        self.assertContains(list_after, 'Not sent')

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
            saved = self.client.post(edit_url, {
                'title': 'Updated title',
                'body': 'Updated body',
            })
            enqueue.assert_not_called()
        self.assertRedirects(saved, list_url)
        row.refresh_from_db()
        self.assertEqual(row.title, 'Updated title')
        self.assertEqual(row.body, 'Updated body')
        self.assertEqual(row.status, CourseAnnouncement.STATUS_PUBLISHED)

    def test_edit_form_publish_persists_changed_title_and_body(self):
        row = CourseAnnouncement.objects.create(
            course=self.course,
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
