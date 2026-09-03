"""End-to-end coverage for the Studio account-merge UI (issue #842).

Exercises the irreversible preview -> confirm flow from a real browser: a clean
merge with a moved event registration, the dry-run-is-a-no-op guarantee, the
self-merge stop, the unknown-email message, and the pre-fill-from-user-detail
entry point.

Non-staff access denial lives in ``studio/tests/test_user_merge.py``
(``StaffGateTest.test_member_gets_403``).

Usage:
    uv run pytest playwright_tests/test_studio_user_merge.py -v
"""

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

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
from django.utils import timezone  # noqa: E402

# Issue #656: local-only fixtures (DB seeding, session-cookie injection).
pytestmark = pytest.mark.local_only


def _clear_users_except_staff(staff_email):
    from accounts.models import User

    User.objects.exclude(email=staff_email).delete()
    connection.close()


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _register_for_event(email, event_slug):
    """Give ``email`` a registration for a (newly created) event."""
    from accounts.models import User
    from events.models import Event, EventRegistration

    event, _ = Event.objects.get_or_create(
        slug=event_slug,
        defaults={"title": event_slug, "start_datetime": timezone.now()},
    )
    EventRegistration.objects.get_or_create(
        event=event, user=User.objects.get(email=email)
    )
    connection.close()


def _secondary_state(email):
    """Return ``(is_active, has_alias_on_canonical)`` for assertions."""
    from accounts.models import EmailAlias, User

    user = User.objects.filter(email=email).first()
    is_active = bool(user and user.is_active)
    has_alias = EmailAlias.objects.filter(email=email).exists()
    connection.close()
    return is_active, has_alias


def _canonical_event_count(email):
    from accounts.models import User
    from events.models import EventRegistration

    user = User.objects.get(email=email)
    n = EventRegistration.objects.filter(user=user).count()
    connection.close()
    return n


def _fill_merge_form_and_preview(page, canonical_email, secondary_email):
    """Fill both merge inputs and submit with the ordinary browser journey."""
    page.locator('[data-testid="merge-canonical-input"]').fill(canonical_email)
    page.locator('[data-testid="merge-secondary-input"]').fill(secondary_email)
    page.locator('[data-testid="merge-preview-submit"]').click()
    page.wait_for_load_state("domcontentloaded")


def _query_for(request):
    return parse_qs(urlparse(request.url).query).get("q", [""])[0]


def _fulfill_search(route, results):
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results}),
    )


def _release_search(page, route, results):
    with page.expect_response(lambda response: response.url == route.request.url) as info:
        _fulfill_search(route, results)
    info.value.body()
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => "
        "requestAnimationFrame(resolve)))"
    )


def _assert_suggestions_dismissed(page, testid):
    suggestions = page.locator(f'[data-testid="{testid}"]')
    expect(suggestions).to_be_hidden()
    expect(suggestions.locator("li")).to_have_count(0)


