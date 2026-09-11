"""Safety and delivery contracts for monitored re-permission waves."""

import ast
import threading
from datetime import UTC, datetime, timedelta
from unittest import skipUnless
from unittest.mock import patch

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, tag
from django.utils import timezone
from django_q.models import Schedule

from accounts.models import TierOverride
from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign, EmailLog
from email_app.services.campaign_dispatch import retry_delivery
from email_app.services.campaign_repermission import (
    REPERMISSION_COPY,
    REPERMISSION_CTA,
    REPERMISSION_EMAIL_TYPE,
    repermission_body,
)
from email_app.services.campaign_waves import (
    bounce_threshold_breached,
    campaign_wave_summary,
    release_next_wave,
)
from email_app.services.email_service import UNSUBSCRIBED_AT_SEND, EmailService
from email_app.tasks.campaign_delivery_recovery import recover_campaign_deliveries
from email_app.tasks.send_campaign import (
    INACTIVE_AT_SEND,
    VERIFIED_AT_SEND,
    send_campaign,
    send_campaign_batch,
)
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


class RepermissionCampaignBase(TierSetupMixin):
    def make_user(self, suffix, **fields):
        values = {
            "email": f"{suffix}@example.com",
            "tier": self.free_tier,
            "email_verified": False,
            "unsubscribed": False,
        }
        values.update(fields)
        return create_user_with_membership(**values)

    def make_campaign(self, **fields):
        values = {
            "subject": "Please confirm",
            "body": "Hello from the team.",
            "audience_verification": "unverified_only",
        }
        values.update(fields)
        return EmailCampaign.objects.create(**values)


@tag("core")
class CampaignWaveModelTest(RepermissionCampaignBase, TestCase):
    def test_wave_number_is_positive_and_unique_within_campaign(self):
        campaign = self.make_campaign()
        CampaignWave.objects.create(campaign=campaign, number=1)

        for number in (0, 1):
            with self.subTest(number=number):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CampaignWave.objects.create(campaign=campaign, number=number)

    def test_standard_delivery_keeps_nullable_wave_relation(self):
        campaign = EmailCampaign.objects.create(subject="Standard", body="Body")
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            recipient_user_pk=123,
            recipient_email="historical@example.com",
        )

        self.assertIsNone(delivery.wave_id)


@tag("core")
class RepermissionAudienceTest(RepermissionCampaignBase, TestCase):
    def test_strict_audience_excludes_ineligible_addresses(self):
        eligible = self.make_user("eligible")
        self.make_user("verified", email_verified=True)
        self.make_user("unsubscribed", unsubscribed=True)
        self.make_user("inactive", is_active=False)
        self.make_user("permanent", bounce_state=User.BounceState.PERMANENT)

        recipients = list(self.make_campaign().get_eligible_recipients())

        self.assertEqual(recipients, [eligible])

    def test_standard_audience_modes_keep_their_existing_contract(self):
        verified = self.make_user("standard-verified", email_verified=True)
        unverified = self.make_user("standard-unverified")

        verified_campaign = EmailCampaign.objects.create(
            subject="Verified",
            body="Body",
        )
        everyone_campaign = EmailCampaign.objects.create(
            subject="Everyone",
            body="Body",
            audience_verification="everyone",
        )

        self.assertEqual(
            list(verified_campaign.get_eligible_recipients()),
            [verified],
        )
        self.assertCountEqual(
            everyone_campaign.get_eligible_recipients(),
            [verified, unverified],
        )

    def test_unverified_filter_composes_with_tier_tag_slack_and_event(self):
        event = Event.objects.create(
            title="Re-permission audience",
            slug="repermission-audience",
            start_datetime=datetime(2026, 9, 11, tzinfo=UTC),
        )
        eligible = self.make_user(
            "composed",
            tier=self.main_tier,
            slack_member=True,
            tags=["include"],
        )
        overridden = self.make_user(
            "overridden",
            tier=self.free_tier,
            slack_member=True,
            tags=["include"],
        )
        TierOverride.objects.create(
            user=overridden,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )
        verified = self.make_user(
            "composed-verified",
            tier=self.main_tier,
            email_verified=True,
            slack_member=True,
            tags=["include"],
        )
        wrong_slack = self.make_user(
            "composed-slack",
            tier=self.main_tier,
            slack_member=False,
            tags=["include"],
        )
        blocked = self.make_user(
            "composed-blocked",
            tier=self.main_tier,
            slack_member=True,
            tags=["include", "blocked"],
        )
        self.make_user(
            "composed-outsider",
            tier=self.main_tier,
            slack_member=True,
            tags=["include"],
        )
        for user in (eligible, overridden, verified, wrong_slack, blocked):
            EventRegistration.objects.create(event=event, user=user)
        campaign = self.make_campaign(
            target_event=event,
            target_min_level=20,
            target_tags_any=["include"],
            target_tags_none=["blocked"],
            slack_filter="yes",
        )

        self.assertCountEqual(
            campaign.get_eligible_recipients(),
            [eligible, overridden],
        )


