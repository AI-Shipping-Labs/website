# CI Test Duration & Low-Value Coverage Audit — 2026-05-07

This report investigates where CI wall-clock time goes today, identifies tests that overlap or
provide low signal, and proposes a small number of concrete optimizations. It does not delete or
modify tests. Issue: #466. Companion priors: `_docs/test-remediation-plan.md` (2026-03-21),
`_docs/audit-2026-04-20.md`.

## 1. Methodology

CI numbers come from GitHub Actions metadata for the last 10 successful `deploy-dev.yml` runs on
`main` (databaseIds 25426943899, 25438872153, 25441338111, 25453742886, 25455913932, 25459278280,
25462032273, 25465476630, 25482127199, 25483408572 — covering 2026-05-06 09:21Z through
2026-05-07 07:56Z). CI metadata fetched with:

```bash
gh run list --repo AI-Shipping-Labs/website --workflow deploy-dev.yml --branch main \
    --status success --limit 10 --json databaseId,createdAt,displayTitle,headSha
gh run view <id> --repo AI-Shipping-Labs/website --json jobs
```

Per-step duration is `completedAt - startedAt`, aggregated across 10 runs. Median and p90 reported
per step. CI runner is `ubuntu-latest` (4 vCPU, 16 GB RAM per GitHub's 2026 specs).

Local Django timings come from `uv run python -m pytest <app>/tests/ --durations=N` runs, executed
once per app group sequentially. Where pytest fixtures rely on isolated state, no `--keepdb` was
used. Hardware: AMD Ryzen 5 3600 (6 cores / 12 threads), 64 GB RAM, NVMe SSD, Linux 6.8. Branch:
`worktree-agent-a415bb44ccdee69d0` rebased on `main` at commit `d877021`.

Total Django wall-clock: a separate `uv run python manage.py test --parallel 4` run produced
`Ran 6073 tests in 305.169s` on the same hardware (parallelism = 4, matching CI). The 305 s figure
is the local equivalent of CI's `Run unit and integration tests` step (median 811 s on
`ubuntu-latest`, ~2.7x slower than the local box at the same parallelism).

Playwright timings: `uv run python -m pytest playwright_tests/ --durations=30 -q --no-header`,
executed in this worktree with the session-scoped browser fixture from
`playwright_tests/conftest.py`. Result: 584 passed, 1 skipped, 1 failed (the failure is
`test_studio_sidebar_layout.py::TestSidebarNavOrder::test_sidebar_nav_groups_and_links_stay_in_order`
and is unrelated to this audit) in 786.13 s = 13:06 with one Chromium instance reused across the
session.

Scripts used:

```bash
# CI aggregator (median + p90 per step across 10 runs)
python3 /tmp/aggregate_ci.py

# Per-test/class/module rollup from pytest --durations output
python3 /tmp/parse_durations.py
```

The two scripts and the captured `--durations` files were kept in `/tmp` and are reproducible from
the `gh run view` JSON and the pytest output.

Caveat: pytest's `--durations` only emits the top N rows per invocation; per-app rollups in the
tables below are unions of those tops, not a full enumeration. For tests outside the top-N of any
app run, durations are not visible — we treat anything not in the durations list as ≤ ~0.4 s.

## 2. CI wall-clock breakdown

Whole job times for the 10 sampled runs:

| Job | Median (s) | p90 (s) | n |
|---|---|---|---|
| `Unit & Integration Tests` | 834 | 847 | 10 |
| `Deploy to Dev` | 246 | 259 | 10 |

Per-step inside `Unit & Integration Tests`:

| Step | Median (s) | p90 (s) | % of job | Notes |
|---|---|---|---|---|
| Set up job | 1 | 2 | 0.1 | runner provisioning |
| Checkout code | 2 | 2 | 0.2 | `actions/checkout@v5` |
| Install uv | 2 | 3 | 0.2 | `astral-sh/setup-uv@v7` |
| Set up Python | 0 | 1 | 0.0 | cached by `setup-uv` |
| Install dependencies | 2 | 3 | 0.2 | `uv sync` (cache hits typical) |
| Collect static files | 4 | 6 | 0.5 | `manage.py collectstatic --noinput` |
| Run migrations | 6 | 7 | 0.7 | wasted — see proposal P5 |
| Run unit and integration tests | 811 | 821 | 97.2 | dominant, see Section 3 |
| Post-steps + teardown | 0–1 | 0–1 | 0.0 | cleanup |
| Job total (wall) | 834 | 847 | 100 | |

Per-step inside `Deploy to Dev`:

| Step | Median (s) | p90 (s) | % of job | Notes |
|---|---|---|---|---|
| Set up job | 1 | 1 | 0.4 | |
| Checkout code | 1 | 2 | 0.4 | |
| Prepare tags | 0 | 0 | 0.0 | shell |
| Set up Python | 0 | 1 | 0.0 | |
| Build Docker image | 21 | 23 | 8.5 | `docker build` |
| Log in to Amazon ECR | 3 | 4 | 1.2 | |
| Push Docker image to ECR | 17 | 18 | 6.9 | |
| Deploy to Dev | 196 | 210 | 79.5 | ECS task rollout, dominant |
| Verify deploy landed | 0 | 1 | 0.0 | one-shot HTTP probe |
| Job total (wall) | 246 | 259 | 100 | |

Two facts that drive everything in this report:

