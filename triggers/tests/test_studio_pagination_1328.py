"""Pagination coverage for growing Studio trigger logs (issue #1328)."""

from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase

from triggers.models import (
    EventEmission,
    TriggerSubscription,
    WebhookDelivery,
    WebhookDeliveryJob,
)

User = get_user_model()


def _query_params(url):
    return parse_qs(urlparse(url).query)


class StudioTriggerPaginationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="trigger-pagination-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.subscription = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/primary",
            secret="primary-secret",
        )
        cls.other_subscription = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={},
            target_url="https://handler.example.com/other",
            secret="other-secret",
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def _create_emissions(self, count, *, prefix):
        return EventEmission.objects.bulk_create([
            EventEmission(
                event_name=f"{prefix}.{index:03d}",
                properties={},
                envelope_id=f"evt_{prefix}_{index:03d}",
            )
            for index in range(count)
        ])

    def _create_delivery_history(self, count=201):
        emissions = self._create_emissions(count, prefix="delivery")
        jobs = WebhookDeliveryJob.objects.bulk_create([
            WebhookDeliveryJob(
                emission=emission,
                subscription=self.subscription,
                target_url=self.subscription.target_url,
                encrypted_secret="encrypted-placeholder",
                secret_version=1,
                request_body="{}",
                status=WebhookDeliveryJob.STATUS_FAILED,
            )
            for emission in emissions
        ])
        WebhookDelivery.objects.bulk_create([
            WebhookDelivery(
                emission=job.emission,
                subscription=self.subscription,
                job=job,
                target_url=self.subscription.target_url,
                attempt=1,
                succeeded=False,
                response_status=500,
            )
            for job in jobs
        ])
        return jobs

    def test_emissions_beyond_former_200_cap_are_reachable_and_clamped(self):
        emissions = self._create_emissions(201, prefix="emission")
        oldest = emissions[0]

        first = self.client.get("/studio/triggers/emissions/")
        ninth = self.client.get("/studio/triggers/emissions/?page=9")

        self.assertEqual(len(first.context["emissions"]), 25)
        self.assertEqual(first.context["filtered_total"], 201)
        self.assertEqual([item.pk for item in ninth.context["emissions"]], [oldest.pk])
        self.assertContains(ninth, oldest.envelope_id)
        self.assertContains(first, 'data-testid="trigger-emission-list-pager"')

        cases = (("garbage", 1), ("0", 1), ("-1", 1), ("999", 9))
        for raw, expected in cases:
            with self.subTest(raw=raw):
                response = self.client.get(f"/studio/triggers/emissions/?page={raw}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["page"].number, expected)

    def test_emission_empty_state_and_25_row_page_do_not_render_pager(self):
        empty = self.client.get("/studio/triggers/emissions/")
        self.assertContains(empty, 'data-testid="emissions-empty"')
        self.assertContains(empty, "No emissions recorded yet")
        self.assertNotContains(empty, 'data-testid="trigger-emission-list-pager"')

        self._create_emissions(25, prefix="small")
        populated = self.client.get("/studio/triggers/emissions/")
        self.assertEqual(len(populated.context["emissions"]), 25)
        self.assertNotContains(populated, 'data-testid="trigger-emission-list-pager"')

    def test_delivery_jobs_and_attempts_remove_cap_and_page_independently(self):
        jobs = self._create_delivery_history()
        oldest_job = jobs[0]

        response = self.client.get(
            "/studio/triggers/deliveries/"
            f"?subscription={self.subscription.pk}&succeeded=false"
            "&jobs_page=9&attempts_page=1"
        )

        self.assertEqual(response.context["delivery_jobs_filtered_total"], 201)
        self.assertEqual(response.context["delivery_attempts_filtered_total"], 201)
        self.assertEqual(response.context["delivery_jobs_page"].number, 9)
        self.assertEqual(response.context["delivery_attempts_page"].number, 1)
        self.assertEqual([item.pk for item in response.context["delivery_jobs"]], [oldest_job.pk])
        self.assertEqual(len(response.context["deliveries"]), 25)

        attempt_next = _query_params(
            response.context["delivery_attempts_pager_next_url"]
        )
        self.assertEqual(attempt_next["subscription"], [str(self.subscription.pk)])
        self.assertEqual(attempt_next["succeeded"], ["false"])
        self.assertEqual(attempt_next["jobs_page"], ["9"])
        self.assertEqual(attempt_next["attempts_page"], ["2"])

        jobs_previous = _query_params(response.context["delivery_jobs_pager_prev_url"])
        self.assertEqual(jobs_previous["jobs_page"], ["8"])
        self.assertEqual(jobs_previous["attempts_page"], ["1"])
        self.assertEqual(jobs_previous["subscription"], [str(self.subscription.pk)])
        self.assertEqual(jobs_previous["succeeded"], ["false"])

        self.assertContains(response, 'data-testid="trigger-delivery-job-list-pager"')
        self.assertContains(response, 'data-testid="trigger-delivery-attempt-list-pager"')

    def test_delivery_filters_exclude_successes_and_other_subscriptions_on_page_two(self):
        self._create_delivery_history(26)
        other_emission = self._create_emissions(1, prefix="other")[0]
        other_job = WebhookDeliveryJob.objects.create(
            emission=other_emission,
            subscription=self.other_subscription,
            target_url=self.other_subscription.target_url,
            encrypted_secret="encrypted-placeholder",
            secret_version=1,
            request_body="{}",
            status=WebhookDeliveryJob.STATUS_FAILED,
        )
        WebhookDelivery.objects.create(
            emission=other_emission,
            subscription=self.other_subscription,
            job=other_job,
            target_url=self.other_subscription.target_url,
            attempt=1,
            succeeded=False,
            response_status=500,
        )
        success_emission = self._create_emissions(1, prefix="success")[0]
        success_job = WebhookDeliveryJob.objects.create(
            emission=success_emission,
            subscription=self.subscription,
            target_url=self.subscription.target_url,
            encrypted_secret="encrypted-placeholder",
            secret_version=1,
            request_body="{}",
            status=WebhookDeliveryJob.STATUS_SUCCEEDED,
        )
        WebhookDelivery.objects.create(
            emission=success_emission,
            subscription=self.subscription,
            job=success_job,
            target_url=self.subscription.target_url,
            attempt=1,
            succeeded=True,
            response_status=200,
        )

        response = self.client.get(
            "/studio/triggers/deliveries/"
            f"?subscription={self.subscription.pk}&succeeded=false"
            "&jobs_page=2&attempts_page=2"
        )
        self.assertEqual(len(response.context["delivery_jobs"]), 1)
        self.assertEqual(len(response.context["deliveries"]), 1)
        self.assertTrue(all(
            job.subscription_id == self.subscription.pk
            and job.status != WebhookDeliveryJob.STATUS_SUCCEEDED
            for job in response.context["delivery_jobs"]
        ))
        self.assertTrue(all(
            delivery.subscription_id == self.subscription.pk
            and not delivery.succeeded
            for delivery in response.context["deliveries"]
        ))

    def test_each_delivery_page_parameter_clamps_without_changing_the_other(self):
        self._create_delivery_history(26)
        cases = (
            ("garbage", "2", 1, 2),
            ("0", "-1", 1, 1),
            ("-5", "999", 1, 2),
            ("999", "garbage", 2, 1),
        )
        for jobs_raw, attempts_raw, jobs_expected, attempts_expected in cases:
            with self.subTest(jobs_raw=jobs_raw, attempts_raw=attempts_raw):
                response = self.client.get(
                    "/studio/triggers/deliveries/"
                    f"?jobs_page={jobs_raw}&attempts_page={attempts_raw}"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.context["delivery_jobs_page"].number,
                    jobs_expected,
                )
                self.assertEqual(
                    response.context["delivery_attempts_page"].number,
                    attempts_expected,
                )

    def test_delivery_empty_state_and_single_pages_render_without_pagers(self):
        empty = self.client.get("/studio/triggers/deliveries/")
        self.assertContains(empty, 'data-testid="deliveries-empty"')
        self.assertContains(empty, "No deliveries recorded yet")
        self.assertNotContains(empty, 'data-testid="trigger-delivery-job-list-pager"')
        self.assertNotContains(empty, 'data-testid="trigger-delivery-attempt-list-pager"')

        self._create_delivery_history(25)
        populated = self.client.get("/studio/triggers/deliveries/")
        self.assertEqual(len(populated.context["delivery_jobs"]), 25)
        self.assertEqual(len(populated.context["deliveries"]), 25)
        self.assertNotContains(populated, 'data-testid="trigger-delivery-job-list-pager"')
        self.assertNotContains(populated, 'data-testid="trigger-delivery-attempt-list-pager"')
