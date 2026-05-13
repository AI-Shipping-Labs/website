"""Playwright E2E tests for the Studio CRM (issue #560).

Covers the scenarios in the spec:

1. Account-only view of the user profile.
2. Track-to-CRM from a previously-untracked profile.
3. Idempotent re-track.
4. Existing notes survive backfill and surface on the CRM record.
5. Plans-only user does not pollute the CRM list.
6. Snapshot (persona, summary, next_steps) round-trip.
7. (removed) Experiments CRUD — feature dropped in issue #590.
8. Archive / reactivate.
9. Non-staff returns 403 from CRM surfaces.
10. Anonymous redirects to login.

Plus issue #590 cleanup scenarios:

11. Notes block uses standard card chrome (no colored stripe) on the
    CRM detail page.
12. Experiments section is gone from the CRM detail page.
13. Removed experiment URLs return 404.
14. Notes block uses standard card chrome on the plan detail page.
15. Member-facing /account/ page is unaffected.
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

STAFF_EMAIL = "crm-e2e-staff@test.com"
ENGAGED_EMAIL = "crm-e2e-engaged@test.com"
MEMBER_EMAIL = "crm-e2e-member@test.com"
COLD_EMAIL = "crm-e2e-cold@test.com"


def _wipe_state():
    """Reset relationship-table state between tests.

    The CRM data migration backfills records on first migrate; once
    Playwright is up we get a single shared DB, so each test wipes the
    CRM, plan, sprint, and note tables to start from a clean slate and
    seeds the four test users below.
    """
    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote, Plan, Sprint

    CRMRecord.objects.all().delete()
    InterviewNote.objects.all().delete()
    Plan.objects.all().delete()
    Sprint.objects.all().delete()
    # Keep staff users so the auth_context cookie keeps working.
    User.objects.exclude(is_staff=True).delete()
    connection.close()


def _seed_users_and_data():
    """Seed staff + 3 members with the exact data the scenarios need.

    Returns a dict with the primary keys + the engaged user's CRM
    record id so the test can build URLs without re-querying.
    """
    from accounts.models import User
    from crm.models import CRMRecord
    from plans.models import InterviewNote, Plan, Sprint

    _create_staff_user(STAFF_EMAIL)
    engaged = _create_user(
        ENGAGED_EMAIL, tier_slug="main", email_verified=True,
    )
    member = _create_user(
        MEMBER_EMAIL, tier_slug="main", email_verified=True,
    )
    cold = _create_user(
        COLD_EMAIL, tier_slug="free", email_verified=True,
    )
    staff = User.objects.get(email=STAFF_EMAIL)
    sprint = Sprint.objects.create(
        name="Spring 2026 CRM",
        slug="spring-2026-crm",
        start_date=datetime.date(2026, 3, 1),
    )

    # engaged: 2 plans + 3 notes (2 internal, 1 external)
    plan_a = Plan.objects.create(member=engaged, sprint=sprint)
    sprint_b = Sprint.objects.create(
        name="Summer 2026 CRM",
        slug="summer-2026-crm",
        start_date=datetime.date(2026, 6, 1),
    )
    plan_b = Plan.objects.create(member=engaged, sprint=sprint_b)
    InterviewNote.objects.create(
        member=engaged, plan=plan_a, visibility="internal",
        kind="intake", body="Engaged internal note one",
        created_by=staff,
    )
    InterviewNote.objects.create(
        member=engaged, plan=None, visibility="internal",
        kind="meeting", body="Engaged internal note two",
        created_by=staff,
    )
    InterviewNote.objects.create(
        member=engaged, plan=None, visibility="external",
        kind="general", body="Engaged external note",
        created_by=staff,
    )

    # member: 1 plan, 0 notes -> NOT auto-tracked
    Plan.objects.create(member=member, sprint=sprint)

    # cold: nothing.

    # Run the backfill (mirrors the data migration body).
    member_ids = (
        InterviewNote.objects
        .filter(member__crm_record__isnull=True)
        .values_list('member_id', flat=True)
        .distinct()
    )
    for member_id in member_ids:
        CRMRecord.objects.get_or_create(
            user_id=member_id, defaults={'status': 'active'},
        )

    engaged_record = CRMRecord.objects.get(user=engaged)
    pks = {
        'engaged_pk': engaged.pk,
        'member_pk': member.pk,
        'cold_pk': cold.pk,
        'engaged_record_pk': engaged_record.pk,
        'plan_a_pk': plan_a.pk,
        'plan_b_pk': plan_b.pk,
        'sprint_pk': sprint.pk,
    }
    connection.close()
    return pks


@pytest.mark.django_db(transaction=True)
class TestStudioCRM:
    """All ten Playwright scenarios for issue #560."""

    def test_scenario_1_account_only_profile_for_cold_user(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{pks['cold_pk']}/",
            wait_until="domcontentloaded",
        )

        assert page.locator(
            '[data-testid="user-detail-profile-section"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="user-detail-membership-section"]'
        ).is_visible()
        assert page.locator('[data-testid="user-tags-section"]').is_visible()

        # The removed sections must not be on the page.
        assert page.locator(
            '[data-testid="user-detail-plans-section"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="member-notes-section"]'
        ).count() == 0
        assert page.get_by_role(
            "heading", name="Sprints & plans"
        ).count() == 0
        assert page.get_by_role(
            "heading", name="Member notes"
        ).count() == 0

        # The CRM card shows the Track button.
        assert page.locator(
            '[data-testid="user-crm-cta-track"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="user-crm-cta-open"]'
        ).count() == 0
        context.close()

    @pytest.mark.core
    def test_scenario_2_track_untracked_user_from_profile(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{pks['cold_pk']}/",
            wait_until="domcontentloaded",
        )

        # State A: Track button visible.
        track_button = page.locator('[data-testid="user-crm-cta-track"]')
        assert track_button.is_visible()
        assert page.get_by_text("Not yet tracked in CRM").is_visible()

        # Click — lands on the new CRM record detail.
        track_button.click()
        page.wait_for_url("**/studio/crm/**")
        assert page.locator('[data-testid="crm-detail-header"]').is_visible()
        assert page.get_by_text(COLD_EMAIL).first.is_visible()

        # Empty-state copy in the relationship sections.
        assert page.locator(
            '[data-testid="crm-plans-empty"]'
        ).is_visible()

        # Navigate back to the profile: now shows the Open button.
        page.goto(
            f"{django_server}/studio/users/{pks['cold_pk']}/",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="user-crm-cta-open"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="user-crm-cta-track"]'
        ).count() == 0
        assert page.get_by_text("Tracked since").is_visible()
        context.close()

    def test_scenario_3_re_tracking_is_idempotent(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        from crm.models import CRMRecord

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        # 1. Engaged user already has a record post-backfill -> State B.
        page.goto(
            f"{django_server}/studio/users/{pks['engaged_pk']}/",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="user-crm-cta-open"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="user-crm-cta-track"]'
        ).count() == 0

        # 2. Manually POST to /crm/track and assert no duplicate.
        before = CRMRecord.objects.filter(user_id=pks['engaged_pk']).count()
        assert before == 1
        # The profile in State B only renders the Open link (no form),
        # so we open another page that has a form to harvest a token —
        # the cold user profile renders the Track form.
        page.goto(
            f"{django_server}/studio/users/{pks['cold_pk']}/",
            wait_until="domcontentloaded",
        )
        csrf_value = page.locator(
            'form[action$="/crm/track"] input[name="csrfmiddlewaretoken"]'
        ).get_attribute("value")

        response = page.request.post(
            f"{django_server}/studio/users/{pks['engaged_pk']}/crm/track",
            data={"csrfmiddlewaretoken": csrf_value},
            headers={"X-CSRFToken": csrf_value, "Referer": django_server},
            max_redirects=0,
        )
        assert response.status == 302
        assert (
            f"/studio/crm/{pks['engaged_record_pk']}/" in response.headers["location"]
        )
        after = CRMRecord.objects.filter(user_id=pks['engaged_pk']).count()
        assert after == 1
        connection.close()
        context.close()

    def test_scenario_4_notes_survive_and_surface_on_crm_record(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{pks['engaged_pk']}/",
            wait_until="domcontentloaded",
        )

        # Profile must not show notes inline.
        assert page.locator(
            '[data-testid="member-notes-section"]'
        ).count() == 0

        # Click Open CRM record.
        page.locator('[data-testid="user-crm-cta-open"]').click()
        page.wait_for_url(f"**/studio/crm/{pks['engaged_record_pk']}/")

        # All three existing notes are visible.
        notes_section = page.locator(
            '[data-testid="crm-notes-section"]'
        )
        assert notes_section.is_visible()
        assert page.get_by_text("Engaged internal note one").is_visible()
        assert page.get_by_text("Engaged internal note two").is_visible()
        assert page.get_by_text("Engaged external note").is_visible()
        # Internal/external split labels are present.
        assert page.locator(
            '[data-testid="internal-notes-heading"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="external-notes-heading"]'
        ).is_visible()
        context.close()

    def test_scenario_5_plans_only_user_not_in_crm_list(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        # CRM list: member is not present (plans only, no notes).
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text(MEMBER_EMAIL).count() == 0
        assert page.get_by_text(ENGAGED_EMAIL).first.is_visible()

        # Member profile shows Track button (not Open).
        page.goto(
            f"{django_server}/studio/users/{pks['member_pk']}/",
            wait_until="domcontentloaded",
        )
        assert page.locator(
            '[data-testid="user-crm-cta-track"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="user-crm-cta-open"]'
        ).count() == 0

        # Click Track -> lands on the new record page.
        page.locator('[data-testid="user-crm-cta-track"]').click()
        page.wait_for_url("**/studio/crm/**")

        # CRM list now includes the member with Plans count 1.
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        member_link = page.get_by_text(MEMBER_EMAIL).first
        assert member_link.is_visible()
        # Search the row containing the email and check plans count.
        row = page.locator(
            f'tr:has-text("{MEMBER_EMAIL}")'
        )
        plans_cell = row.locator(
            '[data-testid="crm-row-plans-count"]'
        )
        assert plans_cell.inner_text().strip() == '1'
        context.close()

    def test_scenario_6_snapshot_round_trip_and_member_view_is_safe(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )

        persona = "Sam — The Technical Professional Moving to AI"
        summary = "Backend engineer pivoting to LLM tooling"
        next_steps = "Pair on agents lesson; review eval framework"

        page.locator(
            '[data-testid="crm-persona-input"]'
        ).fill(persona)
        page.locator(
            '[data-testid="crm-summary-input"]'
        ).fill(summary)
        page.locator(
            '[data-testid="crm-next-steps-input"]'
        ).fill(next_steps)
        page.locator('[data-testid="crm-snapshot-save"]').click()

        page.wait_for_url(
            f"**/studio/crm/{pks['engaged_record_pk']}/"
        )
        assert page.locator(
            '[data-testid="crm-persona-input"]'
        ).input_value() == persona
        assert summary in page.locator(
            '[data-testid="crm-summary-input"]'
        ).input_value()

        # CRM list shows the persona.
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text(
            "Sam — The Technical Professional Moving to AI"
        ).first.is_visible()

        context.close()

        # Now visit /account/ as the engaged member: staff-only fields
        # must NOT appear.
        member_ctx = _auth_context(browser, ENGAGED_EMAIL)
        member_page = member_ctx.new_page()
        member_page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        body = member_page.content()
        assert "Backend engineer pivoting" not in body
        assert "Pair on agents lesson" not in body
        assert "Sam — The Technical Professional" not in body

        # Member plan editor must also be safe.
        member_page.goto(
            f"{django_server}/account/plan/{pks['plan_a_pk']}/edit/",
            wait_until="domcontentloaded",
        )
        body = member_page.content()
        assert "Backend engineer pivoting" not in body
        assert "Pair on agents lesson" not in body
        assert "Sam — The Technical Professional" not in body
        member_ctx.close()

    def test_scenario_8_archive_and_reactivate(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        # Active list shows engaged.
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text(ENGAGED_EMAIL).first.is_visible()

        # Archive the record.
        page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="crm-detail-archive"]'
        ).click()
        page.wait_for_url(
            f"**/studio/crm/{pks['engaged_record_pk']}/"
        )
        assert page.locator(
            '[data-testid="crm-detail-reactivate"]'
        ).is_visible()

        # Default Active filter no longer includes engaged.
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text(ENGAGED_EMAIL).count() == 0

        # Archived filter shows them.
        page.locator('[data-testid="crm-filter-archived"]').click()
        page.wait_for_url("**/studio/crm/?filter=archived")
        assert page.get_by_text(ENGAGED_EMAIL).first.is_visible()

        # Reactivate.
        page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="crm-detail-reactivate"]'
        ).click()
        page.wait_for_url(
            f"**/studio/crm/{pks['engaged_record_pk']}/"
        )

        # Default Active filter shows them again.
        page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert page.get_by_text(ENGAGED_EMAIL).first.is_visible()
        context.close()

    def test_scenario_9_non_staff_cannot_access_crm(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        # Engaged is a Main-tier member, not staff.
        context = _auth_context(browser, ENGAGED_EMAIL)
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403

        response = page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403

        # POST to track endpoint also returns 403. Reuse Playwright's
        # APIRequestContext from the same browser context.
        api_response = page.request.post(
            f"{django_server}/studio/users/{pks['engaged_pk']}/crm/track",
            headers={"Referer": django_server},
        )
        assert api_response.status == 403
        context.close()

    def test_scenario_10_anonymous_redirects_to_login(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        # Fresh context with no session cookie.
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/crm/",
            wait_until="domcontentloaded",
        )
        # Followed redirect should land on login.
        assert "/accounts/login/" in page.url
        assert response.status in (200, 302)

        response = page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login/" in page.url
        context.close()

    # ---------------------------------------------------------------
    # Issue #590 cleanup scenarios
    # ---------------------------------------------------------------

    def test_scenario_11_notes_block_uses_standard_card_chrome(
        self, django_server, browser,
    ):
        """Notes section uses the standard card chrome with no
        purple/green left-side stripe; pill badges still distinguish
        Internal vs External."""
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )

        internal_section = page.locator('[data-testid="internal-notes"]')
        external_section = page.locator('[data-testid="external-notes"]')
        assert internal_section.is_visible()
        assert external_section.is_visible()

        # The colored left-side stripe utilities must be gone from
        # both sections.
        internal_class = internal_section.get_attribute("class") or ""
        external_class = external_section.get_attribute("class") or ""
        assert "border-l-4" not in internal_class
        assert "border-l-purple-400/60" not in internal_class
        assert "border-l-4" not in external_class
        assert "border-l-green-400/60" not in external_class

        # The standard card chrome must be present on both.
        for token in ("bg-card", "border", "border-border", "rounded-lg", "p-6"):
            assert token in internal_class, (
                f"missing class token {token!r} on internal-notes; "
                f"got: {internal_class}"
            )
            assert token in external_class, (
                f"missing class token {token!r} on external-notes; "
                f"got: {external_class}"
            )

        # Pill badges still distinguish the two sections.
        assert internal_section.get_by_text("Internal", exact=True).is_visible()
        assert external_section.get_by_text("External", exact=True).is_visible()

        # The seeded notes themselves are listed inside each section.
        assert internal_section.get_by_text(
            "Engaged internal note one"
        ).is_visible()
        assert external_section.get_by_text(
            "Engaged external note"
        ).is_visible()
        context.close()

    def test_scenario_12_experiments_section_is_gone(
        self, django_server, browser,
    ):
        """The CRM detail page no longer renders any Experiments UI."""
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        response = page.goto(
            f"{django_server}/studio/crm/{pks['engaged_record_pk']}/",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        # No Experiments heading and no experiments-related testids.
        assert page.get_by_role(
            "heading", name="Experiments", exact=True,
        ).count() == 0
        for testid in (
            "crm-experiments-section",
            "crm-experiments-empty",
            "crm-experiments-list",
            "crm-experiment-add-toggle",
            "crm-experiment-add-form",
            "crm-experiment-add-submit",
            "crm-experiment-title-input",
            "crm-experiment-hypothesis-input",
            "crm-experiment-status-input",
            "crm-experiment-edit-link",
            "crm-experiment-delete",
        ):
            assert page.locator(
                f'[data-testid="{testid}"]'
            ).count() == 0, f"unexpected element with testid {testid!r}"

        # The remaining sections still render in the same vertical
        # order: header, snapshot, plans, notes, content context.
        expected_order = [
            "crm-detail-header",
            "crm-snapshot-card",
            "crm-plans-section",
            "crm-notes-section",
            "crm-content-context-section",
        ]
        body = page.content()
        positions = [body.find(f'data-testid="{t}"') for t in expected_order]
        for testid, pos in zip(expected_order, positions):
            assert pos != -1, f"missing testid {testid!r} on CRM detail page"
        assert positions == sorted(positions), (
            f"sections out of order; got positions: "
            f"{list(zip(expected_order, positions))}"
        )
        context.close()

    def test_scenario_13_old_experiment_urls_return_404(
        self, django_server, browser,
    ):
        """The three former experiment URL patterns no longer exist."""
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()

        # Harvest a CSRF token from a profile page (any GET form works).
        page.goto(
            f"{django_server}/studio/users/{pks['cold_pk']}/",
            wait_until="domcontentloaded",
        )
        csrf_value = page.locator(
            'input[name="csrfmiddlewaretoken"]'
        ).first.get_attribute("value")

        record_pk = pks['engaged_record_pk']

        # 1. POST to /experiments/new -> 404.
        resp = page.request.post(
            f"{django_server}/studio/crm/{record_pk}/experiments/new",
            data={
                "csrfmiddlewaretoken": csrf_value,
                "title": "should never land",
            },
            headers={"X-CSRFToken": csrf_value, "Referer": django_server},
            max_redirects=0,
        )
        assert resp.status == 404

        # 2. GET to /experiments/1/edit -> 404.
        resp = page.request.get(
            f"{django_server}/studio/crm/{record_pk}/experiments/1/edit",
            max_redirects=0,
        )
        assert resp.status == 404

        # 3. POST to /experiments/1/delete -> 404.
        resp = page.request.post(
            f"{django_server}/studio/crm/{record_pk}/experiments/1/delete",
            data={"csrfmiddlewaretoken": csrf_value},
            headers={"X-CSRFToken": csrf_value, "Referer": django_server},
            max_redirects=0,
        )
        assert resp.status == 404
        context.close()

    def test_scenario_14_plan_detail_notes_block_standard_chrome(
        self, django_server, browser,
    ):
        """The shared notes partial also renders cleanly on the plan
        detail page (the same partial powers both surfaces)."""
        _ensure_tiers()
        _wipe_state()
        pks = _seed_users_and_data()

        context = _auth_context(browser, STAFF_EMAIL)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{pks['plan_a_pk']}/",
            wait_until="domcontentloaded",
        )

        internal_section = page.locator('[data-testid="internal-notes"]')
        external_section = page.locator('[data-testid="external-notes"]')
        assert internal_section.is_visible()
        assert external_section.is_visible()

        internal_class = internal_section.get_attribute("class") or ""
        external_class = external_section.get_attribute("class") or ""
        assert "border-l-4" not in internal_class
        assert "border-l-purple-400/60" not in internal_class
        assert "border-l-4" not in external_class
        assert "border-l-green-400/60" not in external_class
        for token in ("bg-card", "border", "border-border", "rounded-lg", "p-6"):
            assert token in internal_class
            assert token in external_class
        context.close()

    def test_scenario_15_member_account_page_unaffected(
        self, django_server, browser,
    ):
        """The member-facing /account/ page still renders normally
        with no leak of staff CRM fields after the model removal."""
        _ensure_tiers()
        _wipe_state()
        _seed_users_and_data()

        context = _auth_context(browser, MEMBER_EMAIL)
        page = context.new_page()
        response = page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        body = page.content()
        # Staff-only CRM fields must not appear (existing leak guard).
        assert "Backend engineer pivoting" not in body
        assert "Pair on agents lesson" not in body
        # The deprecated Experiments UI must not appear either.
        assert "Add experiment" not in body
        assert "crm-experiments-section" not in body
        assert "crm-experiment-add" not in body
        context.close()
