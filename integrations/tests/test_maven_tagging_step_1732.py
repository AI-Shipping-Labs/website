"""Maven ``tagging`` ledger step and removal retraction (issue #1732).

Covers the forward path only: the webhook applies the production CRM tag
convention (``maven`` / ``<prefix>`` / ``<prefix>-<cohort_key>``) through its
own retryable ledger step, retracts the buildcamp tags on
``user_cohort.removed``, and stamps webhook-created accounts
``signup_source=maven_webhook``. Cohorts 1-4 were tagged by an operator
contact import in production, so nothing here backfills anything.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from community.models import CommunityAuditLog
from integrations.config import clear_config_cache, set_package_override
from integrations.maven_config import (
    DEFAULT_COURSE_TAG_PREFIXES,
    maven_course_tag_prefix,
    maven_course_tag_prefixes,
)
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import (
    MAVEN_TAGS_APPLIED_NOTE,
    MAVEN_TAGS_NO_PREFIX_NOTE,
    MAVEN_TAGS_NO_USER_NOTE,
    MAX_STEP_ATTEMPTS,
    MavenTransientError,
    handle_maven_event,
    retry_occurrence_step,
    run_occurrence_steps,
)
from integrations.services.maven_attention import (
    failed_occurrences,
    failed_step_names,
    needs_attention_occurrences,
)

User = get_user_model()

WEBHOOK_URL = "/api/webhooks/maven"
SECRET = "tagging-secret-1732"
COURSE_KEY = "from-rag-to-agents"


def _set(key, value):
    IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})


class _MavenTaggingBase(TestCase):
    """Shared Maven-enabled harness; the welcome mail is always stubbed."""

    def setUp(self):
        _set("MAVEN_ENROLLMENT_ENABLED", "true")
        _set("MAVEN_WEBHOOK_SHARED_SECRET", SECRET)
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        patcher = patch("integrations.services.maven.send_package_mail")
        self.package_mail = patcher.start()
        self.addCleanup(patcher.stop)

    def _enroll(self, email, *, cohort="4", cohort_key=None, course_key=COURSE_KEY):
        payload = {
            "event": "user_cohort.enrolled",
            "email": email,
            "course": {"name": "AI Engineering Buildcamp", "id": course_key},
            "cohort": {"name": cohort, "id": cohort_key or cohort},
        }
        return handle_maven_event(payload)

    def _remove(self, email, *, cohort="4", cohort_key=None, course_key=COURSE_KEY):
        payload = {
            "event": "user_cohort.removed",
            "email": email,
            "course": {"name": "AI Engineering Buildcamp", "id": course_key},
            "cohort": {"name": cohort, "id": cohort_key or cohort},
        }
        return handle_maven_event(payload)

    def _tags(self, email):
        return sorted(User.objects.get(email=email).tags or [])

    def _slugs(self, email):
        user = User.objects.get(email=email)
        return sorted(user.contact_tags.values_list("slug", flat=True))


class MavenTaggingOnEnrollmentTest(_MavenTaggingBase):
    def test_new_enrollee_gets_all_three_tags_and_a_succeeded_step(self):
        result = self._enroll("fresh@test.com")

        self.assertEqual(
            self._tags("fresh@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )
        # The relation the CRM filters and campaign audiences read must agree
        # with the JSON column, not just the column.
        self.assertEqual(
            self._slugs("fresh@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )
        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(occurrence.tagging_error, MAVEN_TAGS_APPLIED_NOTE)

    def test_existing_account_is_tagged_without_creating_a_second_account(self):
        User.objects.create_user(email="already@test.com", password="pw")

        result = self._enroll("already@test.com")

        self.assertEqual(User.objects.filter(email="already@test.com").count(), 1)
        self.assertFalse(result.created_user)
        self.assertEqual(
            self._tags("already@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )

    def test_already_member_occurrence_is_still_tagged(self):
        member = User.objects.create_user(email="member@test.com", password="pw")
        with patch(
            "integrations.services.maven._is_active_community_member",
            return_value=True,
        ):
            result = self._enroll("member@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(occurrence.outcome, MavenEnrollmentEvent.OUTCOME_ALREADY_MEMBER)
        self.assertFalse(occurrence.welcome_eligible)
        self.assertEqual(
            self._tags(member.email),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )

    def test_cohort_tag_comes_from_cohort_key_not_the_display_label(self):
        # Production delivers "4" and "4/11" as the cohort label for the same
        # cohort; cohort_key is stably "4".
        self._enroll("labelled@test.com", cohort="4/11", cohort_key="4")

        tags = self._tags("labelled@test.com")
        self.assertIn("ai-buildcamp-4", tags)
        self.assertNotIn("ai-buildcamp-411", tags)

    def test_redelivery_neither_duplicates_tags_nor_reruns_the_step(self):
        first = self._enroll("repeat@test.com")
        occurrence = MavenEnrollmentEvent.objects.get(pk=first.occurrence_id)
        attempts_after_first = occurrence.tagging_attempts

        self._enroll("repeat@test.com")

        occurrence.refresh_from_db()
        self.assertEqual(occurrence.tagging_attempts, attempts_after_first)
        self.assertEqual(
            User.objects.get(email="repeat@test.com").tags,
            ["maven", "ai-buildcamp", "ai-buildcamp-4"],
        )

    def test_unmapped_course_key_applies_maven_only_with_the_allowlisted_note(self):
        result = self._enroll("other-course@test.com", course_key="some-other-course")

        self.assertEqual(self._tags("other-course@test.com"), ["maven"])
        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(occurrence.tagging_error, MAVEN_TAGS_NO_PREFIX_NOTE)

    def test_blank_cohort_key_applies_the_course_tag_without_a_cohort_tag(self):
        self._enroll("no-cohort@test.com", cohort="", cohort_key="")

        self.assertEqual(self._tags("no-cohort@test.com"), ["ai-buildcamp", "maven"])

    def test_occurrence_without_a_user_skips_the_step(self):
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key="no-user-1732",
            identity_hash="no-user-1732",
            user=None,
            course_key=COURSE_KEY,
            cohort_key="4",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type="user_cohort.enrolled",
            tagging_status=MavenEnrollmentEvent.STEP_PENDING,
        )

        retry_occurrence_step(occurrence, "tagging")

        occurrence.refresh_from_db()
        self.assertEqual(occurrence.tagging_status, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertEqual(occurrence.tagging_error, MAVEN_TAGS_NO_USER_NOTE)

    def test_tagging_writes_no_audit_row_and_sends_no_mail_of_its_own(self):
        user = User.objects.create_user(email="quiet@test.com", password="pw")
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key="quiet-1732",
            identity_hash="quiet-1732",
            user=user,
            course_key=COURSE_KEY,
            cohort_key="4",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type="user_cohort.enrolled",
            tagging_status=MavenEnrollmentEvent.STEP_PENDING,
        )

        retry_occurrence_step(occurrence, "tagging")

        self.assertFalse(CommunityAuditLog.objects.filter(user=user).exists())
        self.package_mail.assert_not_called()


class MavenTaggingStepIndependenceTest(_MavenTaggingBase):
    def test_tags_are_applied_even_when_the_override_step_fails(self):
        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=RuntimeError("tier backend down"),
        ), self.assertRaises(MavenTransientError):
            # A failed entitlement below the attempt ceiling still asks Maven
            # to redeliver; the tags must already be in place by then.
            self._enroll("override-broken@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(
            email="override-broken@test.com",
        )
        self.assertEqual(occurrence.override_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(
            self._tags("override-broken@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )

    def test_a_failed_tagging_step_does_not_block_the_rest_of_the_ledger(self):
        with patch(
            "integrations.services.maven._run_tagging_step",
            side_effect=RuntimeError("tag store down"),
        ):
            result = self._enroll("tagging-broken@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(occurrence.tagging_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertEqual(occurrence.tagging_error, "RuntimeError")
        self.assertEqual(
            occurrence.override_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(
            occurrence.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)

    def test_a_failed_tagging_step_is_queued_for_attention_and_recovers_on_retry(self):
        with patch(
            "integrations.services.maven._run_tagging_step",
            side_effect=RuntimeError("tag store down"),
        ):
            result = self._enroll("recoverable@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        # ``enrollment`` also fails here — no content.Course declares this
        # maven_course_key — so assert on tagging's presence and its lead
        # position in the canonical ledger order rather than on the whole list.
        self.assertIn("tagging", failed_step_names(occurrence))
        self.assertEqual(failed_step_names(occurrence)[0], "tagging")
        self.assertIn(
            occurrence.pk,
            list(failed_occurrences().values_list("pk", flat=True)),
        )
        occurrence.tagging_attempts = MAX_STEP_ATTEMPTS
        occurrence.save(update_fields=["tagging_attempts"])
        self.assertIn(
            occurrence.pk,
            list(needs_attention_occurrences().values_list("pk", flat=True)),
        )

        retry = retry_occurrence_step(occurrence, "tagging")

        occurrence.refresh_from_db()
        self.assertTrue(retry.attempted)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(
            self._tags("recoverable@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )


class MavenTaggingForcedRetryTest(_MavenTaggingBase):
    def _skipped_occurrence(self, user, *, lifecycle, key):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key,
            identity_hash=key,
            user=user,
            course_key=COURSE_KEY,
            cohort_key="4",
            lifecycle=lifecycle,
            event_type="user_cohort.enrolled",
            tagging_status=MavenEnrollmentEvent.STEP_SKIPPED,
            override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )

    def test_forced_retry_recovers_a_skipped_step_on_an_active_occurrence(self):
        user = User.objects.create_user(email="backfilled@test.com", password="pw")
        occurrence = self._skipped_occurrence(
            user, lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE, key="active-1732",
        )

        result = retry_occurrence_step(occurrence, "tagging")

        occurrence.refresh_from_db()
        self.assertTrue(result.attempted)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(
            self._tags("backfilled@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )

    def test_forced_retry_on_a_removed_occurrence_is_declined_and_applies_no_tags(self):
        user = User.objects.create_user(email="gone@test.com", password="pw")
        occurrence = self._skipped_occurrence(
            user, lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED, key="removed-1732",
        )

        result = retry_occurrence_step(occurrence, "tagging")

        occurrence.refresh_from_db()
        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "not_retryable")
        self.assertEqual(occurrence.tagging_status, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertEqual(occurrence.tagging_attempts, 0)
        self.assertEqual(self._tags("gone@test.com"), [])


class MavenTagRetractionOnRemovalTest(_MavenTaggingBase):
    def test_removal_drops_the_cohort_and_course_tags_and_keeps_maven(self):
        self._enroll("leaving@test.com")
        user = User.objects.get(email="leaving@test.com")
        user.tags = [*user.tags, "newsletter-vip"]
        user.save(update_fields=["tags"])

        self._remove("leaving@test.com")

        self.assertEqual(self._tags("leaving@test.com"), ["maven", "newsletter-vip"])
        self.assertEqual(self._slugs("leaving@test.com"), ["maven", "newsletter-vip"])

    def test_removal_keeps_the_course_tag_when_another_cohort_tag_remains(self):
        self._enroll("returning@test.com", cohort="3", cohort_key="3")
        self._enroll("returning@test.com", cohort="4", cohort_key="4")
        self.assertEqual(
            self._tags("returning@test.com"),
            ["ai-buildcamp", "ai-buildcamp-3", "ai-buildcamp-4", "maven"],
        )

        self._remove("returning@test.com", cohort="4", cohort_key="4")

        self.assertEqual(
            self._tags("returning@test.com"),
            ["ai-buildcamp", "ai-buildcamp-3", "maven"],
        )

    def test_retraction_runs_on_an_operator_backfilled_skipped_occurrence(self):
        # Cohorts 1-4 were tagged outside the ledger, so their occurrences are
        # tagging_status=skipped — removal must still retract.
        user = User.objects.create_user(email="cohort3@test.com", password="pw")
        user.tags = ["maven", "ai-buildcamp", "ai-buildcamp-3"]
        user.save(update_fields=["tags"])
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key="legacy-cohort-3",
            identity_hash="legacy-cohort-3",
            user=user,
            email=user.email,
            course_key=COURSE_KEY,
            cohort_key="3",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED,
            event_type="user_cohort.removed",
            tagging_status=MavenEnrollmentEvent.STEP_SKIPPED,
            removal_status=MavenEnrollmentEvent.STEP_PENDING,
        )

        run_occurrence_steps(occurrence)

        occurrence.refresh_from_db()
        self.assertEqual(occurrence.removal_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(self._tags("cohort3@test.com"), ["maven"])

    def test_removal_succeeds_when_the_tags_are_already_absent(self):
        User.objects.create_user(email="untagged@test.com", password="pw")

        result = self._remove("untagged@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(occurrence.removal_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(self._tags("untagged@test.com"), [])

    def test_removal_succeeds_when_no_account_is_linked(self):
        result = self._remove("ghost@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertIsNone(occurrence.user_id)
        self.assertEqual(occurrence.removal_status, MavenEnrollmentEvent.STEP_SUCCEEDED)

    def test_removal_succeeds_and_retracts_nothing_for_an_unmapped_course(self):
        self._enroll("unmapped@test.com", course_key="mystery-course")
        self.assertEqual(self._tags("unmapped@test.com"), ["maven"])

        result = self._remove("unmapped@test.com", course_key="mystery-course")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(occurrence.removal_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(self._tags("unmapped@test.com"), ["maven"])

    def test_re_enrolling_after_removal_reapplies_every_tag(self):
        self._enroll("comeback@test.com")
        self._remove("comeback@test.com")
        self.assertEqual(self._tags("comeback@test.com"), ["maven"])

        result = self._enroll("comeback@test.com")

        occurrence = MavenEnrollmentEvent.objects.get(pk=result.occurrence_id)
        self.assertEqual(
            occurrence.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertEqual(
            self._tags("comeback@test.com"),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )


class MavenCourseTagPrefixConfigTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_unset_config_resolves_the_built_in_default_map(self):
        self.assertEqual(maven_course_tag_prefix(COURSE_KEY), "ai-buildcamp")
        self.assertEqual(maven_course_tag_prefixes(), DEFAULT_COURSE_TAG_PREFIXES)

    def test_course_key_is_matched_case_insensitively(self):
        self.assertEqual(maven_course_tag_prefix("From-RAG-To-Agents"), "ai-buildcamp")

    def test_unmapped_and_blank_course_keys_resolve_to_no_prefix(self):
        self.assertEqual(maven_course_tag_prefix("nope"), "")
        self.assertEqual(maven_course_tag_prefix(""), "")

    def test_a_stored_override_replaces_the_default_map(self):
        set_package_override(
            "MAVEN_COURSE_TAG_PREFIXES",
            json.dumps({"agentic-evals": "ai-evals"}),
            actor_ref="test",
        )
        clear_config_cache()

        self.assertEqual(maven_course_tag_prefix("agentic-evals"), "ai-evals")
        self.assertEqual(maven_course_tag_prefix(COURSE_KEY), "")

    # Studio coerces the value on save, so a malformed map can only reach the
    # resolver through the settings/environment layer — which is exactly what
    # ``override_settings`` exercises here.
    @override_settings(MAVEN_COURSE_TAG_PREFIXES='{"broken": ')
    def test_invalid_json_falls_back_to_the_default_map_with_a_warning(self):
        with self.assertLogs("integrations.maven_config", level="WARNING") as logs:
            resolved = maven_course_tag_prefixes()

        self.assertEqual(resolved, DEFAULT_COURSE_TAG_PREFIXES)
        self.assertIn("MAVEN_COURSE_TAG_PREFIXES", logs.output[0])

    @override_settings(MAVEN_COURSE_TAG_PREFIXES='["ai-buildcamp"]')
    def test_a_non_object_payload_falls_back_to_the_default_map(self):
        with self.assertLogs("integrations.maven_config", level="WARNING"):
            resolved = maven_course_tag_prefixes()

        self.assertEqual(resolved, DEFAULT_COURSE_TAG_PREFIXES)

    @override_settings(MAVEN_COURSE_TAG_PREFIXES='{"agentic-evals": 4}')
    def test_non_string_values_fall_back_to_the_default_map(self):
        with self.assertLogs("integrations.maven_config", level="WARNING"):
            resolved = maven_course_tag_prefixes()

        self.assertEqual(resolved, DEFAULT_COURSE_TAG_PREFIXES)

    @override_settings(MAVEN_COURSE_TAG_PREFIXES='{"agentic-evals": "ai-evals"}')
    def test_a_malformed_map_never_leaves_an_enrollee_untagged(self):
        self.assertEqual(maven_course_tag_prefix("agentic-evals"), "ai-evals")


class MavenSecondCourseOnboardingTest(_MavenTaggingBase):
    def test_a_stored_prefix_map_changes_the_next_enrollment_with_no_redeploy(self):
        set_package_override(
            "MAVEN_COURSE_TAG_PREFIXES",
            json.dumps(
                {COURSE_KEY: "ai-buildcamp", "agentic-evals": "ai-evals"},
            ),
            actor_ref="test",
        )
        clear_config_cache()

        self._enroll("evals@test.com", course_key="agentic-evals", cohort="1")

        self.assertEqual(
            self._tags("evals@test.com"), ["ai-evals", "ai-evals-1", "maven"],
        )
