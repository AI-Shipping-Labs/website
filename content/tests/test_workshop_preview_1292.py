import uuid
from datetime import date

from django.test import TestCase, override_settings

from content.models import Workshop, WorkshopPage
from events.models import Event


class WorkshopPreviewTest(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(
            content_id=uuid.uuid4(),
            slug="draft-workshop-1292",
            title="Draft workshop",
            description="Secret preview description",
            date=date(2026, 7, 18),
            status="draft",
        )

    def test_token_is_stable_until_explicit_rotation(self):
        token = self.workshop.preview_token
        self.workshop.title = "Changed"
        self.workshop.save()
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.preview_token, token)
        self.workshop.regenerate_preview_token()
        self.assertNotEqual(self.workshop.preview_token, token)

    def test_legacy_null_token_never_builds_a_preview_url(self):
        Workshop.objects.filter(pk=self.workshop.pk).update(preview_token=None)
        self.workshop.refresh_from_db()
        self.assertEqual(self.workshop.get_preview_url(), "")

    def test_draft_requires_token_and_preview_is_noindex(self):
        self.assertEqual(self.client.get(self.workshop.get_absolute_url()).status_code, 404)
        response = self.client.get(self.workshop.get_preview_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secret preview description")
        self.assertEqual(
            response.headers["X-Robots-Tag"], "noindex, nofollow, noarchive"
        )
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        cache_control = response.headers["Cache-Control"]
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)
        self.assertIn("max-age=0", cache_control)
        self.assertEqual(
            self.client.get(f"/workshops/preview/{uuid.uuid4()}").status_code, 404
        )

    def test_published_preview_redirects_to_canonical(self):
        self.workshop.status = "published"
        self.workshop.save()
        response = self.client.get(self.workshop.get_preview_url())
        self.assertRedirects(response, self.workshop.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_preview_token_not_leaked_by_catalog_or_sitemap(self):
        token = str(self.workshop.preview_token)
        for path in ("/workshops/", "/sitemap.xml"):
            response = self.client.get(path)
            self.assertNotContains(response, token, status_code=response.status_code)

    @override_settings(GOOGLE_ANALYTICS_ID="G-SECRET-PREVIEW")
    def test_preview_preserves_page_recording_material_and_completion_privacy(self):
        event = Event.objects.create(
            title="Secret recording", slug="secret-recording-1292",
            start_datetime="2026-07-18T12:00:00Z",
            recording_url="https://youtube.com/watch?v=secret1292",
            materials=[{"title": "Secret deck 1292", "url": "https://secret.test/deck"}],
        )
        self.workshop.event = event
        self.workshop.pages_required_level = 20
        self.workshop.recording_required_level = 30
        self.workshop.save()
        WorkshopPage.objects.create(
            workshop=self.workshop, slug="secret-page", title="Secret page title",
            body="SECRET TUTORIAL BODY 1292", sort_order=0,
        )
        response = self.client.get(self.workshop.get_preview_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft workshop")
        self.assertNotContains(response, "SECRET TUTORIAL BODY 1292")
        self.assertNotContains(response, "secret1292")
        self.assertNotContains(response, "Secret deck 1292")
        self.assertNotContains(response, "Mark complete")
        self.assertNotContains(response, "G-SECRET-PREVIEW")
        self.assertContains(response, 'content="noindex,nofollow,noarchive"')
        self.assertEqual(
            self.client.get(f"/workshops/{self.workshop.slug}/tutorial/secret-page").status_code,
            404,
        )