1. The unit-test step is 97% of the test-job wall clock. Anything that is not the test step is
   noise in absolute terms. Migrations being a separate `manage.py migrate` step is wasted (the
   test runner re-runs migrations against the test DB anyway), but it costs only ~6 s.
2. `Deploy to Dev` has its own p90 (~260 s) and runs only after `Unit & Integration Tests`
   completes. CI critical path is `test → deploy`, so cuts to the test step pay back at ~1:1
   minutes-saved-per-push.

## 3. Slowest Django modules, classes, individual tests

Local pytest `--durations` runs were grouped by app. Tables below merge the top-N rows from each
group and rank globally. "Phase" is pytest's classification: `setup` is `setUp`/`setUpTestData`,
`call` is the test method.

### 3a. Top 30 modules by sampled-duration sum

The table uses the union of pytest `--durations=N` rows captured across app-level runs, so `# tests`
means tests visible in the sampled top-N output, not the full module test count. The ranking is
still useful because it identifies where the measurable slow tail is concentrated.

| Rank | Module | # tests | Total time (s) | Mean per test (s) | Suspected cause |
|---|---|---|---|---|---|
| 1 | `studio/tests/test_user_list_pagination.py` | 7 | 84.19 | 12.03 | bulk creating 49–150 users in `setUpTestData` per pagination class |
| 2 | `content/tests/test_course_admin.py` | 23 | 18.37 | 0.80 | `ReorderModulesApiTest` and `ReorderUnitsApiTest` each issue 13/10 PUTs through staff client |
| 3 | `content/tests/test_dashboard.py` | 18 | 8.01 | 0.45 | dashboard fan-out: `ContinueLearningTest`, `WelcomeBannerTest`, `SlackJoinPromptTest` each render the full dashboard |
| 4 | `studio/tests/test_notifications.py` | 8 | 7.21 | 0.90 | notification log and notification-create tests build batch data and render staff pages |
| 5 | `studio/tests/test_campaigns.py` | 7 | 6.41 | 0.92 | recipient-count rendering touches user/tag tables |
| 6 | `studio/tests/test_enrollments.py` | 8 | 6.25 | 0.78 | course-scoped enrollment list per status filter |
| 7 | `content/tests/test_courses.py` | 6 | 5.82 | 0.97 | first-test warm-up plus course/module/unit setup chains |
| 8 | `studio/tests/test_dashboard_sprints_tile.py` | 2 | 5.68 | 2.84 | query-count test intentionally seeds many sprints + plans |
| 9 | `accounts/tests/test_email_auth.py` | 9 | 5.67 | 0.63 | each auth mutation hashes a real password via PBKDF2 |
| 10 | `email_app/tests/test_newsletter_subscriber_migration.py` | 2 | 5.00 | 2.50 | applies a real migration in a clean DB to assert table state |
| 11 | `integrations/tests/test_github_sync.py` | 8 | 4.59 | 0.57 | content sync round-trips create/update/delete several content types |
| 12 | `studio/tests/test_access.py` | 1 | 3.85 | 3.85 | first-test warm-up plus staff/non-staff access matrix setup |
| 13 | `integrations/tests/test_workshop_sync.py` | 7 | 3.80 | 0.54 | workshop sync idempotency and stale-cleanup round-trips |
| 14 | `content/tests/test_access_registered.py` | 1 | 3.61 | 3.61 | first-test warm-up artifact |
| 15 | `integrations/tests/test_announcement_banner.py` | 1 | 3.61 | 3.61 | first-test warm-up artifact |
| 16 | `accounts/tests/test_account.py` | 1 | 3.55 | 3.55 | first-test warm-up artifact |
| 17 | `events/tests/test_calendar_invite.py` | 1 | 3.54 | 3.54 | first-test warm-up artifact |
| 18 | `content/tests/test_course_units.py` | 6 | 3.23 | 0.54 | course-unit API and detail-view access checks |
| 19 | `integrations/tests/test_course_rename_orphan.py` | 4 | 3.22 | 0.81 | course sync rename/orphan rescue round-trips |
| 20 | `plans/tests/test_views_cohort_board_progress.py` | 4 | 2.97 | 0.74 | cohort board progress rows seed multiple plans/users |
| 21 | `content/tests/test_cohorts.py` | 6 | 2.87 | 0.48 | cohort display and enrollment setup |
| 22 | `accounts/tests/test_tier_override.py` | 5 | 2.80 | 0.56 | tier override paths render dashboard/account/studio views |
| 23 | `content/tests/test_course_purchase.py` | 5 | 2.72 | 0.54 | Stripe-product and purchase-button setup |
| 24 | `studio/tests/test_utm_analytics.py` | 2 | 2.64 | 1.32 | UTM detail conversion rows and MRR aggregation |
| 25 | `studio/tests/test_user_list_name_display.py` | 3 | 2.56 | 0.85 | user-list row-dict and search setup |
| 26 | `notifications/tests/test_service.py` | 4 | 2.47 | 0.62 | notification fan-out across users and content levels |
| 27 | `studio/tests/test_course_access.py` | 3 | 2.33 | 0.78 | course-access list and permission checks |
| 28 | `studio/tests/test_subscribers.py` | 2 | 2.01 | 1.00 | user-list chip/export setup |
| 29 | `notifications/tests/test_api.py` | 3 | 1.88 | 0.63 | notification API scope checks |
| 30 | `plans/tests/test_visibility_queryset.py` | 2 | 1.87 | 0.94 | first-test warm-up plus visibility queryset fixtures |

