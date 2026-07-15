"""Tests for studio download management views."""

from unittest.mock import patch

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
            file_size_bytes=2048,
            required_level=10,
        )
        response = self.client.get('/studio/downloads/')
        self.assertContains(response, 'Test Download')
        self.assertContains(response, 'Basic and above')
        self.assertContains(response, '2.0 KB')

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
        self.assertContains(response, 'Successful downloads: 0')

    @patch(
        'content.services.download_delivery.verify_download_object_exists',
    )
    def test_edit_download_post(self, object_exists):
        self.client.post(f'/studio/downloads/{self.download.pk}/edit', {
            'title': 'Updated DL',
            'slug': 'edit-dl',
            'file_url': 'https://example.com/updated.pdf',
            'storage_key': 'downloads/edit-dl.zip',
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
        object_exists.assert_called_once_with('downloads/edit-dl.zip')

    @patch(
        'content.services.download_delivery.verify_download_object_exists',
        side_effect=ValueError(
            'Private download object is missing or inaccessible',
        ),
    )
    def test_manual_row_cannot_remain_published_when_object_is_missing(
        self,
        object_exists,
    ):
        self.download.storage_key = 'downloads/edit-dl.pdf'
        self.download.asset_mime_type = 'application/pdf'
        self.download.file_size_bytes = 2048
        self.download.published = True
        self.download.save()

        payload = {
            'title': self.download.title,
            'slug': self.download.slug,
            'description': self.download.description,
            'storage_key': self.download.storage_key,
            'file_type': 'pdf',
            'asset_mime_type': 'application/pdf',
            'file_size_bytes': '2048',
            'required_level': '0',
            'published': 'on',
            'tags': '',
        }
        response = self.client.post(
            f'/studio/downloads/{self.download.pk}/edit',
            payload,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            'Private download object is missing or inaccessible',
            status_code=400,
        )
        self.download.refresh_from_db()
        self.assertFalse(self.download.published)
        self.assertFalse(self.download.delivery_ready)
        self.assertTrue(self.download.delivery_blocked_reason)

        payload.pop('published')
        draft_response = self.client.post(
            f'/studio/downloads/{self.download.pk}/edit',
            payload,
        )
        self.assertEqual(draft_response.status_code, 302)
        self.download.refresh_from_db()
        self.assertFalse(self.download.published)
        self.assertFalse(self.download.delivery_ready)
        object_exists.assert_called_once_with('downloads/edit-dl.pdf')

    def test_edit_nonexistent_returns_404(self):
        response = self.client.get('/studio/downloads/99999/edit')
        self.assertEqual(response.status_code, 404)

    def test_unready_published_download_can_be_unpublished(self):
        self.download.published = True
        self.download.storage_key = ''
        self.download.save(update_fields=['published', 'storage_key'])

        response = self.client.post(
            f'/studio/downloads/{self.download.pk}/edit',
            {
                'title': self.download.title,
                'slug': self.download.slug,
                'description': self.download.description,
                'file_type': 'pdf',
                'file_size_bytes': '0',
                'required_level': '0',
                'tags': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.download.refresh_from_db()
        self.assertFalse(self.download.published)
        self.assertFalse(self.download.delivery_ready)

    def test_synced_download_shows_origin_panel(self):
        download = Download.objects.create(
            title='Synced Download',
            slug='synced-download',
            file_url='https://example.com/file.pdf',
            source_repo='AI-Shipping-Labs/content',
            source_path='downloads/synced-download.yaml',
            source_commit='abc123def4567890',
        )

        response = self.client.get(f'/studio/downloads/{download.pk}/edit')

        self.assertContains(response, 'data-testid="origin-panel"')
        self.assertContains(response, 'Synced from GitHub')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'downloads/synced-download.yaml')
        self.assertContains(response, 'Edit on GitHub')
        self.assertContains(response, 'Re-sync source')
        self.assertContains(response, 'Successful downloads: 0')
        self.assertContains(response, 'disabled')
        self.assertNotContains(response, 'data-testid="synced-banner"')

    def test_manual_download_has_no_origin_panel(self):
        response = self.client.get(f'/studio/downloads/{self.download.pk}/edit')

        self.assertNotContains(response, 'data-testid="origin-panel"')
        self.assertNotContains(response, 'data-testid="synced-banner"')