@tag("core")
class RepermissionSnapshotTest(RepermissionCampaignBase, TestCase):
    def test_snapshot_is_oldest_first_and_releases_only_the_pilot(self):
        users = [self.make_user(f"wave-{index:03d}") for index in range(351)]
        base_time = timezone.now() - timedelta(days=400)
        for index, user in enumerate(reversed(users)):
            User.objects.filter(pk=user.pk).update(
                date_joined=base_time + timedelta(minutes=index),
            )
        expected_first = [user.pk for user in reversed(users)][0:100]
        campaign = self.make_campaign(status="sending")
        actor = User.objects.create_user(
            email="snapshot-operator@example.com",
            email_verified=True,
        )

        result = send_campaign(campaign.pk, released_by_id=actor.pk)

        waves = list(campaign.waves.prefetch_related("deliveries"))
        self.assertEqual(result["wave_count"], 3)
        self.assertEqual(
            [wave.deliveries.count() for wave in waves],
            [100, 250, 1],
        )
        self.assertEqual(waves[0].state, CampaignWave.State.SENDING)
        self.assertIsNotNone(waves[0].released_at)
        self.assertEqual(waves[0].released_by, actor)
        self.assertTrue(all(wave.released_at is None for wave in waves[1:]))
        self.assertEqual(
            list(
                waves[0]
                .deliveries.order_by("recipient_user_pk")
                .values_list(
                    "recipient_user_pk",
                    flat=True,
                )
            ),
            sorted(expected_first),
        )
        schedules = Schedule.objects.filter(
            func="email_app.tasks.send_campaign.send_campaign_batch",
        )
        self.assertEqual(schedules.count(), 1)
        scheduled_delivery_ids = ast.literal_eval(
            schedules.get().kwargs,
        )["delivery_ids"]
        scheduled_recipients = {
            delivery.pk: delivery.recipient_user_pk
            for delivery in CampaignDelivery.objects.filter(
                pk__in=scheduled_delivery_ids,
            )
        }
        self.assertEqual(
            [scheduled_recipients[delivery_id] for delivery_id in scheduled_delivery_ids],
            expected_first,
        )

    def test_recovery_never_queues_an_unreleased_wave(self):
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        wave = CampaignWave.objects.create(campaign=campaign, number=1)
        user = self.make_user("future")
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=wave,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
        )

        result = recover_campaign_deliveries()

        self.assertEqual(result["requeued_batches"], 0)
        self.assertFalse(Schedule.objects.exists())