### 3b. Top 15 slowest classes (sum of sampled durations)

| Rank | Class | Module | # tests sampled | Total (s) | Suspected cause |
|---|---|---|---|---|---|
| 1 | `StudioUserListPagerOneFiftyTest` | `studio/tests/test_user_list_pagination.py` | 1 | 28.17 | seeds 149 users + staff in `setUpTestData` |
| 2 | `StudioUserExportUnpaginatedTest` | same | 1 | 13.75 | seeds large user set for export round-trip |
| 3 | `StudioUserListPaginationTest` | same | 1 | 13.69 | seeds enough users to span page boundaries |
| 4 | `ReorderModulesApiTest` | `content/tests/test_course_admin.py` | 13 | 10.44 | each test logs in, posts JSON, asserts persistence |
| 5 | `StudioUserListPagerExactlyFiftyTest` | `studio/tests/test_user_list_pagination.py` | 1 | 9.44 | 50-user seed |
| 6 | `StudioUserListPagerFiftyOneTest` | same | 1 | 9.30 | 51-user seed (page boundary) |
| 7 | `StudioUserListPagerFortyNineTest` | same | 1 | 8.91 | 49-user seed |
| 8 | `ReorderUnitsApiTest` | `content/tests/test_course_admin.py` | 10 | 7.93 | mirrors ReorderModulesApiTest |
| 9 | `CourseScopedEnrollmentsListTest` | `studio/tests/test_enrollments.py` | 8 | 6.25 | per-status assertions on shared enrollment fixtures |
| 10 | `NewsletterSubscriberRemovalMigrationTest` | `email_app/tests/test_newsletter_subscriber_migration.py` | 2 | 5.00 | runs `MigrationExecutor` in-test |
| 11 | `StudioDashboardSprintsTileQueryCountTest` | `studio/tests/test_dashboard_sprints_tile.py` | 1 | 4.33 | seeds many sprints/plans then asserts `assertNumQueries` |
| 12 | `StudioNotificationLogTest` | `studio/tests/test_notifications.py` | 4 | 4.13 | 20+ notification batches |
| 13 | `StudioAccessControlTest` | `studio/tests/test_access.py` | 1 | 3.85 | `setUpTestData` builds full staff/non-staff user matrix |
| 14 | `AnnouncementBannerModelTest` | `integrations/tests/test_announcement_banner.py` | 1 | 3.61 | first-class fixture cost (sampled in `setup`) |
| 15 | `LevelRegisteredConstantsTest` | `content/tests/test_access_registered.py` | 1 | 3.61 | first-class fixture cost (sampled in `setup`) |

Note on rows 14–15: these "3.6 s setup" entries reflect first-class warm-up (test DB connection +
migrations applied to a fresh per-process DB). They show up at the top because pytest's `setup`
phase for the FIRST test of a fresh worker includes the cost of preparing the DB. The cost is
amortized; it is not a real bottleneck per class. The same effect explains the 3.5 s rows in
`accounts/tests/test_account.py`, `events/tests/test_calendar_invite.py`, and
`content/tests/test_courses.py`.

### 3c. Top 30 slowest individual tests (call + setup)

Verdict column applies the rules from `_docs/testing-guidelines.md`:

- keep — high-value test, optimize in place
- rewrite — same coverage, faster shape (e.g., reduce fixture size, use `setUpTestData`)
- merge — overlaps with another test, consolidate
- delete-candidate — covered by another layer or low-signal

