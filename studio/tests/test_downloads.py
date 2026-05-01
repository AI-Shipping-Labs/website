"""Tests for studio download management views."""

from django.test import TestCase

from content.models import Download
from tests.fixtures import StaffUserMixin


class StudioDownloadListTest(StaffUserMixin, TestCase):
    """Test download list view."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_returns_200(self):
        response = self.client.get('/studio/downloads/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/downloads/')
        self.assertTemplateUsed(response, 'studio/downloads/list.html')

    def test_list_shows_downloads(self):
        Download.objects.create(
            title='Test Download', slug='test-dl',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get('/studio/downloads/')
        self.assertContains(response, 'Test Download')

    def test_list_search(self):
        Download.objects.create(
            title='Python Guide', slug='python',
            file_url='https://example.com/python.pdf',
        )
        Download.objects.create(
            title='Java Guide', slug='java',
            file_url='https://example.com/java.pdf',
        )
        response = self.client.get('/studio/downloads/?q=Python')
        self.assertContains(response, 'Python Guide')
        self.assertNotContains(response, 'Java Guide')


class StudioDownloadCreateRemovedTest(StaffUserMixin, TestCase):
    """Test that download create URL has been removed."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_url_returns_404(self):
        # Replaces playwright_tests/test_downloadable_resources.py::TestScenario11StaffCreatesDownloadViaStudio::test_download_create_url_removed_and_download_visible_publicly
        # (the "/studio/downloads/new is removed" half — the public-listing
        # half lives in content.tests.test_downloads.DownloadsPubliclyVisibleAfterCreateTest)
        response = self.client.get('/studio/downloads/new')
        self.assertEqual(response.status_code, 404)


class StudioDownloadEditTest(StaffUserMixin, TestCase):
    """Test download editing."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.download = Download.objects.create(
            title='Edit DL', slug='edit-dl',
            file_url='https://example.com/file.pdf',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/downloads/{self.download.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_download_post(self):
        self.client.post(f'/studio/downloads/{self.download.pk}/edit', {
            'title': 'Updated DL',
            'slug': 'edit-dl',
            'file_url': 'https://example.com/updated.pdf',
            'file_type': 'zip',
            'file_size_bytes': '2048',
            'published': 'on',
            'required_level': '10',
            'tags': 'pdf, , resource ,, template ',
        })
        self.download.refresh_from_db()
        self.assertEqual(self.download.title, 'Updated DL')
        self.assertEqual(self.download.file_type, 'zip')
        self.assertEqual(self.download.file_size_bytes, 2048)
        self.assertEqual(self.download.required_level, 10)
        self.assertEqual(self.download.tags, ['pdf', 'resource', 'template'])

    def test_edit_nonexistent_returns_404(self):
        response = self.client.get('/studio/downloads/99999/edit')
        self.assertEqual(response.status_code, 404)
