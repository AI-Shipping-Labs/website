"""Policy tests for the final pytest-collected Playwright owner baseline."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import textwrap
from pathlib import Path

from django.test import SimpleTestCase

from playwright_tests.worktree_guard import PlaywrightWorktreeGuard
from scripts.playwright_owner_inventory import (
    InventoryError,
    collect_inventory,
    collect_owner_ids,
    load_live_manifest,
    validate_inventory,
)
from scripts.playwright_owner_inventory_ceilings import (
    LEGACY_DECLARED_BROWSER_CEILING,
    LEGACY_NON_BROWSER_CEILING,
)

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".tmp" / "playwright-owner-policy-tests"


class SyntheticCollectionTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="case-", dir=SCRATCH))
        (self.root / ".tmp").mkdir()
        (self.root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (self.root / "playwright_tests").mkdir()
        (self.root / "playwright_tests" / "__init__.py").write_text("", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root)
        super().tearDown()

    def write(self, relative_path: str, source: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")

    def collect(self):
        return collect_owner_ids(root=self.root)

    def collect_details(self):
        return collect_inventory(root=self.root)


class FinalPytestCollectionTests(SyntheticCollectionTestCase):
    def test_only_explicit_final_callables_are_reported_as_declared(self):
        self.write(
            "playwright_tests/test_declarations.py",
            """
            import functools
            import pytest

            from scripts.browser_journey_policy import browser_journey


            pytestmark = pytest.mark.core


            def wrapping(decorated):
                @functools.wraps(decorated)
                def wrapper():
                    return decorated()
                return wrapper


            @wrapping
            @browser_journey
            def test_wrapped_after_declaration():
                return None


            @browser_journey
            @wrapping
            def test_final_wrapper_declared():
                return None


            @browser_journey
            def test_replaced_through_globals():
                return None


            def replacement():
                return None


            globals()["test_replaced_through_globals"] = replacement


            class TestOrdinaryInheritedMark:
                pytestmark = pytest.mark.core

                def test_mark_is_not_declaration(self):
                    return None


            def generated_callable():
                return None
            """,
        )
        self.write(
            "conftest.py",
            """
            import pytest

            from scripts.browser_journey_policy import browser_journey


            def pytest_pycollect_makeitem(collector, name, obj):
                if name == "generated_callable":
                    return pytest.Function.from_parent(
                        collector,
                        name="test_plugin_generated",
                        callobj=browser_journey(obj),
                    )
                return None
            """,
        )

        inventory = self.collect_details()

        self.assertEqual(
            inventory.declared_owners,
            {
                "playwright_tests/test_declarations.py::test_final_wrapper_declared",
                "playwright_tests/test_declarations.py::test_plugin_generated",
            },
        )
        self.assertNotIn(
            "playwright_tests/test_declarations.py::test_replaced_through_globals",
            inventory.declared_owners,
        )
        self.assertNotIn(
            "playwright_tests/test_declarations.py::TestOrdinaryInheritedMark::test_mark_is_not_declaration",
            inventory.declared_owners,
        )

    def test_runtime_replacement_and_collection_hook_use_final_items(self):
        self.write(
            "playwright_tests/test_runtime.py",
            """
            def test_replaced_definition():
                raise AssertionError("the replaced source callable must not matter")


            def final_callable():
                return None


            globals()["test_replaced_definition"] = final_callable


            def test_removed_by_hook():
                return None


            def test_kept_by_hook():
                return None
            """,
        )
        self.write(
            "conftest.py",
            """
            def pytest_collection_modifyitems(items):
                items[:] = [item for item in items if item.name != "test_removed_by_hook"]
            """,
        )

        owners, item_count = self.collect()

        self.assertEqual(item_count, 2)
        self.assertEqual(
            owners,
            [
                "playwright_tests/test_runtime.py::test_kept_by_hook",
                "playwright_tests/test_runtime.py::test_replaced_definition",
            ],
        )

    def test_plugin_generation_and_all_parametrization_scopes_normalize(self):
        self.write(
            "playwright_tests/test_dynamic.py",
            """
            import pytest


            pytestmark = pytest.mark.parametrize("module_case", ["m1", "m2"])


            @pytest.mark.parametrize("class_case", ["c1", "c2"])
            class TestMatrix:
                @pytest.mark.parametrize("function_case", ["f1", "f2"])
                def test_scoped_params(self, module_case, class_case, function_case):
                    return None


            def generated_callable():
                return None
            """,
        )
        self.write(
            "conftest.py",
            """
            import pytest


            def pytest_pycollect_makeitem(collector, name, obj):
                if name == "generated_callable":
                    return pytest.Function.from_parent(
                        collector,
                        name="test_plugin_generated",
                        callobj=obj,
                    )
                return None
            """,
        )

        owners, item_count = self.collect()

        self.assertEqual(item_count, 9)
        self.assertEqual(
            owners,
            [
                "playwright_tests/test_dynamic.py::TestMatrix::test_scoped_params",
                "playwright_tests/test_dynamic.py::test_plugin_generated",
            ],
        )

    def test_collect_only_never_sets_up_browser_server_database_or_autouse_fixtures(self):
        self.write(
            "playwright_tests/conftest.py",
            """
            import pytest


            @pytest.fixture(scope="session")
            def browser():
                raise AssertionError("browser fixture started during collection")


            @pytest.fixture(scope="session")
            def django_server():
                raise AssertionError("server fixture started during collection")


            @pytest.fixture(scope="session")
            def django_db_setup():
                raise AssertionError("database fixture started during collection")


            @pytest.fixture(autouse=True)
            def autouse_probe():
                raise AssertionError("autouse fixture started during collection")
            """,
        )
        self.write(
            "playwright_tests/test_browserless_collection.py",
            """
            def test_owner(browser, django_server, django_db_setup):
                return None
            """,
        )

        owners, item_count = self.collect()

        self.assertEqual(item_count, 1)
        self.assertEqual(
            owners,
            ["playwright_tests/test_browserless_collection.py::test_owner"],
        )

    def test_duplicate_final_owner_identity_is_rejected(self):
        self.write(
            "playwright_tests/test_duplicate.py",
            """
            def test_owner():
                return None
            """,
        )
        self.write(
            "conftest.py",
            """
            import pytest


            def another_callable():
                return None


            def pytest_collection_modifyitems(items):
                original = items[0]
                duplicate = pytest.Function.from_parent(
                    original.parent,
                    name=original.name,
                    callobj=another_callable,
                )
                items.append(duplicate)
            """,
        )

        with self.assertRaisesRegex(InventoryError, "duplicate owner identity.*test_owner"):
            self.collect()

    def test_unsupported_custom_item_is_rejected_with_exact_id(self):
        self.write(
            "playwright_tests/test_custom.py",
            """
            custom_owner = object()
            """,
        )
        self.write(
            "conftest.py",
            """
            import pytest


            class CustomOwner(pytest.Item):
                def runtest(self):
                    return None


            def pytest_pycollect_makeitem(collector, name, obj):
                if name == "custom_owner":
                    return CustomOwner.from_parent(collector, name="test_custom_owner")
                return None
            """,
        )

        with self.assertRaisesRegex(
            InventoryError,
            "unsupported collected item `playwright_tests/test_custom.py::test_custom_owner`",
        ):
            self.collect()

    def test_source_collection_drift_reports_uncollected_module(self):
        self.write("playwright_tests/test_empty.py", "VALUE = 1\n")
        self.write(
            "playwright_tests/test_owner.py",
            """
            def test_owner():
                return None
            """,
        )

        with self.assertRaisesRegex(
            InventoryError,
            "source/collection drift: uncollected modules: playwright_tests/test_empty.py",
        ):
            self.collect()

    def test_owner_order_is_independent_of_argument_and_hook_order(self):
        self.write(
            "playwright_tests/test_zed.py",
            """
            def test_zed():
                return None
            """,
        )
        self.write(
            "playwright_tests/test_alpha.py",
            """
            def test_alpha():
                return None
            """,
        )
        self.write(
            "conftest.py",
            """
            def pytest_collection_modifyitems(items):
                items.reverse()
            """,
        )

        owners, _ = self.collect()

        self.assertEqual(
            owners,
            [
                "playwright_tests/test_alpha.py::test_alpha",
                "playwright_tests/test_zed.py::test_zed",
            ],
        )


class InventoryRatchetTests(SimpleTestCase):
    declared = "playwright_tests/test_browser.py::TestJourney::test_owner"
    non_browser = "playwright_tests/test_api.py::test_owner"

    def manifest(self, *, declared=None, non_browser=None):
        declared = [self.declared] if declared is None else declared
        non_browser = (
            {
                self.non_browser: {
                    "category": "api",
                    "reason": "Uses an HTTP client without a browser journey.",
                    "relocation": "events/tests/",
                }
            }
            if non_browser is None
            else non_browser
        )
        return {
            "schema_version": 1,
            "LEGACY_DECLARED_BROWSER": declared,
            "LEGACY_NON_BROWSER": non_browser,
        }

    @staticmethod
    def digest(owners):
        return hashlib.sha256("\n".join(sorted(owners)).encode()).hexdigest()

    def validate(
        self,
        collected,
        manifest=None,
        declared_ceiling=None,
        non_browser_ceiling=None,
        declared_owners=None,
        expected_declared_ceiling=None,
        expected_non_browser_ceiling=None,
    ):
        declared_ceiling = declared_ceiling or {self.declared}
        non_browser_ceiling = non_browser_ceiling or {self.non_browser}
        expected_declared_ceiling = expected_declared_ceiling or {self.declared}
        expected_non_browser_ceiling = expected_non_browser_ceiling or {self.non_browser}
        return validate_inventory(
            set(collected),
            manifest or self.manifest(),
            declared_ceiling=declared_ceiling,
            non_browser_ceiling=non_browser_ceiling,
            expected_ceiling_counts={
                "LEGACY_DECLARED_BROWSER": 1,
                "LEGACY_NON_BROWSER": 1,
            },
            expected_ceiling_digests={
                "LEGACY_DECLARED_BROWSER": self.digest(expected_declared_ceiling),
                "LEGACY_NON_BROWSER": self.digest(expected_non_browser_ceiling),
            },
            declared_owners=set(declared_owners or ()),
        )

    def test_exact_partition_passes(self):
        self.assertEqual(self.validate({self.declared, self.non_browser}), [])

    def test_new_node_inside_listed_file_is_not_covered_by_file(self):
        new_owner = "playwright_tests/test_browser.py::TestJourney::test_new_owner"
        errors = self.validate({self.declared, self.non_browser, new_owner})
        self.assertIn(f"new owner: `{new_owner}`", "\n".join(errors))
        self.assertIn("@browser_journey", "\n".join(errors))

    def test_declared_new_owner_grows_without_live_or_ceiling_edit(self):
        new_owner = "playwright_tests/test_browser.py::test_explicit_new_owner"
        self.assertEqual(
            self.validate(
                {self.declared, self.non_browser, new_owner},
                declared_owners={new_owner},
            ),
            [],
        )

    def test_migrated_owner_shrinks_live_only_and_keeps_immutable_ceiling(self):
        manifest = self.manifest(declared=[])
        self.assertEqual(
            self.validate(
                {self.declared, self.non_browser},
                manifest=manifest,
                declared_owners={self.declared},
            ),
            [],
        )

    def test_declared_owner_cannot_remain_in_live_manifest(self):
        joined = "\n".join(
            self.validate(
                {self.declared, self.non_browser},
                declared_owners={self.declared},
            )
        )
        self.assertIn(f"declared live owner: `{self.declared}`", joined)
        self.assertIn("Remove only its live entry", joined)

    def test_stale_and_replacement_ids_both_report_exact_next_actions(self):
        replacement = "playwright_tests/test_browser.py::TestJourney::test_replacement"
        errors = self.validate({replacement, self.non_browser})
        joined = "\n".join(errors)
        self.assertIn(f"new owner: `{replacement}`", joined)
        self.assertIn(f"stale live owner: `{self.declared}`", joined)
        self.assertIn("leave the immutable ceiling unchanged", joined)

    def test_retiring_live_id_shrinks_live_only_and_keeps_ceiling(self):
        manifest = self.manifest(declared=[])
        errors = self.validate(
            {self.non_browser},
            manifest=manifest,
            declared_ceiling={self.declared},
        )
        self.assertEqual(errors, [])

    def test_overlap_and_missing_review_fields_fail_exact_owner(self):
        manifest = self.manifest(
            declared=[self.declared, self.non_browser],
            non_browser={self.non_browser: {"category": "", "reason": "", "relocation": ""}},
        )
        joined = "\n".join(self.validate({self.declared, self.non_browser}, manifest=manifest))
        self.assertIn(f"overlap: `{self.non_browser}`", joined)
        self.assertIn(f"missing category: `{self.non_browser}`", joined)
        self.assertIn(f"missing reason: `{self.non_browser}`", joined)
        self.assertIn(f"missing relocation: `{self.non_browser}`", joined)

    def test_ceiling_growth_fails_with_exact_id_and_revert_action(self):
        growth = "playwright_tests/test_browser.py::test_illegal_ceiling_growth"
        joined = "\n".join(
            self.validate(
                {self.declared, self.non_browser},
                declared_ceiling={self.declared, growth},
            )
        )
        self.assertIn("ceiling growth: LEGACY_DECLARED_BROWSER", joined)
        self.assertIn(f"`{growth}`", joined)
        self.assertIn("Revert the ceiling change", joined)

    def test_same_size_ceiling_replacement_cannot_reuse_retired_debt(self):
        replacement = "playwright_tests/test_browser.py::test_replacement_debt"
        joined = "\n".join(
            self.validate(
                {self.non_browser, replacement},
                manifest=self.manifest(declared=[]),
                declared_ceiling={replacement},
                declared_owners={replacement},
                expected_declared_ceiling={self.declared},
            )
        )
        self.assertIn("immutable ceiling changed: LEGACY_DECLARED_BROWSER", joined)
        self.assertIn("remove every replacement", joined)


class CurrentRepositoryInventoryTests(SimpleTestCase):
    recap_api_owner = (
        "playwright_tests/test_event_recap.py::"
        "TestRecapThroughTheStaffApi::test_organiser_publishes_weekly_notes_from_a_script"
    )
    bookclub_api_owner = (
        "playwright_tests/test_book_club_summary_notifications_1374.py::"
        "TestAdminApi::test_admin_publish_observable_and_idempotent"
    )
    community_api_owners = frozenset({
        "playwright_tests/test_call_profiles_1404.py::"
        "TestCallProfileApiJourney::test_api_delete_protects_history_then_allows_hiding",
        "playwright_tests/test_sprint_progress_evidence_api.py::"
        "test_staff_operator_classifies_next_sprint_candidates",
        "playwright_tests/test_sprint_roster_activity_1202.py::"
        "test_staff_token_reads_roster_activity_api",
    })
    slack_live_owner = (
        "playwright_tests/test_slack_integration.py::test_post_slack_announcement_real"
    )
    content_http_owners = frozenset({
        "playwright_tests/test_legacy_recording_redirects_1381.py::"
        "test_unmapped_legacy_recording_is_a_genuine_404",
        "playwright_tests/test_search_indexing_policy_1379.py::"
        "test_trailing_slash_redirects_apply_noindex_only_to_private_targets",
    })
    event_api_owners = frozenset({
        "playwright_tests/test_event_calendar_lifecycle_1073.py::"
        "TestApiCalendarLifecycle::test_api_patch_cancel_enqueues_calendar_cancel",
        "playwright_tests/test_event_calendar_lifecycle_1073.py::"
        "TestApiCalendarLifecycle::test_api_patch_reschedule_enqueues_calendar_update",
        "playwright_tests/test_event_operator_workflow_1285.py::"
        "test_token_api_publish_is_scoped_and_idempotent",
        "playwright_tests/test_event_zoom_lifecycle_1074.py::"
        "TestApiZoomLifecycle::test_api_patch_cancel_deletes_zoom_meeting_and_clears_fields",
        "playwright_tests/test_event_zoom_lifecycle_1074.py::"
        "TestApiZoomLifecycle::test_api_patch_reschedule_patches_existing_zoom_meeting",
        "playwright_tests/test_event_zoom_lifecycle_1074.py::"
        "TestApiZoomLifecycle::test_api_series_rename_patches_auto_titled_zoom_occurrences",
    })
    event_calendar_http_owners = frozenset({
        "playwright_tests/test_event_detail_registration.py::"
        "TestPostRegistrationConfirmation::test_ics_download_returns_vcalendar",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestAnonymousHttpFetchOfFeed::test_feed_returns_200_and_includes_public_events",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestEditsPropagateAsUpdatesNotDuplicates::"
        "test_edited_event_appears_once_with_higher_sequence",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestEtagShortCircuit::test_if_none_match_returns_304_then_200_after_edit",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestExternalEventsAreMarkedInFeed::test_maven_event_summary_location_url",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestGatedAndDraftStayOutOfPublicFeed::test_only_open_published_event_appears",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestPerEventDownloadDropsMembersOnlyPrefix::"
        "test_gated_event_download_has_no_members_only_prefix",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestPerEventDownloadDropsMembersOnlyPrefix::"
        "test_gated_external_download_keeps_hosted_drops_members_only",
        "playwright_tests/test_events_calendar_feed.py::"
        "TestPerEventDownloadDropsMembersOnlyPrefix::test_open_event_download_is_plain_title",
    })
    studio_auth_http_owners = frozenset({
        "playwright_tests/test_custom_banner_upload_931.py::"
        "TestCustomBannerPanel::test_non_staff_cannot_upload",
        "playwright_tests/test_studio_event_duplicates.py::"
        "TestNonStaffBlocked::test_member_gets_403",
        "playwright_tests/test_studio_user_detail_tier_override.py::"
        "TestNonStaffCannotReachEndpoint::test_member_post_is_blocked",
        "playwright_tests/test_studio_user_merge.py::"
        "TestNonStaffBlocked::test_member_gets_403",
    })
    browser_lifecycle_owners = frozenset({
        "playwright_tests/test_browser_lifecycle_1418.py::"
        "test_cleanup_attempts_every_context_and_aggregates_all_errors",
        "playwright_tests/test_browser_lifecycle_1418.py::"
        "test_intentional_failure_probe_still_cleans_before_following_node",
        "playwright_tests/test_browser_lifecycle_1418.py::"
        "test_node_after_direct_contexts_starts_with_zero_resources",
        "playwright_tests/test_browser_lifecycle_1418.py::"
        "test_node_after_page_fixture_starts_with_zero_resources",
        "playwright_tests/test_browser_lifecycle_1418.py::"
        "test_static_node_does_not_require_browser",
    })
    migrated_owner = "playwright_tests/test_dev_smoke_sitemap.py::test_sitemap_xml_is_served"
    campaign_owner = (
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffReconcilesAmbiguousDelivery::"
        "test_duplicate_risk_confirmations_gate_retry_and_assume_sent"
    )
    issue_1528_owners = frozenset({
        "playwright_tests/test_member_plan_edit.py::"
        "TestMemberOpensOwnPlan::"
        "test_member_can_complete_checkpoint_but_has_no_delete_control",
        "playwright_tests/test_member_plan_edit.py::"
        "TestMemberOpensOwnPlan::"
        "test_member_deterministic_task_error_reverts_without_retry",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffEditsSummaryInline::"
        "test_deterministic_summary_error_does_not_retry",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffDeleteRetryPolicy::"
        "test_lost_success_then_not_found_does_not_restore_ghost",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffDeleteRetryPolicy::"
        "test_network_failure_retries_once_then_delete_succeeds",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffDeleteRetryPolicy::"
        "test_forbidden_delete_restores_immediately_without_retry",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffDeleteRetryPolicy::"
        "test_deleting_only_checkpoint_reveals_empty_week_hint",
        "playwright_tests/test_studio_plan_editor.py::"
        "TestStaffSeesRevertOnApiFailure::"
        "test_checkpoint_edit_deterministic_error_does_not_retry",
    })
    issue_1529_owners = frozenset({
        "playwright_tests/test_studio_confirm_guard_1529.py::"
        "TestStudioConfirmGuard::"
        "test_apostrophe_course_unenroll_cancel_then_accepts_without_script_injection",
        "playwright_tests/test_studio_confirm_guard_1529.py::"
        "TestStudioConfirmGuard::"
        "test_apostrophe_instructor_remove_cancel_then_accepts",
        "playwright_tests/test_studio_confirm_guard_1529.py::"
        "TestStudioConfirmGuard::"
        "test_revoke_access_keeps_dynamic_email_and_existing_post",
        "playwright_tests/test_studio_confirm_guard_1529.py::"
        "TestStudioConfirmGuard::"
        "test_email_reset_cancels_from_list_and_keyboard_then_accepts_from_edit",
    })
    issue_1530_owners = frozenset({
        "playwright_tests/test_account_email_preference_switches_1530.py::"
        "test_member_reads_toggles_and_persists_mouse_and_keyboard_changes",
        "playwright_tests/test_account_email_preference_switches_1530.py::"
        "test_failed_and_network_saves_leave_accessible_and_visual_state_unchanged",
    })
    issue_1531_owners = frozenset({
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_member_submits_a_single_choice_through_its_named_group",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_member_saves_labelled_other_details",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_required_question_error_names_the_blocked_control",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_onboarding_text_help_describes_its_labelled_control",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_member_saves_multiple_choices_from_one_named_group",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_staff_creates_questionnaire_with_labelled_fields_and_title_error",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_staff_authors_scale_question_with_named_min_and_max",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_staff_adds_labelled_custom_question_to_only_one_member",
        "playwright_tests/test_questionnaire_accessibility_1531.py::"
        "test_keyboard_user_selects_choice_and_submits",
    })
    issue_1506_owners = frozenset({
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffSeesNeedsAttentionAfterHardRejection::"
        "test_hard_ses_rejection_leaves_needs_attention_not_sending",
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffRetriesFailedRecipient::"
        "test_retry_failed_recipient_can_finish_the_campaign",
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffAssumesAmbiguousWithoutResend::"
        "test_assume_delivered_does_not_create_email_log",
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffSeesFrozenAudience::"
        "test_late_joiner_is_not_listed_on_recipients",
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffSeesSkippedSnapshotAsSent::"
        "test_unsubscribed_snapshot_completes_as_sent",
    })
    issue_1551_owners = frozenset({
        "playwright_tests/test_articles_blog.py::TestBlogBrowserSmoke::"
        "test_staff_edits_article_from_public_page_in_studio",
        "playwright_tests/test_studio_edit_button.py::"
        "TestStudioEditButtonOnEventDetail::test_main_member_has_no_operator_escape_hatch",
        "playwright_tests/test_studio_user_detail_layout_586.py::"
        "TestUserDetailLayout586::test_merge_accounts_lands_on_studio_merge_page",
        "playwright_tests/test_studio_user_detail_layout_586.py::"
        "TestUserDetailLayout586::test_unlinked_slack_id_stays_in_studio",
        "playwright_tests/test_content_comment_notifications.py::"
        "TestOperatorLinksThenNotifies::"
        "test_studio_linking_enables_future_notifications",
        "playwright_tests/test_video_player.py::"
        "TestScenario10StudioTimestampEditor::"
        "test_staff_adds_timestamps_to_recording_in_studio",
    })
    ses_1552_owners = frozenset({
        "playwright_tests/test_studio_ses_events_1552.py::"
        "test_keyboard_summary_card_opens_matching_global_queue",
        "playwright_tests/test_studio_ses_events_1552.py::"
        "test_campaign_without_linked_events_leads_to_recipient_diagnostics",
        "playwright_tests/test_studio_ses_events_1552.py::"
        "test_clear_event_filters_keeps_campaign_and_recovers_linked_event",
        "playwright_tests/test_studio_ses_events_1552.py::"
        "test_matched_identity_links_from_campaign_event_to_member",
    })
    issue_1515_owners = frozenset({
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_visitor_cannot_register_with_common_password",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_visitor_cannot_register_with_numeric_password",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_visitor_cannot_register_with_email_as_password",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_member_cannot_change_to_common_password",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_member_changes_to_strong_password_and_signs_in",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_reset_link_rejects_numeric_password",
        "playwright_tests/test_password_validators_1515.py::"
        "TestPasswordValidatorJourneys::"
        "test_reset_link_sets_strong_password_and_signs_in",
    })
    issue_1516_owners = frozenset({
        "playwright_tests/test_auth_throttle_1516.py::"
        "TestAuthThrottleJourneys::"
        "test_visitor_who_mistypes_password_is_asked_to_wait",
        "playwright_tests/test_auth_throttle_1516.py::"
        "TestAuthThrottleJourneys::"
        "test_throttled_login_does_not_block_first_newsletter_subscribe",
        "playwright_tests/test_auth_throttle_1516.py::"
        "TestAuthThrottleJourneys::"
        "test_visitor_who_hammers_account_creation_is_asked_to_wait",
        "playwright_tests/test_auth_throttle_1516.py::"
        "TestAuthThrottleJourneys::"
        "test_reset_request_spam_is_told_to_wait_without_fake_success",
    })
    issue_1565_owners = frozenset({
        "playwright_tests/test_maven_welcome_slack_journey_1565.py::"
        "test_enrollee_follows_the_welcome_email_into_slack",
        "playwright_tests/test_maven_welcome_slack_journey_1565.py::"
        "test_staff_preview_shows_the_ordered_steps_and_the_slack_link",
        "playwright_tests/test_maven_welcome_slack_journey_1565.py::"
        "test_support_reads_why_an_enrollee_never_reached_slack",
    })
    issue_1557_owners = frozenset({
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_staff_publishes_verifies_and_announces_a_recap",
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_studio_explains_every_recap_ready_blocker",
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_registrant_follows_the_notification_to_the_recap_page",
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_only_active_exact_registrants_receive_the_recap_notice",
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_rerunning_the_action_reports_already_sent_without_duplicates",
        "playwright_tests/test_event_recap.py::"
        "TestRecapReadyNotificationBrowserFlow::"
        "test_zero_recipient_send_is_clear_and_successful",
    })
    issue_1540_owners = frozenset({
        "playwright_tests/test_schedule_reconciliation_1540.py::"
        "TestScheduleReconciliationWorkerBanner::"
        "test_staff_sees_degraded_banner_and_worker_content",
        "playwright_tests/test_schedule_reconciliation_1540.py::"
        "TestScheduleReconciliationWorkerBanner::test_ok_state_has_no_degraded_banner",
        "playwright_tests/test_schedule_reconciliation_1540.py::"
        "TestScheduleReconciliationWorkerBanner::test_successful_apply_clears_existing_banner",
        "playwright_tests/test_schedule_reconciliation_1540.py::"
        "TestScheduleReconciliationWorkerBanner::test_non_staff_cannot_open_worker_diagnostics",
    })
    issue_1541_owners = frozenset({
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_users_spaced_search_survives_tier_chip",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_users_plus_ampersand_search_survives_slack_then_bounce",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_users_csv_export_preserves_encoded_search_and_tag",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_users_tag_clear_preserves_encoded_search_and_drops_page",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_crm_ampersand_search_survives_status_chip",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_crm_spaced_search_survives_pager",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_lifecycle_chips_preserve_encoded_search_on_users_and_crm",
        "playwright_tests/test_studio_listing_query_encoding_1541.py::"
        "test_filtered_lists_keep_anonymous_redirect_and_nonstaff_forbidden",
    })
    issue_1589_owners = frozenset({
        "playwright_tests/test_course_code_annotations_1589.py::"
        "TestCourseCodeAnnotationsReader::"
        "test_reader_attaches_highlights_and_ordered_notes_to_each_block",
        "playwright_tests/test_course_code_annotations_1589.py::"
        "TestCourseCodeAnnotationsReader::"
        "test_keyboard_copy_uses_only_raw_code_and_ordinary_fence_stays_plain",
        "playwright_tests/test_course_code_annotations_1589.py::"
        "TestCourseCodeAnnotationsReader::"
        "test_mobile_notes_fit_reader_while_long_code_scrolls_and_copies",
    })

    issue_1591_owners = frozenset({
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_copy_reviewer_sees_the_nameless_greeting",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_copy_reviewer_switches_back_to_the_named_recipient",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_operator_override_still_degrades_for_nameless_members",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_security_notice_greets_nameless_members_neutrally",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_maven_welcome_greets_nameless_enrollees_neutrally",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_sprint_partner_intro_keeps_partner_labels",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_nameless_member_password_reset_is_greeted_as_a_person",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_named_member_registration_email_keeps_their_name",
        "playwright_tests/test_email_greeting_no_name_1591.py::"
        "test_send_test_addresses_the_operator_by_their_own_name",
    })

    issue_1590_owners = frozenset({
        "playwright_tests/test_comment_dates_1590.py::"
        "test_us_locale_course_question_and_reply_use_day_first_semantic_dates",
        "playwright_tests/test_comment_dates_1590.py::"
        "test_timezone_boundary_changes_local_day_without_changing_format",
        "playwright_tests/test_comment_dates_1590.py::"
        "test_relative_thresholds_and_long_mobile_metadata_remain_readable",
    })

    issue_1593_owners = frozenset({
        "playwright_tests/test_maven_newsletter_opt_in_1593.py::"
        "test_enrollee_clicks_verify_and_is_both_verified_and_subscribed",
        "playwright_tests/test_maven_newsletter_opt_in_1593.py::"
        "test_enrollee_who_never_clicks_stays_off_the_newsletter",
        "playwright_tests/test_maven_newsletter_opt_in_1593.py::"
        "test_opting_in_then_leaving_takes_one_click_each_way",
        "playwright_tests/test_maven_newsletter_opt_in_1593.py::"
        "test_a_tampered_opt_in_link_fails_safely",
    })

    issue_1592_owners = frozenset({
        "playwright_tests/test_operator_comments_1592.py::"
        "test_operator_reply_is_escaped_and_idempotent_in_course_discussion",
    })

    issue_1539_owners = frozenset({
        "playwright_tests/test_studio_settings_observability.py::"
        "TestStudioSettingsObservability::"
        "test_staff_disables_logfire_and_is_still_told_to_restart",
        "playwright_tests/test_studio_settings_observability.py::"
        "TestStudioSettingsObservability::"
        "test_saving_site_settings_does_not_show_logfire_restart_message",
        "playwright_tests/test_studio_settings_observability.py::"
        "TestStudioSettingsObservability::"
        "test_non_staff_member_cannot_open_observability_settings",
    })

    issue_1542_owners = frozenset({
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_desktop_enrollments_list_each_student_and_action_once",
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_desktop_unenroll_has_no_hidden_duplicate_form",
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_desktop_access_lists_grant_and_purchase_once",
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_purchased_access_has_one_explanation_and_no_revoke",
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_empty_enrollments_keep_form_and_one_shared_empty_state",
        "playwright_tests/test_studio_course_access.py::"
        "TestResponsiveCourseRecordLists::"
        "test_non_staff_cannot_open_enrollment_or_access_lists",
    })

    issue_1544_owners = frozenset({
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestSharedPlanMarkdownModule::"
        "test_renders_supported_markdown_and_keeps_unsafe_values_inert",
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestMemberPlanMarkdownEdits::"
        "test_pre_sprint_action_matches_server_render_after_reload",
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestMemberPlanMarkdownEdits::"
        "test_client_rerender_does_not_execute_script_or_javascript_link",
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestMemberPlanMarkdownEdits::"
        "test_goal_markdown_is_visible_to_owner_and_teammate",
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestMemberPlanMarkdownEdits::"
        "test_checkpoint_save_prefers_server_description_html",
        "playwright_tests/test_plan_markdown_client_1544.py::"
        "TestStudioPlanMarkdownEdit::"
        "test_staff_checkpoint_markdown_matches_after_reload",
    })

    def test_reviewed_recap_owner_stays_in_browser_partition(self):
        manifest = load_live_manifest()

        self.assertIn(self.recap_api_owner, manifest["LEGACY_DECLARED_BROWSER"])
        self.assertNotIn(self.recap_api_owner, manifest["LEGACY_NON_BROWSER"])
        self.assertIn(self.recap_api_owner, LEGACY_DECLARED_BROWSER_CEILING)
        self.assertNotIn(self.recap_api_owner, LEGACY_NON_BROWSER_CEILING)
        self.assertNotIn(self.bookclub_api_owner, manifest["LEGACY_NON_BROWSER"])
        self.assertNotIn(self.bookclub_api_owner, manifest["LEGACY_DECLARED_BROWSER"])
        self.assertIn(self.bookclub_api_owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.community_api_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        self.assertNotIn(self.slack_live_owner, manifest["LEGACY_NON_BROWSER"])
        self.assertNotIn(self.slack_live_owner, manifest["LEGACY_DECLARED_BROWSER"])
        self.assertIn(self.slack_live_owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.content_http_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.event_api_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.event_calendar_http_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.studio_auth_http_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        for owner in self.browser_lifecycle_owners:
            self.assertNotIn(owner, manifest["LEGACY_NON_BROWSER"])
            self.assertNotIn(owner, manifest["LEGACY_DECLARED_BROWSER"])
            self.assertIn(owner, LEGACY_NON_BROWSER_CEILING)
        self.assertEqual(len(manifest["LEGACY_DECLARED_BROWSER"]), 2246)
        self.assertEqual(len(manifest["LEGACY_NON_BROWSER"]), 50)
        self.assertEqual(len(LEGACY_DECLARED_BROWSER_CEILING), 2258)
        self.assertEqual(len(LEGACY_NON_BROWSER_CEILING), 81)

    def test_current_collection_exactly_matches_live_partition_without_runtime_startup(self):
        lock = ROOT / ".tmp" / "playwright-session.lock"
        guard = None
        if not lock.exists():
            guard = PlaywrightWorktreeGuard(ROOT).acquire()
        lock_before = lock.read_bytes()
        database_paths = sorted(ROOT.glob("test_playwright_db*.sqlite3"))
        databases_before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in database_paths}

        try:
            inventory = collect_inventory(extra_env={"PLAYWRIGHT_DJANGO_PORT": "65534"})
            errors = validate_inventory(
                set(inventory.owners),
                load_live_manifest(),
                declared_owners=inventory.declared_owners,
            )
        finally:
            if guard is not None:
                guard.release()

        self.assertEqual(inventory.item_count, 2628)
        self.assertEqual(len(inventory.owners), 2403)
        self.assertEqual(
            inventory.declared_owners,
            {self.migrated_owner, self.campaign_owner}
            | self.issue_1528_owners
            | self.issue_1529_owners
            | self.issue_1530_owners
            | self.issue_1531_owners
            | self.ses_1552_owners
            | self.issue_1551_owners
            | self.issue_1557_owners
            | self.issue_1506_owners
            | self.issue_1515_owners
            | self.issue_1516_owners
            | self.issue_1565_owners
            | self.issue_1540_owners
            | self.issue_1541_owners
            | self.issue_1589_owners
            | self.issue_1591_owners
            | self.issue_1590_owners
            | self.issue_1593_owners
            | self.issue_1592_owners
            | self.issue_1539_owners
            | self.issue_1542_owners
            | self.issue_1544_owners,
        )
        self.assertNotIn(
            self.migrated_owner,
            load_live_manifest()["LEGACY_DECLARED_BROWSER"],
        )
        self.assertIn(self.migrated_owner, LEGACY_DECLARED_BROWSER_CEILING)
        self.assertNotIn(
            self.campaign_owner,
            load_live_manifest()["LEGACY_DECLARED_BROWSER"],
        )
        self.assertNotIn(self.campaign_owner, LEGACY_DECLARED_BROWSER_CEILING)
        for owner in self.issue_1506_owners:
            self.assertNotIn(owner, load_live_manifest()["LEGACY_DECLARED_BROWSER"])
            self.assertNotIn(owner, LEGACY_DECLARED_BROWSER_CEILING)
        for owner in self.issue_1515_owners:
            self.assertNotIn(owner, load_live_manifest()["LEGACY_DECLARED_BROWSER"])
            self.assertNotIn(owner, LEGACY_DECLARED_BROWSER_CEILING)
        for owner in self.issue_1516_owners:
            self.assertNotIn(owner, load_live_manifest()["LEGACY_DECLARED_BROWSER"])
            self.assertNotIn(owner, LEGACY_DECLARED_BROWSER_CEILING)
        for owner in self.issue_1565_owners:
            self.assertNotIn(owner, load_live_manifest()["LEGACY_DECLARED_BROWSER"])
            self.assertNotIn(owner, LEGACY_DECLARED_BROWSER_CEILING)
        for owner in (
            self.issue_1528_owners
            | self.issue_1529_owners
            | self.issue_1530_owners
            | self.issue_1531_owners
            | self.issue_1589_owners
            | self.issue_1591_owners
            | self.issue_1590_owners
            | self.issue_1593_owners
            | self.issue_1592_owners
            | self.issue_1542_owners
            | self.issue_1544_owners
        ):
            self.assertNotIn(owner, load_live_manifest()["LEGACY_DECLARED_BROWSER"])
            self.assertNotIn(owner, LEGACY_DECLARED_BROWSER_CEILING)
        self.assertEqual(errors, [])
        if guard is None:
            self.assertEqual(lock.read_bytes(), lock_before)
        else:
            self.assertFalse(lock.exists())
        self.assertEqual(
            {
                path: (path.stat().st_size, path.stat().st_mtime_ns)
                for path in sorted(ROOT.glob("test_playwright_db*.sqlite3"))
            },
            databases_before,
        )
        self.assertNotIn("PLAYWRIGHT_BASE_URL", os.environ)