| Rank | Test | Time (s) | What it exercises | Verdict |
|---|---|---|---|---|
| 1 | `studio/tests/test_user_list_pagination.py::StudioUserListPagerOneFiftyTest::test_last_page_disables_next_and_last` | 28.17 | 150-user pager, "last page disables next" link | rewrite (reduce N) |
| 2 | `studio/tests/test_user_list_pagination.py::StudioUserExportUnpaginatedTest::test_export_ignores_page_param` | 13.75 | CSV export ignores `page=` querystring | rewrite (reduce N) |
| 3 | `studio/tests/test_user_list_pagination.py::StudioUserListPaginationTest::test_chip_links_do_not_carry_page_param` | 13.69 | filter chips drop `page=` param | rewrite (reduce N) |
| 4 | `studio/tests/test_user_list_pagination.py::StudioUserListPagerExactlyFiftyTest::test_fifty_rows_fit_in_one_page` | 9.44 | exactly-50 boundary case | rewrite (reduce N) |
| 5 | `studio/tests/test_user_list_pagination.py::StudioUserListPagerFiftyOneTest::test_fifty_one_rows_split_into_two_pages` | 9.30 | 50→51 page boundary | rewrite (reduce N) |
| 6 | `studio/tests/test_user_list_pagination.py::StudioUserListPagerFortyNineTest::test_forty_nine_rows_fit_in_one_page` | 8.91 | exactly-49 boundary case | rewrite (reduce N) |
| 7 | `studio/tests/test_dashboard_sprints_tile.py::StudioDashboardSprintsTileQueryCountTest::test_dashboard_query_count_does_not_scale_with_sprint_or_plan_count` | 4.33 | `assertNumQueries`-style scaling guard | keep (intentional, value comes from N) |
| 8 | `studio/tests/test_access.py::StudioAccessControlTest::test_anonymous_articles_redirects` | 3.85 | first-class warm-up (see note above) | keep (warm-up artifact) |
| 9 | `integrations/tests/test_announcement_banner.py::AnnouncementBannerModelTest::test_get_singleton_creates_first_row` | 3.61 | first-class warm-up | keep (warm-up artifact) |
| 10 | `content/tests/test_access_registered.py::LevelRegisteredConstantsTest::test_level_registered_between_open_and_basic` | 3.61 | first-class warm-up | keep (warm-up artifact) |
| 11 | `accounts/tests/test_account.py::AccountPageAccessTest::test_logged_in_returns_200` | 3.55 | first-class warm-up | keep (warm-up artifact) |
| 12 | `events/tests/test_calendar_invite.py::GenerateIcsTest::test_generate_ics_cancel_method` | 3.54 | first-class warm-up | keep (warm-up artifact) |
| 13 | `content/tests/test_courses.py::CourseModelTest::test_create_course_with_required_fields` | 3.38 | first-class warm-up | keep (warm-up artifact) |
| 14 | `email_app/tests/test_newsletter_subscriber_migration.py::NewsletterSubscriberRemovalMigrationTest::test_newsletter_subscriber_table_is_removed` | 2.96 | runs MigrationExecutor against test DB | keep (one-shot migration validation) |
| 15 | `email_app/tests/test_newsletter_subscriber_migration.py::NewsletterSubscriberRemovalMigrationTest::test_legacy_subscribers_map_to_user_newsletter_state` | 2.04 | same migration, different invariant | keep |
| 16 | `studio/tests/test_utm_analytics.py::UtmLinkDetailViewTest::test_conversion_rows_for_attributed_users` | 1.88 | renders UTM detail with conversions | keep |
| 17 | `accounts/tests/test_import_users.py::ImportUsersServiceTest::test_large_import_queues_throttled_schedules_without_sending_ses` | 1.56 | batched user import + scheduling | keep (test name encodes scaling intent) |
| 18 | `studio/tests/test_notifications.py::StudioNotificationLogTest::test_notification_log_shows_deduplicated_batches` | 1.37 | renders log with multiple batches | keep |
| 19 | `studio/tests/test_dashboard_sprints_tile.py::StudioDashboardSprintsTileCountsTest::test_total_plans_counts_across_all_sprints` | 1.35 | counts across sprints | keep |
| 20 | `studio/tests/test_campaigns.py::StudioCampaignCreateFormTest::test_create_form_shows_recipient_count_for_default_audience` | 1.21 | recipient count from user/tag tables | keep |
| 21 | `plans/tests/test_visibility_queryset.py::VisibleOnCohortBoardTest::test_visible_on_cohort_board_empty_for_anonymous` | 1.12 | first-class warm-up | keep |
| 22 | `studio/tests/test_subscribers.py::StudioUserListTest::test_chip_links_carry_search_value` | 1.09 | first-class warm-up | keep |
| 23 | `studio/tests/test_notifications.py::StudioNotificationLogTest::test_non_staff_redirected_to_login` | 1.08 | access-control redirect | keep |
| 24 | `integrations/tests/test_course_rename_orphan.py::CourseRenameOrphanSyncTest::test_pre_existing_draft_with_same_content_id_is_resurrected` | 1.03 | sync round-trip | keep |
| 25 | `studio/tests/test_campaigns.py::StudioCampaignDetailPreviewTest::test_detail_send_button_shows_recipient_count` | 1.02 | recipient count | keep |
| 26 | `studio/tests/test_user_tags.py::StudioUserListTagFilterTest::test_active_tag_chip_renders_with_clear_link` | 0.93 | filter chip rendering | keep |
| 27 | `studio/tests/test_user_list_pagination.py::StudioUserListPagerHiddenTest::test_pager_partial_not_rendered_when_single_page` | 0.93 | pager hidden state | keep |
| 28 | `plans/tests/test_display_name.py::DisplayNameHelperTest::test_display_name_email_local_part_fallback` | 0.93 | display-name helper | keep |
| 29 | `content/tests/test_course_units.py::ApiCourseUnitCompleteTest::test_unauthorized_user_gets_403` | 0.93 | API auth | keep |
| 30 | `studio/tests/test_subscribers.py::StudioUserExportTest::test_export_date_joined_isoformat` | 0.92 | CSV export iso date | keep |

The picture is consistent: the top-six rows are all in one file (`test_user_list_pagination.py`),
all in `setUpTestData` (creating up to 150 users per class), and together account for ≈ 83 s of
the sampled durations. Everything else in the top-30 is below 5 s and most are first-class
warm-up artifacts that are not real bottlenecks.

## 4. Slowest Playwright tests

Top 20 from `--durations=30` (with the session-scoped browser fixture from
`playwright_tests/conftest.py` already applied):