@pytest.mark.django_db(transaction=True)
class TestPreviewThenConfirm:
    """Staff previews a merge, reviews the plan, and commits it."""

    @pytest.mark.core
    def test_full_preview_confirm_flow(self, django_server, browser):
        _ensure_tiers()
        staff_email = "merge-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        _register_for_event("dupe@test.com", "merge-ev")
        canonical_pk = _user_id_for("keep@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )

        keep_result = {
            "id": canonical_pk,
            "email": "keep@test.com",
            "display_name": "Keep Account",
            "first_name": "Keep",
            "last_name": "Account",
        }
        dupe_result = {
            "id": _user_id_for("dupe@test.com"),
            "email": "dupe@test.com",
            "display_name": "Duplicate Account",
            "first_name": "Duplicate",
            "last_name": "Account",
        }
        stale_result = {
            "id": 999999,
            "email": "stale@test.com",
            "display_name": "Stale Account",
            "first_name": "Stale",
            "last_name": "Account",
        }
        results_by_query = {
            "keep@test.com": [keep_result],
            "dupe@test.com": [dupe_result],
        }
        hold_once = {
            "keep@test.com",
            "dupe@test.com",
            "older-keep",
            "older-dupe",
        }
        pending = {}

        def control_search(route):
            query = _query_for(route.request)
            if query in hold_once:
                hold_once.remove(query)
                pending[query] = route
                return
            _fulfill_search(route, results_by_query.get(query, []))

        page.route("**/studio/api/users/search/**", control_search)
        canonical = page.locator('[data-testid="merge-canonical-input"]')
        secondary = page.locator('[data-testid="merge-secondary-input"]')
        submit = page.locator('[data-testid="merge-preview-submit"]')

        # A response that arrives after either input blurs cannot reopen its
        # list or intercept the ordinary Preview merge click.
        with page.expect_request(lambda request: _query_for(request) == "keep@test.com"):
            canonical.fill("keep@test.com")
        canonical.press("Tab")
        expect(secondary).to_be_focused()
        _release_search(page, pending["keep@test.com"], [keep_result])
        _assert_suggestions_dismissed(page, "merge-canonical-suggestions")

        with page.expect_request(lambda request: _query_for(request) == "dupe@test.com"):
            secondary.fill("dupe@test.com")
        secondary.press("Tab")
        expect(submit).to_be_focused()
        _release_search(page, pending["dupe@test.com"], [dupe_result])
        _assert_suggestions_dismissed(page, "merge-secondary-suggestions")

        submit.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator('[data-testid="merge-preview"]')).to_be_visible()

        # Repeat through pointer selection while an older response remains in
        # flight. Releasing each stale response must preserve the selected
        # account and keep its list empty.
        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        canonical = page.locator('[data-testid="merge-canonical-input"]')
        secondary = page.locator('[data-testid="merge-secondary-input"]')

        with page.expect_request(lambda request: _query_for(request) == "older-keep"):
            canonical.fill("older-keep")
        with page.expect_response(
            lambda response: _query_for(response.request) == "keep@test.com"
        ):
            canonical.fill("keep@test.com")
        keep_suggestion = page.locator(
            '[data-testid="merge-canonical-search-suggestion"]'
        ).filter(has_text="keep@test.com")
        expect(keep_suggestion).to_be_visible()
        keep_suggestion.click()
        _release_search(page, pending["older-keep"], [stale_result])
        expect(canonical).to_have_value("keep@test.com")
        _assert_suggestions_dismissed(page, "merge-canonical-suggestions")

        with page.expect_request(lambda request: _query_for(request) == "older-dupe"):
            secondary.fill("older-dupe")
        with page.expect_response(
            lambda response: _query_for(response.request) == "dupe@test.com"
        ):
            secondary.fill("dupe@test.com")
        dupe_suggestion = page.locator(
            '[data-testid="merge-secondary-search-suggestion"]'
        ).filter(has_text="dupe@test.com")
        expect(dupe_suggestion).to_be_visible()
        dupe_suggestion.click()
        _release_search(page, pending["older-dupe"], [stale_result])
        expect(secondary).to_have_value("dupe@test.com")
        _assert_suggestions_dismissed(page, "merge-secondary-suggestions")

        page.locator('[data-testid="merge-preview-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Plan shows the moved event registration row + deactivation notice.
        assert page.locator('[data-testid="merge-preview"]').count() == 1
        plan = page.locator('[data-testid="merge-plan"]')
        assert "events.EventRegistration" in plan.inner_text()
        assert page.locator(
            '[data-testid="merge-plan-deactivate-notice"]'
        ).count() == 1
        assert page.locator('[data-testid="merge-confirm-submit"]').count() == 1

        # Confirm.
        page.once("dialog", lambda d: d.accept())
        page.locator('[data-testid="merge-confirm-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Success headline + link to canonical detail.
        headline = page.locator('[data-testid="merge-result-headline"]')
        assert headline.count() == 1
        assert "dupe@test.com merged into keep@test.com" in headline.inner_text()
        link = page.locator('[data-testid="merge-result-canonical-link"]')
        assert link.get_attribute("href").endswith(
            f"/studio/users/{canonical_pk}/"
        )

        # Canonical now owns the registration.
        assert _canonical_event_count("keep@test.com") == 1
        is_active, has_alias = _secondary_state("dupe@test.com")
        assert is_active is False
        assert has_alias is True

        # Following the link loads the canonical detail page.
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert f"/studio/users/{canonical_pk}/" in page.url

        context.close()


@pytest.mark.django_db(transaction=True)
class TestPreviewIsDryRun:
    """Preview persists nothing."""

    def test_preview_changes_nothing(self, django_server, browser):
        _ensure_tiers()
        staff_email = "dry-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        _create_user("dupe@test.com", tier_slug="free")
        _register_for_event("dupe@test.com", "dry-ev")

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        _fill_merge_form_and_preview(page, "keep@test.com", "dupe@test.com")
        assert page.locator('[data-testid="merge-preview"]').count() == 1

        # Without confirming, secondary is untouched.
        is_active, has_alias = _secondary_state("dupe@test.com")
        assert is_active is True
        assert has_alias is False
        assert _canonical_event_count("keep@test.com") == 0
        assert _canonical_event_count("dupe@test.com") == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestSelfMergeStopped:
    """An account cannot be merged into itself."""

    def test_self_merge_blocked(self, django_server, browser):
        _ensure_tiers()
        staff_email = "self-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("solo@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        _fill_merge_form_and_preview(page, "solo@test.com", "solo@test.com")

        err = page.locator('[data-testid="merge-error-self-merge"]')
        assert err.count() == 1
        assert "into itself" in err.inner_text().lower()
        assert page.locator('[data-testid="merge-confirm-submit"]').count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestUnknownEmail:
    """An email with no account shows a clear field message."""

    def test_unknown_secondary(self, django_server, browser):
        _ensure_tiers()
        staff_email = "ghost-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/merge/",
            wait_until="domcontentloaded",
        )
        _fill_merge_form_and_preview(page, "keep@test.com", "ghost@test.com")

        err = page.locator('[data-testid="merge-error-secondary"]')
        assert err.count() == 1
        assert "No account found for ghost@test.com" in err.inner_text()
        assert page.locator('[data-testid="merge-preview"]').count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestPrefilledFromUserDetail:
    """The user detail "Merge accounts" action pre-fills canonical."""

    def test_prefill_canonical(self, django_server, browser):
        _ensure_tiers()
        staff_email = "prefill-admin@test.com"
        _create_staff_user(staff_email)
        _clear_users_except_staff(staff_email)
        _create_user("keep@test.com", tier_slug="free")
        member_pk = _user_id_for("keep@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        action = page.locator('[data-testid="user-detail-merge"]')
        assert action.count() == 1
        action.click()
        page.wait_for_load_state("domcontentloaded")

        assert "/studio/users/merge/" in page.url
        canonical_input = page.locator('[data-testid="merge-canonical-input"]')
        assert canonical_input.input_value() == "keep@test.com"

        context.close()