@tag("core")
class RepermissionDeliveryTest(RepermissionCampaignBase, TestCase):
    def make_pending_delivery(self, suffix):
        user = self.make_user(suffix)
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        wave = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.SENDING,
            released_at=timezone.now(),
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            wave=wave,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
        )
        return user, campaign, wave, delivery

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_user_who_verified_after_snapshot_is_terminally_skipped(self, send):
        user, campaign, wave, delivery = self.make_pending_delivery(
            "late-verification",
        )
        User.objects.filter(pk=user.pk).update(email_verified=True)

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        wave.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(delivery.skip_reason, VERIFIED_AT_SEND)
        self.assertEqual(wave.state, CampaignWave.State.MONITORING)
        self.assertIsNotNone(wave.monitoring_started_at)
        send.assert_not_called()

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_user_who_unsubscribed_after_snapshot_is_terminally_skipped(self, send):
        user, campaign, _wave, delivery = self.make_pending_delivery(
            "late-unsubscribe",
        )
        User.objects.filter(pk=user.pk).update(unsubscribed=True)

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(delivery.skip_reason, UNSUBSCRIBED_AT_SEND)
        send.assert_not_called()

    @patch("email_app.tasks.send_campaign.EmailService.send_prepared")
    def test_user_deactivated_after_snapshot_is_terminally_skipped(self, send):
        user, campaign, _wave, delivery = self.make_pending_delivery(
            "late-inactive",
        )
        User.objects.filter(pk=user.pk).update(is_active=False)

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(delivery.skip_reason, INACTIVE_AT_SEND)
        send.assert_not_called()

    @patch(
        "email_app.tasks.send_campaign.EmailService.send_prepared",
        return_value="repermission-message",
    )
    def test_confirmed_send_records_the_repermission_email_type(self, _send):
        user = self.make_user("confirmed-repermission")
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        wave = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.SENDING,
            released_at=timezone.now(),
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            wave=wave,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
        )

        send_campaign_batch(campaign.pk, [delivery.pk], send_delay=0)

        delivery.refresh_from_db()
        self.assertEqual(delivery.email_log.email_type, REPERMISSION_EMAIL_TYPE)

    def test_renderer_adds_one_30_day_consent_action_and_no_generic_verify(self):
        user = self.make_user("rendered")
        before = (
            user.email_verified,
            user.unsubscribed,
            dict(user.email_preferences),
            user.verification_expires_at,
        )
        body = repermission_body("Introduction", user=user)

        prepared = EmailService().prepare_rendered(
            user,
            "Confirm",
            body,
            email_type=REPERMISSION_EMAIL_TYPE,
        )

        self.assertIn(REPERMISSION_COPY, prepared.full_html)
        self.assertIn(REPERMISSION_CTA, prepared.full_html)
        self.assertIn(REPERMISSION_COPY, prepared.plain_text)
        self.assertIn(REPERMISSION_CTA, prepared.plain_text)
        self.assertEqual(prepared.full_html.count("/api/verify-and-subscribe"), 1)
        self.assertEqual(prepared.plain_text.count("/api/verify-and-subscribe"), 1)
        self.assertNotIn("/api/verify-email", prepared.full_html)
        self.assertIsNotNone(prepared.unsubscribe_url)
        raw_token = (
            prepared.plain_text.split(
                "/api/verify-and-subscribe?token=",
                1,
            )[1]
            .split()[0]
            .rstrip(")")
        )
        payload = jwt.decode(raw_token, settings.SECRET_KEY, algorithms=["HS256"])
        self.assertEqual(payload["action"], "verify_and_subscribe")
        lifetime = payload["exp"] - int(timezone.now().timestamp())
        self.assertGreater(lifetime, 29 * 24 * 60 * 60)
        self.assertLessEqual(lifetime, 30 * 24 * 60 * 60)
        user.refresh_from_db()
        self.assertEqual(
            (
                user.email_verified,
                user.unsubscribed,
                user.email_preferences,
                user.verification_expires_at,
            ),
            before,
        )