| Rank | Test | Time (s) | User flow | Verdict |
|---|---|---|---|---|
| 1 | `test_studio_users_name_layout.py::TestStudioUsersNameLayout::test_pagination_preserves_filter_and_shows_named_rows_on_page_two` | 12.25 | seeds 60 paid users, opens `/studio/users/?filter=paid&page=2`, asserts on row count and pager links | rewrite (cut seed N) |
| 2 | `test_studio_users_name_layout.py::TestStudioUsersNameLayout::test_at_least_eighteen_rows_visible_at_1280x900` | 6.44 | seeds 30 paid users, asserts row visibility at desktop viewport | rewrite (cut seed N) |
| 3 | `test_account_page.py::TestScenarioChangePasswordSuccess::test_new_password_works_after_change` | 6.38 | logs in, opens account, changes password, opens new context, logs in with new password | keep (multi-step real flow) |
| 4 | `test_course_admin.py::TestScenario3StaffBuildsModulesAndUnits::test_staff_adds_modules_and_units_to_course` | 5.52 | staff opens admin, creates modules and units via the admin form | keep (admin UI, JS-heavy form) |
| 5 | `test_mobile_resources_accordion_matrix.py::TestMobileResourcesAccordionAcrossPages::test_mobile_resources_accordion_present_on_each_url` | 4.61 | iterates several public URLs at mobile viewport, asserts accordion DOM | keep (matrix is the value) |
| 6 | `test_membership_tiers.py::TestScenario1AnonymousBrowsesFreeSubscribe::test_pricing_page_loads_without_login` | 4.51 | first-test-of-class warm-up; the `call` portion is small | keep (warm-up artifact) |
| 7 | `test_github_content_sync.py::TestScenario14SyncDashboardAutoRefresh::test_row_flips_from_running_to_success_without_reload` | 4.21 | sync dashboard polling — JS auto-refresh assertion (Playwright-only) | keep |
| 8 | `test_github_content_sync.py::TestScenario14SyncDashboardAutoRefresh::test_polling_stops_once_nothing_is_running` | 4.17 | polling-stops invariant | keep |
| 9 | `test_studio_panel.py::TestScenario6StaffCreatesCourse::test_course_create_url_removed_and_course_with_modules_visible` | 4.13 | staff-only end-to-end course creation | keep |
| 10 | `test_studio_mobile_lists.py::test_studio_core_lists_are_usable_at_phone_width` | 3.96 | several Studio lists at phone viewport | keep (responsive) |
| 11 | `test_testimonials_layout.py::test_homepage_testimonials_desktop_and_mobile_screenshots` | 3.95 | screenshot diff | keep (visual regression) |
| 12 | `test_github_content_sync.py::TestScenario13SyncQueuedButtonConfirmation::test_rapid_clicks_do_not_stack_resets` | 3.95 | rapid-click race on Sync Now button | keep (race-condition guard, JS-only) |
| 13 | `test_account_page.py::TestScenarioChangePasswordSuccess::test_change_password_success_message_and_fields_cleared` | 3.84 | success message + form clearing — JS reset | keep |
| 14 | `test_testimonials_layout.py::test_course_testimonials_shared_layout_and_source_link` | 3.76 | shared layout + source-link | keep |
| 15 | `test_env_mismatch_banner.py::test_db_override_clears_banner_on_next_page_load` | 3.66 | banner cache + cookie behaviour | keep |
| 16 | `test_account_profile.py::TestScenarioMemberUpdatesNameAndPassword::test_name_and_password_in_same_session_on_account_page` | 3.66 | name + password update in same session | keep |
| 17 | `test_env_mismatch_banner.py::test_alias_set_via_db_suppresses_banner` | 3.66 | banner suppression | keep |
| 18 | `test_course_catalog.py::TestScenario4MainMemberPaidCourseProgress::test_main_member_takes_paid_course_full_flow` | 3.19 | full paid-course flow, browser-required for progress markers | keep |
| 19 | `test_access_control.py::TestScenario449UnverifiedSignupFreeArticle::test_new_signup_gets_verify_email_gate_for_free_article` | 3.07 | signup → email-verify gate (Playwright is the canonical access-matrix layer per testing-guidelines.md Rule 11) | keep |
| 20 | `test_account_page.py::TestScenarioChangePasswordError::test_old_password_still_works_after_failed_change` | 2.98 | old-password fallback after failed change | keep |

