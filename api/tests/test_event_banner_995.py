"""API tests for event banner (re)generation (issue #995).

Covers the token-auth ``POST /api/events/<slug>/regenerate-banner`` endpoint,
the write-only ``generate_banner`` create/update trigger, and the ``banner_url``
serializer field. The banner pipeline is always mocked: ``enqueue_force`` and
``is_enabled`` are patched where they are imported in ``api.views.events`` so no
real Lambda/S3 call is made and call counts/args are asserted directly.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event

User = get_user_model()

ENQUEUE_PATH = "api.views.events.enqueue_force"
ENABLED_PATH = "api.views.events.banner_generator_is_enabled"


class EventBannerApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-banner@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-banner@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="banner")
        cls.non_staff_token = Token(
            key="non-staff-banner-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.start = timezone.now() + timedelta(days=7)
        cls.studio_event = Event.objects.create(
            title="Studio Banner Event",
            slug="studio-banner-event",
            description="Studio owned",
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status="draft",
            origin="studio",
        )
        cls.github_event = Event.objects.create(
            title="GitHub Banner Event",
            slug="github-banner-event",
            description="Synced",
            start_datetime=cls.start + timedelta(days=1),
            end_datetime=cls.start + timedelta(days=1, hours=1),
            status="upcoming",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/x.md",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/events",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, slug, payload, *, token=None):
        return self.client.patch(
            f"/api/events/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _regenerate(self, slug, *, token=None):
        return self.client.post(
            f"/api/events/{slug}/regenerate-banner",
            **self._auth(token),
        )

    def _create_payload(self, **extra):
        payload = {
            "title": "Banner Create Event",
            "start_datetime": (self.start + timedelta(days=20)).isoformat(),
        }
        payload.update(extra)
        return payload


class RegenerateBannerEndpointTest(EventBannerApiTestBase):
    def test_regenerate_success_returns_202_and_enqueues_once(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value="task-123") as enqueue:
            response = self._regenerate("studio-banner-event")

        self.assertEqual(response.status_code, 202)
        enqueue.assert_called_once_with("event", self.studio_event.pk)
        self.assertEqual(
            response.json(),
            {
                "status": "queued",
                "event_id": self.studio_event.pk,
                "slug": "studio-banner-event",
                "task_id": "task-123",
            },
        )

    def test_regenerate_unknown_event_404s_without_enqueue(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._regenerate("does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_event")
        enqueue.assert_not_called()

    def test_regenerate_disabled_returns_non_2xx_without_enqueue(self):
        with patch(ENABLED_PATH, return_value=False), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._regenerate("studio-banner-event")

        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["code"], "banner_generator_not_configured",
        )
        self.assertTrue(response.json()["error"])
        enqueue.assert_not_called()

    def test_regenerate_allows_synced_github_event(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value="task-9") as enqueue:
            response = self._regenerate("github-banner-event")

        self.assertEqual(response.status_code, 202)
        enqueue.assert_called_once_with("event", self.github_event.pk)

    def test_regenerate_swallowed_enqueue_still_returns_202_null_task(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value=None):
            response = self._regenerate("studio-banner-event")

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertIsNone(body["task_id"])

    def test_regenerate_requires_auth(self):
        for headers in ({}, {"HTTP_AUTHORIZATION": "Token nope"}):
            with self.subTest(headers=headers):
                with patch(ENQUEUE_PATH) as enqueue:
                    response = self.client.post(
                        "/api/events/studio-banner-event/regenerate-banner",
                        **headers,
                    )
                self.assertIn(response.status_code, (401, 403))
                enqueue.assert_not_called()

    def test_regenerate_non_staff_token_rejected(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self._regenerate(
                "studio-banner-event", token=self.non_staff_token,
            )
        self.assertIn(response.status_code, (401, 403))
        enqueue.assert_not_called()


class CreateAutoBannerTest(EventBannerApiTestBase):
    def test_create_default_auto_generates_banner(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value="task-9") as enqueue:
            response = self._post(self._create_payload())

        self.assertEqual(response.status_code, 201)
        body = response.json()
        new = Event.objects.get(slug=body["slug"])
        enqueue.assert_called_once_with("event", new.pk)
        self.assertEqual(body["banner_task_id"], "task-9")
        # generate_banner is a write-only trigger, never echoed back.
        self.assertNotIn("generate_banner", body)
        self.assertNotIn("banner_error", body)

    def test_create_explicit_true_auto_generates_banner(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value="task-7") as enqueue:
            response = self._post(
                self._create_payload(generate_banner=True),
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(response.json()["banner_task_id"], "task-7")

    def test_create_generate_banner_false_opts_out(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._post(
                self._create_payload(generate_banner=False),
            )
        self.assertEqual(response.status_code, 201)
        enqueue.assert_not_called()
        self.assertNotIn("banner_task_id", response.json())

    def test_create_with_generator_disabled_soft_fails(self):
        before = Event.objects.count()
        with patch(ENABLED_PATH, return_value=False), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._post(
                self._create_payload(generate_banner=True),
            )
        self.assertEqual(response.status_code, 201)
        enqueue.assert_not_called()
        body = response.json()
        self.assertIn("banner_error", body)
        self.assertTrue(body["banner_error"])
        self.assertNotIn("banner_task_id", body)
        # The event was still persisted.
        self.assertEqual(Event.objects.count(), before + 1)
        detail = self.client.get(
            f"/api/events/{body['slug']}", **self._auth(),
        )
        self.assertEqual(detail.status_code, 200)

    def test_create_non_boolean_generate_banner_rejected(self):
        before = Event.objects.count()
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._post(
                self._create_payload(generate_banner="yes"),
            )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("generate_banner", body["details"])
        enqueue.assert_not_called()
        self.assertEqual(Event.objects.count(), before)


class PatchBannerTest(EventBannerApiTestBase):
    def test_patch_without_generate_banner_does_not_enqueue(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._patch(
                "studio-banner-event", {"title": "New Title"},
            )
        self.assertEqual(response.status_code, 200)
        enqueue.assert_not_called()

    def test_patch_generate_banner_true_re_enqueues_once(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH, return_value="task-p") as enqueue:
            response = self._patch(
                "studio-banner-event", {"generate_banner": True},
            )
        self.assertEqual(response.status_code, 200)
        enqueue.assert_called_once_with("event", self.studio_event.pk)
        self.assertEqual(response.json()["banner_task_id"], "task-p")
        self.assertNotIn("generate_banner", response.json())

    def test_patch_non_boolean_generate_banner_rejected_without_mutation(self):
        with patch(ENABLED_PATH, return_value=True), \
                patch(ENQUEUE_PATH) as enqueue:
            response = self._patch(
                "studio-banner-event", {"generate_banner": "yes"},
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("generate_banner", response.json()["details"])
        enqueue.assert_not_called()


class SerializeBannerUrlTest(EventBannerApiTestBase):
    def test_detail_echoes_resolved_banner_url(self):
        self.studio_event.auto_banner_url = (
            "https://cdn.example.com/banners/event/x.jpg"
        )
        self.studio_event.save(update_fields=["auto_banner_url"])

        response = self.client.get(
            "/api/events/studio-banner-event", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["banner_url"],
            "https://cdn.example.com/banners/event/x.jpg",
        )
        self.assertNotIn("generate_banner", body)

    def test_cover_image_wins_over_auto_banner(self):
        self.studio_event.cover_image_url = "https://cdn.example.com/cover.png"
        self.studio_event.auto_banner_url = "https://cdn.example.com/auto.jpg"
        self.studio_event.save(
            update_fields=["cover_image_url", "auto_banner_url"],
        )
        response = self.client.get(
            "/api/events/studio-banner-event", **self._auth(),
        )
        self.assertEqual(
            response.json()["banner_url"], "https://cdn.example.com/cover.png",
        )

    def test_banner_url_empty_when_none_set(self):
        response = self.client.get(
            "/api/events/github-banner-event", **self._auth(),
        )
        self.assertEqual(response.json()["banner_url"], "")