@tag("core")
class RepermissionMonitoringTest(RepermissionCampaignBase, TestCase):
    def test_bounce_threshold_uses_exact_199_and_200_basis_point_boundaries(self):
        self.assertFalse(bounce_threshold_breached(199, 10_000))
        self.assertTrue(bounce_threshold_breached(200, 10_000))

    def make_sent_delivery(self, campaign, wave, suffix, *, bounced=False, complained=False):
        user = self.make_user(suffix)
        log = EmailLog.objects.create(
            campaign=campaign,
            user=user,
            recipient_email=user.email,
            email_type="campaign",
            bounced_at=timezone.now() if bounced else None,
            complained_at=timezone.now() if complained else None,
        )
        return CampaignDelivery.objects.create(
            campaign=campaign,
            wave=wave,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
            state=CampaignDelivery.State.SENT,
            email_log=log,
            completed_at=timezone.now(),
        )

    def make_releasable_campaign(self):
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        first = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        second = CampaignWave.objects.create(campaign=campaign, number=2)
        self.make_sent_delivery(campaign, first, "sent")
        future = self.make_user("next")
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=second,
            user=future,
            recipient_user_pk=future.pk,
            recipient_email=future.email,
        )
        return campaign, first, second

    def test_clean_wave_releases_next_once_at_24_hour_boundary(self):
        campaign, first, second = self.make_releasable_campaign()
        actor = User.objects.create_user(email="operator@example.com", is_staff=True)

        first_result = release_next_wave(campaign.pk, actor=actor, source="test")
        second_result = release_next_wave(campaign.pk, actor=actor, source="test")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first_result.released)
        self.assertFalse(second_result.released)
        self.assertEqual(first.state, CampaignWave.State.COMPLETE)
        self.assertEqual(second.state, CampaignWave.State.SENDING)
        self.assertEqual(second.released_by, actor)
        self.assertEqual(Schedule.objects.count(), 1)

    def test_zero_confirmed_sends_and_unresolved_delivery_fail_closed(self):
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        first = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        CampaignWave.objects.create(campaign=campaign, number=2)
        skipped_user = self.make_user("only-skipped")
        skipped = CampaignDelivery.objects.create(
            campaign=campaign,
            wave=first,
            user=skipped_user,
            recipient_user_pk=skipped_user.pk,
            recipient_email=skipped_user.email,
            state=CampaignDelivery.State.SKIPPED,
        )

        self.assertEqual(
            campaign_wave_summary(campaign)["blocking_reason"],
            "no_confirmed_sends",
        )

        self.make_sent_delivery(campaign, first, "confirmed")
        skipped.state = CampaignDelivery.State.AMBIGUOUS
        skipped.save(update_fields=["state"])
        self.assertEqual(
            campaign_wave_summary(campaign)["blocking_reason"],
            "unresolved_deliveries",
        )

    def test_observation_window_blocks_until_24_hours_elapsed(self):
        campaign, first, _second = self.make_releasable_campaign()
        first.monitoring_started_at = timezone.now() - timedelta(hours=23, minutes=59)
        first.save(update_fields=["monitoring_started_at"])

        summary = campaign_wave_summary(campaign)

        self.assertFalse(summary["can_release_next"])
        self.assertEqual(summary["blocking_reason"], "observation_window")

    def test_exact_two_percent_bounce_pauses_and_queues_nothing(self):
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        wave = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        CampaignWave.objects.create(campaign=campaign, number=2)
        for index in range(50):
            self.make_sent_delivery(
                campaign,
                wave,
                f"bounce-{index}",
                bounced=index == 0,
            )

        summary = campaign_wave_summary(campaign)

        campaign.refresh_from_db()
        wave.refresh_from_db()
        self.assertEqual(summary["blocking_reason"], "bounce_threshold")
        self.assertEqual(summary["cumulative"]["bounce_rate"], 2.0)
        self.assertEqual(campaign.status, "paused")
        self.assertEqual(wave.state, CampaignWave.State.PAUSED)
        self.assertFalse(Schedule.objects.exists())

    def test_any_complaint_pauses(self):
        campaign, first, _second = self.make_releasable_campaign()
        first.deliveries.all().delete()
        self.make_sent_delivery(campaign, first, "complaint", complained=True)

        summary = campaign_wave_summary(campaign)

        self.assertEqual(summary["blocking_reason"], "complaint_threshold")
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "paused")

    def test_complaint_pauses_while_other_deliveries_are_still_pending(self):
        campaign, first, _second = self.make_releasable_campaign()
        first.deliveries.all().delete()
        self.make_sent_delivery(campaign, first, "early-complaint", complained=True)
        pending_user = self.make_user("still-pending")
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=first,
            user=pending_user,
            recipient_user_pk=pending_user.pk,
            recipient_email=pending_user.email,
        )

        summary = campaign_wave_summary(campaign)

        campaign.refresh_from_db()
        self.assertEqual(summary["blocking_reason"], "complaint_threshold")
        self.assertEqual(campaign.status, "paused")

    @patch("jobs.tasks.async_task", return_value="retry-task")
    def test_retry_restarts_monitoring_window(self, _enqueue):
        campaign, first, _second = self.make_releasable_campaign()
        failed_user = self.make_user("retry-monitoring")
        failed = CampaignDelivery.objects.create(
            campaign=campaign,
            wave=first,
            user=failed_user,
            recipient_user_pk=failed_user.pk,
            recipient_email=failed_user.email,
            state=CampaignDelivery.State.FAILED,
        )

        retry_delivery(failed.pk, actor=failed_user)

        first.refresh_from_db()
        self.assertEqual(first.state, CampaignWave.State.SENDING)
        self.assertIsNone(first.monitoring_started_at)

    def test_final_wave_records_completion_only_after_observation(self):
        campaign = self.make_campaign(
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        wave = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        self.make_sent_delivery(campaign, wave, "final")

        summary = campaign_wave_summary(campaign)

        campaign.refresh_from_db()
        wave.refresh_from_db()
        self.assertEqual(summary["blocking_reason"], "campaign_complete")
        self.assertEqual(wave.state, CampaignWave.State.COMPLETE)
        self.assertIsNotNone(wave.completed_at)
        self.assertEqual(campaign.status, "sent")
        self.assertIsNotNone(campaign.sent_at)


@tag("core", "postgresql")
@skipUnless(connection.vendor == "postgresql", "requires PostgreSQL row locking")
class RepermissionWaveReleaseConcurrencyTest(TransactionTestCase):
    reset_sequences = True

    def test_simultaneous_release_claims_enqueue_the_next_wave_once(self):
        campaign = EmailCampaign.objects.create(
            subject="Concurrent monitored release",
            body="Body",
            audience_verification="unverified_only",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        first = CampaignWave.objects.create(
            campaign=campaign,
            number=1,
            state=CampaignWave.State.MONITORING,
            released_at=timezone.now() - timedelta(hours=25),
            monitoring_started_at=timezone.now() - timedelta(hours=24),
        )
        second = CampaignWave.objects.create(campaign=campaign, number=2)
        sent_user = User.objects.create_user(
            email="concurrent-wave-sent@example.com",
            email_verified=False,
        )
        sent_log = EmailLog.objects.create(
            campaign=campaign,
            user=sent_user,
            recipient_email=sent_user.email,
            email_type=REPERMISSION_EMAIL_TYPE,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=first,
            user=sent_user,
            recipient_user_pk=sent_user.pk,
            recipient_email=sent_user.email,
            state=CampaignDelivery.State.SENT,
            email_log=sent_log,
        )
        pending_user = User.objects.create_user(
            email="concurrent-wave-pending@example.com",
            email_verified=False,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            wave=second,
            user=pending_user,
            recipient_user_pk=pending_user.pk,
            recipient_email=pending_user.email,
        )
        actor = User.objects.create_user(
            email="concurrent-wave-operator@example.com",
            is_staff=True,
        )
        start = threading.Barrier(3)
        results = []
        errors = []

        def contender():
            close_old_connections()
            try:
                start.wait(timeout=5)
                results.append(
                    release_next_wave(
                        campaign.pk,
                        actor=actor,
                        source="concurrent wave test",
                    )
                )
            except Exception as exc:  # pragma: no cover - assertion captures it
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=contender) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(
            any(thread.is_alive() for thread in threads),
            "wave release worker threads did not finish",
        )
        self.assertEqual(errors, [])
        self.assertEqual(sum(result.released for result in results), 1)
        self.assertEqual(Schedule.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.state, CampaignWave.State.SENDING)
