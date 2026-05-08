"""Tests for 'View on site' links in Studio list and edit pages.

Verifies that every content type in Studio has a link to its public page,
both in list rows and in edit/review page headers. Links must open in a new
tab (target="_blank").
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event

User = get_user_model()


class ViewOnSiteTestMixin:
    """Shared setUp: staff user logged in."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')


class ArticleViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for articles."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='My Article', slug='my-article',
            date=timezone.now().date(),
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/articles/')
        self.assertContains(response, 'href="/blog/my-article"')
        self.assertContains(response, 'target="_blank"')

    def test_list_view_on_site_has_correct_text(self):
        response = self.client.get('/studio/articles/')
        self.assertContains(response, 'View on site')

    def test_edit_has_view_on_site_link(self):
        response = self.client.get(f'/studio/articles/{self.article.pk}/edit')
        self.assertContains(response, 'href="/blog/my-article"')
        self.assertContains(response, 'target="_blank"')

    def test_edit_view_on_site_has_correct_text(self):
        response = self.client.get(f'/studio/articles/{self.article.pk}/edit')
        self.assertContains(response, 'View on site')


class CourseViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for courses."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='My Course', slug='my-course',
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'href="/courses/my-course"')
        self.assertContains(response, 'target="_blank"')

    def test_edit_has_view_on_site_link(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'href="/courses/my-course"')
        self.assertContains(response, 'target="_blank"')


class EventViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for events."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='My Event', slug='my-event',
            start_datetime=timezone.now(), status='upcoming',
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/events/')
        self.assertContains(response, 'href="/events/my-event"')
        self.assertContains(response, 'target="_blank"')

    def test_edit_has_view_on_site_link(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'href="/events/my-event"')
        self.assertContains(response, 'target="_blank"')


class RecordingLinkedWorkshopViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for recordings with a linked Workshop (issue #426).

    The Studio recordings list/edit pages link to the canonical Workshop
    surface (``/workshops/<slug>``) rather than the announcement-only event
    detail page, since the event detail page no longer plays recordings.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = Event.objects.create(
            title='My Recording', slug='my-recording',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        cls.workshop = Workshop.objects.create(
            slug='my-workshop',
            title='My Workshop',
            date=datetime.date(2025, 4, 1),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            event=cls.recording,
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/recordings/')
        self.assertContains(response, 'href="/workshops/my-workshop"')
        self.assertContains(response, 'target="_blank"')
        # Must NOT link out to the announcement-only event detail page.
        self.assertNotContains(response, 'href="/events/my-recording"')

    def test_edit_has_view_on_site_link(self):
        response = self.client.get(
            f'/studio/recordings/{self.recording.pk}/edit',
        )
        self.assertContains(response, 'href="/workshops/my-workshop"')
        self.assertContains(response, 'target="_blank"')
        self.assertNotContains(response, 'href="/events/my-recording"')


class RecordingNoWorkshopNoViewOnSiteLinkTest(ViewOnSiteTestMixin, TestCase):
    """A recording with no linked Workshop has no public view-on-site link.

    Issue #426: the event detail page is announcement-only, so there is no
    canonical public surface to point at when no Workshop has been
    promoted from the event yet.
    """

    @classmethod
    def setUpTestData(cls):
        cls.recording = Event.objects.create(
            title='Orphan Recording', slug='orphan-recording',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=orphan',
        )

    def test_list_has_no_view_on_site_link(self):
        response = self.client.get('/studio/recordings/')
        self.assertEqual(response.status_code, 200)
        # The recording row still renders, but with no public link.
        self.assertContains(response, 'Orphan Recording')
        self.assertNotContains(response, 'href="/events/orphan-recording"')
        self.assertNotContains(response, 'href="/workshops/')

    def test_edit_has_no_view_on_site_link(self):
        response = self.client.get(
            f'/studio/recordings/{self.recording.pk}/edit',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Orphan Recording')
        self.assertNotContains(response, 'href="/events/orphan-recording"')
        self.assertNotContains(response, 'href="/workshops/')


class ProjectViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for projects."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='My Project', slug='my-project',
            date=timezone.now().date(),
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/projects/')
        self.assertContains(response, 'href="/projects/my-project"')
        self.assertContains(response, 'target="_blank"')

    def test_review_has_view_on_site_link(self):
        response = self.client.get(
            f'/studio/projects/{self.project.pk}/review',
        )
        self.assertContains(response, 'href="/projects/my-project"')
        self.assertContains(response, 'target="_blank"')


class DownloadViewOnSiteTest(ViewOnSiteTestMixin, TestCase):
    """View on site links for downloads."""

    @classmethod
    def setUpTestData(cls):
        cls.download = Download.objects.create(
            title='My Download', slug='my-download',
            file_url='https://example.com/file.pdf',
        )

    def test_list_has_view_on_site_link(self):
        response = self.client.get('/studio/downloads/')
        self.assertContains(response, 'href="/downloads/my-download"')
        self.assertContains(response, 'target="_blank"')

    def test_edit_has_view_on_site_link(self):
        response = self.client.get(
            f'/studio/downloads/{self.download.pk}/edit',
        )
        self.assertContains(response, 'href="/downloads/my-download"')
        self.assertContains(response, 'target="_blank"')
