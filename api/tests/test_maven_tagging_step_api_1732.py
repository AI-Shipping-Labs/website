"""Operator API coverage for the Maven ``tagging`` step (issue #1732)."""

from api.tests.test_maven_occurrences_1568 import (
    COLLECTION_URL,
    MavenOccurrenceApiTestBase,
)
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    MAVEN_TAGS_APPLIED_NOTE,
    MAVEN_TAGS_NO_PREFIX_NOTE,
)


class MavenTaggingStepApiTest(MavenOccurrenceApiTestBase):
    def test_detail_exposes_tagging_first_in_the_step_ledger(self):
        occurrence = self.event("tagging-detail", user=self.member)

        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()

        self.assertEqual(body["steps"][0]["name"], "tagging")

    def test_tagging_appears_in_failed_steps_and_needs_attention_steps(self):
        occurrence = self.event(
            "tagging-failed-steps",
            user=self.member,
            tagging_status=MavenEnrollmentEvent.STEP_FAILED,
            tagging_attempts=3,
            tagging_error="RuntimeError",
        )

        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()

        self.assertIn("tagging", body["failed_steps"])
        self.assertIn("tagging", body["needs_attention_steps"])

    def test_failed_step_filter_accepts_tagging(self):
        failing = self.event(
            "tagging-filter-failing",
            user=self.member,
            tagging_status=MavenEnrollmentEvent.STEP_FAILED,
        )
        self.event(
            "tagging-filter-ok",
            user=self.member,
            tagging_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

        response = self.client.get(
            f"{COLLECTION_URL}?failed_step=tagging", **self.auth()
        )

        ids = [row["id"] for row in response.json()["occurrences"]]
        self.assertEqual(ids, [failing.pk])

    def test_retry_tagging_step_applies_the_tags_and_returns_the_refreshed_occurrence(self):
        occurrence = self.event(
            "tagging-retry",
            user=self.member,
            course_key="from-rag-to-agents",
            cohort_key="4",
            tagging_status=MavenEnrollmentEvent.STEP_FAILED,
            tagging_error="RuntimeError",
        )

        response = self.client.post(
            self.retry_url(occurrence, "tagging"), **self.auth()
        )

        body = response.json()
        self.assertEqual(body["retry"]["outcome"], "succeeded")
        self.assertEqual(body["occurrence"]["failed_steps"], [])
        self.member.refresh_from_db()
        self.assertEqual(
            sorted(self.member.tags), ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )

    def test_controlled_tagging_notes_survive_serialization_unredacted(self):
        applied = self.event(
            "tagging-note-applied",
            user=self.member,
            tagging_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            tagging_error=MAVEN_TAGS_APPLIED_NOTE,
        )
        unmapped = self.event(
            "tagging-note-unmapped",
            user=self.member,
            tagging_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            tagging_error=MAVEN_TAGS_NO_PREFIX_NOTE,
        )

        for occurrence, note in ((applied, MAVEN_TAGS_APPLIED_NOTE), (unmapped, MAVEN_TAGS_NO_PREFIX_NOTE)):
            with self.subTest(occurrence=occurrence.dedupe_key):
                body = self.client.get(
                    self.detail_url(occurrence), **self.auth()
                ).json()
                step = next(
                    row for row in body["steps"] if row["name"] == "tagging"
                )
                self.assertEqual(step["last_error"], note)
                self.assertFalse(step["error_redacted"])
