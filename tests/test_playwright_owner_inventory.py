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
    migrated_owner = "playwright_tests/test_dev_smoke_sitemap.py::test_sitemap_xml_is_served"
    campaign_owner = (
        "playwright_tests/test_studio_campaigns.py::"
        "TestStaffReconcilesAmbiguousDelivery::"
        "test_duplicate_risk_confirmations_gate_retry_and_assume_sent"
    )
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
        self.assertEqual(len(manifest["LEGACY_DECLARED_BROWSER"]), 2246)
        self.assertEqual(len(manifest["LEGACY_NON_BROWSER"]), 77)
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

        self.assertEqual(inventory.item_count, 2561)
        self.assertEqual(len(inventory.owners), 2341)
        self.assertEqual(
            inventory.declared_owners,
            {self.migrated_owner, self.campaign_owner}
            | self.ses_1552_owners
            | self.issue_1551_owners
            | self.issue_1557_owners,
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