`page.goto / page.click / expect` rough counts on the slowest tests (proxy for "is this doing
real work or just waiting"):

| Test | `page.goto` | `page.click` | `expect`/`assert` |
|---|---|---|---|
| `test_pagination_preserves_filter_and_shows_named_rows_on_page_two` | 1 | 1 | 5 |
| `test_at_least_eighteen_rows_visible_at_1280x900` | 1 | 0 | 1 |
| `test_new_password_works_after_change` | 2+ | 2+ | 3+ |
| `test_staff_adds_modules_and_units_to_course` | 1 | many | many |

Tests that exceed 30 s individually: zero. The previous (pre-#342) hardcoded `wait_for_timeout`
problem from the 2026-03 plan is essentially gone — only one `page.wait_for_timeout(...)` call
remains, in `playwright_tests/test_studio_long_form_ergonomics.py:95`, for 100 ms (acceptable as
a stabilization beat). 27 `wait_until="networkidle"` calls remain in three files
(`test_studio_plan_editor.py`, `test_workshop_comments.py`, `test_testimonials_layout.py`); each
of those will idle for ≥ 500 ms by definition.

## 5. Coverage overlap candidates

Cross-referenced manually by reading both layers' tests for the same user-visible behaviour. The
canonical division (per `_docs/testing-guidelines.md` Rule 11 and the worked example under issue
#261/#254) is: server-rendered HTML and access-control matrix unit tests in Django; real user
flows, JS interactions, and the cross-content-type access matrix in Playwright.

The codebase mostly follows that division now. The candidates below are the residual cases.

| User behaviour | Django test | Playwright test | Recommendation | Reason |
|---|---|---|---|---|
| Articles tag filtering on `/blog` | `content/tests/test_blog.py::BlogListTagFilteringTest` (7 tests, server-rendered HTML, asserts on context and links) | `playwright_tests/test_articles_blog.py::TestScenario2FilterByTag::test_tag_filtering` (1 test, opens browser, clicks chip) | keep both — they cover different layers per Rule 11 (server-render vs click) | not actually overlapping after re-read |
| Article publish/unpublish via admin | `content/tests/test_blog.py::ArticleAdminTest::test_admin_publish_action` etc. | `playwright_tests/test_articles_blog.py::TestScenario7AdminPublishesDraft` and `Scenario8AdminUnpublishesArticle` | keep Django + collapse PW into one round-trip | Two PW scenarios that each replay the action and an assertion are cheap to cut to one — they don't depend on JS, the admin action is a regular form POST. Estimated PW save: ~1.5 s × 2 = 3 s, low risk |
| Article create via admin | `content/tests/test_blog.py::ArticleAdminTest::test_admin_create_article` (call=0.45 s, asserts row created) | `playwright_tests/test_articles_blog.py::TestScenario12AdminCreatesArticle::test_admin_creates_article_via_admin` | keep Django, drop PW | Server-rendered admin form, no JS branch — the Django version already asserts creation. PW is duplicate. ~1–1.5 s saved |
| Empty-blog / empty-state messaging on `/blog` | `content/tests/test_blog.py::BlogListEmptyStateTest` (server-rendered) | `playwright_tests/test_articles_blog.py::TestScenario9EmptyBlog::test_empty_blog_shows_friendly_message` | keep Django, drop PW | Pure server-render; PW just `page.goto` + `assertContains`-equivalent. ~1 s saved |
| Filter-by-nonexistent-tag empty state | `content/tests/test_blog.py::BlogListTagFilteringTest::test_filter_by_nonexistent_tag` | `playwright_tests/test_articles_blog.py::TestScenario10FilterByTagNoMatches::test_no_matching_tag_shows_message` | keep Django, drop PW | Same as above |
| Banner without link (server-rendered) | `integrations/tests/test_announcement_banner.py::AnnouncementBannerContextProcessorTest` covers context | `playwright_tests/test_announcement_banner.py::TestScenario7BannerWithoutLink::test_no_link_means_no_anchor_and_no_label` | strengthen Django (one `assertNotContains('<a class="announcement-link"')` test would replicate this), then drop PW | The PW test does `page.goto` + DOM assertion of "no anchor, no label" — fully server-rendered. ~1 s saved |
| Dashboard "Continue learning" with completed course excluded | `content/tests/test_dashboard.py::ContinueLearningTest::test_fully_completed_course_not_shown` and `::test_fully_completed_course_stays_filtered_from_in_progress` | `playwright_tests/test_dashboard.py::TestScenario10CompletedCourseNotInContinueLearning::test_completed_course_excluded_in_progress_shown` | keep Django (exhaustive), drop PW (duplicate) | Server-rendered dashboard, Django version covers all cases at ~0.43 s each. PW pays browser cost for the same assertion. ~1.5 s saved. Also called out in 2026-03 remediation plan, Step 4 |
| Article-create / banner-toggle / filter-empty-state class | several Django tests | Playwright equivalents listed above | systematic: any PW test whose body is `page.goto(...)` + DOM-text-assertion with no clicks, no JS, no auth-state change, no context-switch is a candidate for replacement | net savings 5–8 s across the listed candidates |
| `playwright_tests/test_database_guard.py` (entire file, 2 tests, never opens a browser) | none | `test_playwright_database_is_pytest_scoped`, `test_playwright_server_starts_on_pytest_database` | move to `tests/test_database_guard.py` (regular Django tests) | Per Rule 10: a test that never opens a browser does not belong in `playwright_tests/`. Zero direct savings (the tests still run) but cleans up the "real" Playwright wall by removing two warm-ups |

Total measurable savings from "drop PW that duplicates Django (server-rendered only)": ~5–8 s on
each Playwright run, plus reducing test-count noise. These are individually small; bundled they
are still smaller than the pagination fix in §3 by an order of magnitude. Worth doing for clarity,
not for raw seconds.

## 6. Low-value test candidates

Cross-referenced against `_docs/test-remediation-plan.md` (2026-03-21). For each category from
that plan, this audit checked whether the listed items are still in the tree.

| Test | File:line | Category | Already in remediation plan? | Recommendation |
|---|---|---|---|---|
| `test_cover_image_url_default_empty` | `content/tests/test_blog.py:39` | Django ORM round-trip (default value) | not specifically listed but matches Step 1d category | delete (one-line removal); 1 test |
| `test_admin_user_list_displays_columns` | (not present) | Step 1a admin attribute | listed | already removed |
| `CourseAdminConfigTest`, `ModuleAdminConfigTest`, `UnitAdminConfigTest`, `UnitInlineConfigTest`, `ModuleInlineConfigTest` (all in `content/tests/test_course_admin.py:28-195`) | (not present at those line ranges) | Step 1a admin attribute | listed | already removed |
| `accounts/tests/test_admin.py`, `jobs/tests/test_admin.py` (entire files) | (files not present) | Step 1b smoke | listed | already removed |
| `CancelModalJavaScriptTest` and `Theme*ScriptTest`/`ThemeCSSVariablesTest` etc. | (replaced) | Step 1c JS-string-matching | listed | already removed; remaining `accounts/tests/test_theme.py` covers API behaviour, not JS strings |
| `accounts/tests/test_models.py:83-200` ORM defaults block | (not present at those line ranges) | Step 1d Django ORM round-trip | listed | already removed |
| `community/tests/test_models.py::test_cascade_delete_user`, `content/tests/test_courses.py::test_cascade_delete`, `voting/tests/test_models.py::test_cascade_delete_with_poll`, etc. | (not present) | Step 1d CASCADE tests | listed | already removed |
| `content/tests/test_urls.py` (entire file, 13 tests) | (file not present) | Step 1e URL resolution | listed | already removed |
| `voting/tests/test_models.py::test_poll_type_level_map` | (not present) | Step 1f constants | listed | already removed |
| Marketing-copy assertions in `content/tests/test_views.py:48-75` | partly — `test_views.py:56-87` still contains `assertIn('Test Article', content)`, `assertIn('AI Shipping Labs', content)`, `assertIn('/about', content)`, `assertIn('/blog', content)` etc. | Step 1g marketing copy / Rule 2 substring matching | partly | strengthen — replace `assertIn` on full-page string with `assertContains(response, '...', html=True)` or scoped locator. Keep the tests, fix the assertions. ~10 assertions across `content/tests/test_views.py` |
| `accounts/tests/test_import_users.py:519` `self.assertIn("source", model_admin.list_filter)` | `accounts/tests/test_import_users.py:519` | Rule 3 — admin attribute test | not listed in plan | delete (one assertion) |
| `tests/test_security_settings.py::test_redirect_and_aggressive_hsts_options_stay_disabled` (and the two siblings in same class) | `tests/test_security_settings.py:7-30` | Static config / settings | not listed | review — these guard a security policy decision (cookies + HSTS off in DEBUG, on in production); they are config-as-test but they DO catch a real-world regression class. Keep |
| `tests/test_staticfiles.py::DebugStaticFilesServingTest::test_admin_prepopulate_static_files_are_served_by_staticfiles_finders` | `tests/test_staticfiles.py` | Static config | not listed | keep (catches the one historic regression of admin static files not landing in the dev finder) |

Score: of the items the 2026-03 plan flagged as "delete" (≈ 150 tests across 1a–1g), almost all
are gone. The residual low-value items are isolated and small. The remediation plan worked.

The remaining drag on local test-time is structural, not low-value tests. Specifically:

- `setUp` / `setUpTestData` discipline is now the dominant lever (Step 6a of the 2026-03 plan).
- Bulk-fixture tests (Section 3a row 1) dominate the absolute durations.
- The Django framework warm-up cost per fresh worker (≈ 3–4 s) is real but irreducible without
  `--keepdb` or migration squashing (Step 6h, deferred in the 2026-03 plan and still deferred).

## 7. Optimization proposals

Each row is sourced from a specific table or measurement above. Risk: low / medium / high with a
one-sentence justification.

| Proposal | Estimated savings | Risk | Effort | Follow-up issue |
|---|---|---|---|---|
| P1. Reduce seed sizes in `studio/tests/test_user_list_pagination.py` and `playwright_tests/test_studio_users_name_layout.py` to the minimum needed to assert pager arithmetic, push pager constants down | Django: ~70 s (Section 3c rows 1–6 sum to 83 s of `setUpTestData`; cutting from 49–150 to ~5 users via a smaller `paginate_by` for the test or via a parameterized helper saves ~85% of that). Playwright: ~12 s (Section 4 rows 1–2 sum to 18.7 s, ~65% reducible). Total CI savings: ~80–82 s of unit-test wall, ~12 s of Playwright wall. | low | medium | [#467](https://github.com/AI-Shipping-Labs/website/issues/467) |
| P2. Convert remaining `setUp` to `setUpTestData` for read-only fixtures across the high-impact `content/tests/` files (Step 6a of remediation plan, partly complete) | 30–50% of the remaining content-app non-warm-up time according to Step 6i of the 2026-03 plan; concrete items: `test_courses.py` (12 setUp), `test_seo.py` (12 setUp), `test_tags.py` (12 setUp), `test_access_control.py` (12 setUp). Each setUp → setUpTestData saves a write per test method. Estimate 30–60 s of unit-test wall in aggregate, hard to source more precisely without an instrumentation pass. Treat as 30 s lower bound. | low | medium | [#468](https://github.com/AI-Shipping-Labs/website/issues/468) |
| P3. Replace the seven Playwright tests in §5 that just do `page.goto` + DOM-text-assert with Django `assertContains` (`html=True`) tests | 5–8 s of Playwright wall (per §5 cumulative). Plus removes seven warm-up costs from the PW session. Keep PW for the user flows that genuinely need a browser. | low | small | [#469](https://github.com/AI-Shipping-Labs/website/issues/469) |
| P4. Move `playwright_tests/test_database_guard.py` to a regular Django test module (Rule 10 violation; never opens a browser) | ~0 s direct (the tests still run) but reduces PW collection footprint | low | small | [#470](https://github.com/AI-Shipping-Labs/website/issues/470) |
| P5. Drop the `Run migrations` step from `deploy-dev.yml` (test runner already migrates the test DB; the production DB is not touched here) | ~6 s per CI job (median, p90 7 s) | low (the test runner handles migrations) | small (one-line workflow change — but per Non-goals of issue #466, NOT to be done in this audit; file as separate issue) | [#471](https://github.com/AI-Shipping-Labs/website/issues/471) |

P1 alone is the highest-value single change in this report. It is concentrated in one file; the
83 s figure is real and measurable today.

P2 is the long-running discipline lever — a steady drip of `setUp → setUpTestData` conversions
over time. It already paid back 30–50% of `content/tests/` time in early 2026; what remains is
the long tail.

P3 and P4 are tidiness, not throughput; they are still worth filing because they reduce noise and
clarify the layering rule the project already follows.

P5 is the only CI-config change. Per the non-goals of issue #466, no workflow edits land with
this audit; the savings are real but trivial and the issue is filed for the normal pipeline.

## 8. Mutation testing pilot proposal

### Tool

Recommend `mutmut` (https://mutmut.readthedocs.io). Reasoning:

- It is the more widely-used Python mutation tester in 2026 and works without an experiment-config
  file for simple targets (pure modules with their own test file).
- `cosmic-ray` has a richer experiment runner (filter operators, distribute mutations across
  workers), but the per-experiment YAML setup is overhead the project does not need for a single
  pilot module.
- `mutmut run --paths-to-mutate <module> --tests-dir <test-module> --runner "uv run pytest --no-header -q"`
  is the entire incantation; `cosmic-ray init` + `cosmic-ray exec` adds a step.

### Target module

`content/tier_config.py` (73 lines, three pure functions: `get_tiers`, `get_tiers_with_features`,
`get_activities`). It meets every selection criterion:

- Self-contained (one local DB read, otherwise pure Python).
- Pure logic: no HTTP client needed; tests already use the in-process Django ORM.
- Already has dedicated tests: `content/tests/test_tier_config.py` has 40 tests in 427 lines —
  high test-density per source line — covering empty-DB, three tiers, activity inheritance, and
  feature-line composition.
- Production-relevant: this module drives the homepage tier cards and activities page. A bug
  would mis-show pricing or feature lists to every visitor.

`content/access.py` (364 lines, 13 functions including the access-control matrix) was the runner
up. It is more critical to production than `tier_config.py`, but mutating 364 lines × the number
of access tests would push the pilot wall-clock past acceptable bounds for a first experiment, and
several functions in `access.py` do `isinstance(content, Course)` + `getattr(content, ...)` checks
that produce noise mutations (equivalent / cosmetic). `tier_config.py` is the cleaner first
experiment; if results are encouraging, `access.py` is the natural follow-on.

### Expected runtime

A rough mutation count is (lines × mean operators per line). For `tier_config.py` (73 LOC, mostly
list comprehensions and dict literals), expect 80–120 mutants. The associated test module runs in
≈ 2 s end-to-end (`uv run pytest content/tests/test_tier_config.py` on the same hardware). Upper
bound: 120 mutants × 2 s = 240 s = 4 min. Acceptable for a manual experiment that runs once.

### Worth-scaling criteria

The pilot is worth scaling (i.e., justifying running mutmut on `content/access.py`,
`payments/services/__init__.py`, `notifications/services/*`) if:

- Mutation score < 80 % AND the surviving mutants point to real test gaps (e.g., off-by-one in
  feature-list composition, misordered tier prefixes), not equivalent or cosmetic mutations.

The pilot is not worth scaling if:

- Mutation score ≥ 95 %, in which case the existing tests already pin down the specification
  tightly and the budget is better spent elsewhere.
- Most surviving mutants are equivalent (e.g., reordering a `dict()` literal that is later
  iterated in a non-deterministic order; replacing an `if not x` with `if x is None or x is
  False or x == ''` for a guaranteed-non-empty input).

The pilot itself is NOT executed as part of this audit. `mutmut` is not added to `pyproject.toml`
in this issue — that is part of the follow-up issue.

## 9. Follow-up issues filed

Each item below is filed as a separate issue with the `needs grooming` label, linking back to
issue #466.

| Proposal | Issue | Title |
|---|---|---|
| P1 (pagination fixture sizes) | [#467](https://github.com/AI-Shipping-Labs/website/issues/467) | Reduce fixture sizes in studio pagination tests (audit ci followup #466 P1) |
| P2 (`setUp` → `setUpTestData` second pass) | [#468](https://github.com/AI-Shipping-Labs/website/issues/468) | Convert remaining setUp to setUpTestData in heavy content tests (audit ci followup #466 P2) |
| P3 (drop PW tests duplicated by server-rendered Django) | [#469](https://github.com/AI-Shipping-Labs/website/issues/469) | Replace pure server-rendered Playwright tests with Django assertContains (audit ci followup #466 P3) |
| P4 (move `test_database_guard.py` out of `playwright_tests/`) | [#470](https://github.com/AI-Shipping-Labs/website/issues/470) | Move test_database_guard.py out of playwright_tests/ (audit ci followup #466 P4) |
| P5 (drop redundant `Run migrations` step in `deploy-dev.yml`) | [#471](https://github.com/AI-Shipping-Labs/website/issues/471) | Drop redundant 'Run migrations' step from deploy-dev.yml (audit ci followup #466 P5) |
| Mutation testing pilot on `content/tier_config.py` | [#472](https://github.com/AI-Shipping-Labs/website/issues/472) | Mutation testing pilot on content/tier_config.py (audit ci followup #466) |

All six issues are open with `needs grooming` and reference back to this audit (#466).
