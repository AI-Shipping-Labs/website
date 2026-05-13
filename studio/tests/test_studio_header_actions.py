from datetime import datetime

from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Download, Project, Workshop
from email_app.models import EmailCampaign
from events.models import Event, EventSeries
from tests.fixtures import StaffUserMixin


class StudioHeaderActionsTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def assert_header_action_contains(self, response, *labels):
        body = response.content.decode()
        self.assertContains(response, 'data-testid="studio-header-actions"')
        header_start = body.index('data-testid="studio-header-actions"')
        header_end = body.index('</header>', header_start)
        header = body[header_start:header_end]
        for label in labels:
            self.assertIn(label, header)

    def test_workshop_detail_header_contains_public_and_edit_actions(self):
        workshop = Workshop.objects.create(
            title='Header Workshop',
            slug='header-workshop',
            date=timezone.now().date(),
            status='published',
        )

        response = self.client.get(f'/studio/workshops/{workshop.pk}/')

        self.assert_header_action_contains(response, 'View on site', 'Edit')
        self.assertContains(response, 'data-testid="view-on-site"', count=1)
        self.assertContains(response, 'data-testid="edit-workshop-link"', count=1)

    def test_project_review_header_contains_moderation_actions(self):
        project = Project.objects.create(
            title='Header Project',
            slug='header-project',
            date=timezone.now().date(),
            status='pending_review',
            published=False,
        )

        response = self.client.get(f'/studio/projects/{project.pk}/review')

        self.assert_header_action_contains(
            response, 'View on site', 'Approve', 'Reject',
        )

    def test_form_pages_render_one_public_action_in_header(self):
        article = Article.objects.create(
            title='Header Article',
            slug='header-article',
            date=timezone.now().date(),
        )
        course = Course.objects.create(
            title='Header Course',
            slug='header-course',
            status='published',
        )
        event = Event.objects.create(
            title='Header Event',
            slug='header-event',
            start_datetime=datetime(2026, 6, 1, 10, 0),
            status='upcoming',
        )
        download = Download.objects.create(
            title='Header Download',
            slug='header-download',
            file_url='https://example.com/file.pdf',
        )

        for path in (
            f'/studio/articles/{article.pk}/edit',
            f'/studio/courses/{course.pk}/edit',
            f'/studio/events/{event.pk}/edit',
            f'/studio/downloads/{download.pk}/edit',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assert_header_action_contains(response, 'View on site')
                self.assertContains(response, 'View on site', count=1)
                self.assertNotContains(response, 'data-testid="panel-view-on-site"')
                self.assertNotContains(response, 'data-testid="sticky-view-on-site"')

    def test_recording_with_workshop_header_contains_view_workshop(self):
        recording = Event.objects.create(
            title='Header Recording',
            slug='header-recording',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        Workshop.objects.create(
            title='Recording Workshop',
            slug='recording-workshop',
            date=timezone.now().date(),
            status='published',
            event=recording,
        )

        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')

        self.assert_header_action_contains(response, 'View workshop')
        self.assertContains(response, 'data-testid="view-on-site"', count=1)

    def test_event_series_header_contains_public_and_delete_actions(self):
        series = EventSeries.objects.create(
            name='Header Series',
            slug='header-series',
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )

        response = self.client.get(f'/studio/event-series/{series.pk}/')

        self.assert_header_action_contains(
            response, 'View public series page', 'Save metadata', 'Delete series',
        )
        self.assertContains(response, 'data-testid="event-series-delete-submit"', count=1)

    def test_campaign_draft_header_contains_send_and_edit_actions(self):
        campaign = EmailCampaign.objects.create(
            subject='Header Campaign',
            body='Body',
            status='draft',
        )

        response = self.client.get(f'/studio/campaigns/{campaign.pk}/')

        self.assert_header_action_contains(response, 'Send to 0 recipients', 'Edit')
        self.assertContains(response, 'data-testid="send-campaign-btn"', count=1)
        self.assertContains(response, 'data-testid="edit-campaign-link"', count=1)
