"""Events API parity for event-series auto-enrollment (issue #1357)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from content.access import LEVEL_MAIN
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.occurrence_publication import (
    run_occurrence_publication_lifecycle,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


class EventSeriesAutoEnrollApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email="staff-1357@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="events-1357")
        cls.registrant = User.objects.create_user(
            email="registrant-1357@test.com",
            password="pw",
            email_verified=True,
            tier=cls.main_tier,
        )
        cls.series = EventSeries.objects.create(
            name="API enrollment series",
            slug="api-enrollment-series-1357",
            cadence="none",
            day_of_week=None,
            start_time=None,
            timezone="Europe/Berlin",
            required_level=0,
        )

    def setUp(self):
        SeriesRegistration.objects.create(
            series=self.series,
            user=self.registrant,
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _post(self, **overrides):
        payload = {
            "title": "API series occurrence",
            "description": "An occurrence created through the events API.",
            "start_datetime": (timezone.now() + timedelta(days=7)).isoformat(),
            "status": "upcoming",
            "event_series": self.series.slug,
            "generate_banner": False,
        }
        payload.update(overrides)
        return self.client.post(
            "/api/events",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _patch(self, slug, payload):
        return self.client.patch(
            f"/api/events/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    @patch("events.tasks.notify_series_invite.enqueue_series_update")
    def test_post_upcoming_series_event_enrolls_and_queues_targeted_update(
        self,
        enqueue_update,
    ):
        with patch(
            "api.views.events.run_occurrence_publication_lifecycle",
            wraps=run_occurrence_publication_lifecycle,
        ) as lifecycle:
            response = self._post()

        self.assertEqual(response.status_code, 201)
        event = Event.objects.get(slug=response.json()["slug"])
        self.assertEqual(
            response.json()["event_series"],
            {
                "id": self.series.pk,
                "slug": self.series.slug,
                "name": self.series.name,
            },
        )
        lifecycle.assert_called_once_with(event)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=event,
                user=self.registrant,
            ).count(),
            1,
        )
        enqueue_update.assert_called_once_with(event.pk, [self.registrant.pk])

    @patch("events.tasks.notify_series_invite.enqueue_series_update")
    def test_patch_attach_preserves_ad_hoc_fields_and_enrolls_once(
        self,
        enqueue_update,
    ):
        start = timezone.now() + timedelta(days=8)
        event = Event.objects.create(
            title="Standalone title",
            slug="standalone-attach-1357",
            description="Attach this event without rewriting it.",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="upcoming",
            origin="studio",
        )

        with patch(
            "api.views.events.run_occurrence_publication_lifecycle",
            wraps=run_occurrence_publication_lifecycle,
        ) as lifecycle:
            response = self._patch(
                event.slug,
                {"event_series": self.series.pk},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Standalone title")
        self.assertIsNone(response.json()["series_position"])
        event.refresh_from_db()
        self.assertEqual(event.event_series_id, self.series.pk)
        lifecycle.assert_called_once_with(event)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=event,
                user=self.registrant,
            ).count(),
            1,
        )
        enqueue_update.assert_called_once_with(event.pk, [self.registrant.pk])

    @patch("events.tasks.notify_series_invite.enqueue_series_update")
    def test_draft_publish_and_idempotent_patch_enroll_and_queue_once(
        self,
        enqueue_update,
    ):
        draft_response = self._post(
            title="Draft then publish",
            slug="draft-then-publish-1357",
            status="draft",
        )
        self.assertEqual(draft_response.status_code, 201)
        event = Event.objects.get(slug="draft-then-publish-1357")
        self.assertFalse(
            EventRegistration.objects.filter(
                event=event,
                user=self.registrant,
            ).exists(),
        )
        enqueue_update.assert_not_called()

        publish_response = self._patch(event.slug, {"status": "upcoming"})
        self.assertEqual(publish_response.status_code, 200)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=event,
                user=self.registrant,
            ).count(),
            1,
        )
        enqueue_update.assert_called_once_with(event.pk, [self.registrant.pk])

        retry_response = self._patch(event.slug, {"status": "upcoming"})
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=event,
                user=self.registrant,
            ).count(),
            1,
        )
        enqueue_update.assert_called_once_with(event.pk, [self.registrant.pk])

    @patch("events.tasks.notify_series_invite.enqueue_series_update")
    def test_opt_out_and_tier_access_prevent_enrollment_and_update(
        self,
        enqueue_update,
    ):
        opted_out = User.objects.create_user(
            email="opted-out-1357@test.com",
            password="pw",
            email_verified=True,
            tier=self.main_tier,
        )
        standing_registration = SeriesRegistration.objects.create(
            series=self.series,
            user=opted_out,
        )
        standing_registration.delete()
        inaccessible = User.objects.create_user(
            email="inaccessible-1357@test.com",
            password="pw",
            email_verified=True,
            tier=self.free_tier,
        )
        SeriesRegistration.objects.create(
            series=self.series,
            user=inaccessible,
        )
        SeriesRegistration.objects.filter(
            series=self.series,
            user=self.registrant,
        ).delete()

        response = self._post(
            title="Main-only occurrence",
            slug="main-only-occurrence-1357",
            required_level=LEVEL_MAIN,
        )

        self.assertEqual(response.status_code, 201)
        event = Event.objects.get(slug="main-only-occurrence-1357")
        self.assertFalse(
            EventRegistration.objects.filter(
                event=event,
                user__in=[opted_out, inaccessible],
            ).exists(),
        )
        enqueue_update.assert_not_called()

    @patch("events.tasks.notify_series_invite.enqueue_series_update")
    def test_non_eligible_and_unattached_events_are_noops(self, enqueue_update):
        cases = [
            {
                "title": "Draft occurrence",
                "slug": "draft-occurrence-1357",
                "status": "draft",
            },
            {
                "title": "Cancelled occurrence",
                "slug": "cancelled-occurrence-1357",
                "status": "cancelled",
            },
            {
                "title": "Past occurrence",
                "slug": "past-occurrence-1357",
                "start_datetime": (
                    timezone.now() - timedelta(days=2)
                ).isoformat(),
                "end_datetime": (
                    timezone.now() - timedelta(days=2, hours=-1)
                ).isoformat(),
            },
            {
                "title": "Unattached occurrence",
                "slug": "unattached-occurrence-1357",
                "event_series": None,
            },
        ]

        for payload in cases:
            with self.subTest(slug=payload["slug"]):
                response = self._post(**payload)
                self.assertEqual(response.status_code, 201)
                event = Event.objects.get(slug=payload["slug"])
                self.assertFalse(
                    EventRegistration.objects.filter(event=event).exists(),
                )
        enqueue_update.assert_not_called()

    def test_failed_create_transaction_does_not_run_lifecycle(self):
        with (
            patch(
                "api.views.events._set_event_hosts",
                side_effect=RuntimeError("host write failed"),
            ),
            patch(
                "api.views.events.run_occurrence_publication_lifecycle",
            ) as lifecycle,
            self.assertRaisesRegex(RuntimeError, "host write failed"),
        ):
            self._post(slug="rolled-back-occurrence-1357")

        self.assertFalse(
            Event.objects.filter(slug="rolled-back-occurrence-1357").exists(),
        )
        lifecycle.assert_not_called()
        self.assertFalse(
            EventRegistration.objects.filter(user=self.registrant).exists(),
        )
