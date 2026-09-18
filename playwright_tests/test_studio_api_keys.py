"""Playwright E2E for the Studio API key page (issue #1737).

``/studio/api-keys/`` manages ``community_base.api.APIKey`` -- the scoped
bearer credential for the versioned ``/api/v1/`` API. Before #1737 the page
rendered as an empty Studio shell (the package template's ``{% block content %}``
was silently dropped by the site's ``studio_content`` shell) over a queryset
that listed every key in the database, member-owned ones included.

These tests cover the operator flow in a real browser and, crucially, prove
the credential half against the live API: a minted key authenticates
``GET /api/v1/settings`` only within its scopes, and revoking it through the
UI cuts that access rather than flipping a display flag.

``playwright_tests/test_api_token_management.py`` is the sibling for the
adjacent ``accounts.Token`` page.

Usage:
    uv run pytest playwright_tests/test_studio_api_keys.py -v
"""

import os

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
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

API_KEYS_URL_PATH = "/studio/api-keys/"
SETTINGS_API_PATH = "/api/v1/settings"


def _create_staff_only_user(email):
    """Create an is_staff=True is_superuser=False user."""
    from accounts.models import User

    _ensure_tiers()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": False,
        },
    )
    user.set_password("TestPass123!")
    user.is_staff = True
    user.is_superuser = False
    user.email_verified = True
    user.save()
    connection.close()
    return user


def _delete_all_api_keys():
    from community_base.api.models import APIKey

    APIKey.objects.all().delete()
    connection.close()


def _create_key(email, name, scopes, kind):
    """Seed an APIKey directly and return ``(row_id, plaintext)``."""
    from community_base.api.models import APIKey

    from accounts.models import User

    user = User.objects.get(email=email)
    api_key, plaintext = APIKey.create_for_user(
        user=user, name=name, scopes=scopes, kind=kind
    )
    result = (api_key.id, plaintext)
    connection.close()
    return result


def _key_is_revoked(key_id):
    from community_base.api.models import APIKey

    revoked = APIKey.objects.get(pk=key_id).revoked_at is not None
    connection.close()
    return revoked


def _key_count():
    from community_base.api.models import APIKey

    count = APIKey.objects.count()
    connection.close()
    return count


def _mint_key_through_the_ui(page, django_server, name, scopes):
    """Drive the create form and return the one-shot plaintext value."""
    page.goto(
        f"{django_server}{API_KEYS_URL_PATH}new/",
        wait_until="domcontentloaded",
    )
    page.locator('[data-testid="key-name-input"]').fill(name)
    page.locator('[data-testid="key-scopes-input"]').fill(scopes)
    page.locator('[data-testid="api-key-create-submit"]').click()
    page.wait_for_load_state("domcontentloaded")
    assert f"{API_KEYS_URL_PATH}created/" in page.url, page.url
    return page.locator('[data-testid="api-key-value"]').inner_text().strip()


def _call_settings_api(browser, django_server, bearer=None):
    """Call GET /api/v1/settings from a context with no Studio session."""
    anonymous = browser.new_context()
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    response = anonymous.request.get(
        f"{django_server}{SETTINGS_API_PATH}", headers=headers
    )
    status = response.status
    anonymous.close()
    return status


@pytest.mark.django_db(transaction=True)
class TestOperatorMintsAKeyAndCopiesItOnce:
    """Scenario: mint a key for the versioned API and copy it exactly once."""

    @pytest.mark.core
    @browser_journey
    def test_empty_state_to_one_shot_plaintext_to_masked_row(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys@test.com"
        _create_staff_user(admin_email)
        _delete_all_api_keys()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        # 1. A helpful empty state, not a blank shell and not an empty table.
        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}",
            wait_until="domcontentloaded",
        )
        expect(page.locator('[data-testid="studio-header"]')).to_be_visible()
        expect(
            page.locator('[data-testid="studio-empty-state-fresh"]')
        ).to_be_visible()
        expect(page.locator('[data-testid="api-keys-list"]')).to_have_count(0)

        # 2. The create CTA.
        page.locator('[data-testid="api-key-create-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert f"{API_KEYS_URL_PATH}new/" in page.url
        owner_note = page.locator('[data-testid="key-owner-note"]').inner_text()
        assert admin_email in owner_note

        # 3. Name + scope, submit.
        page.locator('[data-testid="key-name-input"]').fill("settings sync script")
        page.locator('[data-testid="key-scopes-input"]').fill("settings.read")
        page.locator('[data-testid="api-key-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert f"{API_KEYS_URL_PATH}created/" in page.url
        plaintext = page.locator('[data-testid="api-key-value"]').inner_text().strip()
        assert plaintext.startswith("cb_staff_"), plaintext
        warning = page.locator('[data-testid="api-key-warning"]').inner_text()
        assert "only time this key will be shown" in warning
        assert "it will not be shown again" in page.content()

        # 4. Reload the one-shot page: back to the list, plaintext gone.
        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}created/",
            wait_until="domcontentloaded",
        )
        assert page.url.rstrip("/").endswith("/studio/api-keys")
        assert plaintext not in page.content()

        # 5. The row carries the name, the masked prefix and the admin email.
        row = page.locator('[data-testid="api-key-row"]').first
        row_text = row.inner_text()
        assert "settings sync script" in row_text
        assert admin_email in row_text
        # The row shows an identifying stub, not a usable chunk of the secret:
        # the kind prefix plus four characters (issue #1737). Anything longer
        # is plaintext key material sitting in a page we do not need it on.
        prefix = page.locator('[data-testid="api-key-prefix"]').first.inner_text()
        assert prefix.strip() == f"{plaintext[:13]}\u2026", prefix
        assert prefix.strip().startswith("cb_staff_")
        secret_shown = prefix.strip()[len("cb_staff_"):].rstrip("\u2026")
        assert len(secret_shown) == 4, secret_shown
        assert plaintext[:24] not in page.content()
        assert plaintext not in page.content()

        context.close()


@pytest.mark.django_db(transaction=True)
class TestMintedKeyWorksAgainstTheApiItWasMintedFor:
    """Scenario: a newly minted key actually works, and its scope binds."""

    @pytest.mark.core
    @browser_journey
    def test_scope_box_constrains_real_bearer_access(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys-auth@test.com"
        _create_staff_user(admin_email)
        _delete_all_api_keys()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        allowed = _mint_key_through_the_ui(
            page, django_server, "settings reader", "settings.read"
        )

        # 1. No Authorization header at all.
        assert _call_settings_api(browser, django_server) == 401

        # 2. The minted key authenticates the route it was minted for.
        assert _call_settings_api(browser, django_server, bearer=allowed) == 200

        # 3. A key scoped elsewhere is refused by the same route.
        wrong_scope = _mint_key_through_the_ui(
            page, django_server, "curriculum reader", "curriculum.read"
        )
        assert (
            _call_settings_api(browser, django_server, bearer=wrong_scope) == 403
        )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestRevokeIsReachableOnAnOperatorLaptop:
    """Scenario: the kill control is on-screen at real operator widths.

    The original #1737 bug was a page that silently rendered less than it
    should. A table wide enough to push Actions past the right edge of the
    content well is the same failure in a different costume: the wrapper
    scrolls, but overlay scrollbars draw no track, so an operator opening
    this page to kill a compromised key sees a key list with no Revoke.
    """

    @browser_journey
    def test_revoke_is_on_screen_at_1280_and_1366(self, django_server, browser):
        from community_base.api.models import APIKey

        # The fixture is the guard. With short content the layout passes even
        # with the density and break-all headroom stripped out, so the widest
        # plausible real row is used: a long shared-mailbox address and a
        # descriptive key name are both things operators actually type, and
        # they are what consume the remaining slack.
        admin_email = (
            "platform-automation-and-release-engineering@"
            "team.aishippinglabs.example.com"
        )
        _create_staff_user(admin_email)
        _delete_all_api_keys()
        _create_key(
            admin_email,
            "nightly deploy pipeline health check and metrics exporter",
            ["settings.read", "settings.write"],
            APIKey.Kind.STAFF,
        )

        context = _auth_context(browser, admin_email)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        for width, height in ((1280, 900), (1366, 768)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto(
                f"{django_server}{API_KEYS_URL_PATH}",
                wait_until="domcontentloaded",
            )

            revoke = page.locator('[data-testid="api-key-revoke"]').first
            expect(revoke).to_be_visible()

            # to_be_visible() is not enough: an element scrolled outside its
            # overflow-x container still reports visible. Assert the actual
            # geometry an operator sees.
            fits = revoke.evaluate(
                "el => { const r = el.getBoundingClientRect();"
                " return r.left >= 0 && r.right <= window.innerWidth; }"
            )
            assert fits, f"Revoke is off-screen at {width}x{height}"

            wrapper = page.locator('[data-testid="api-keys-list"]')
            overflow = wrapper.evaluate(
                "el => el.scrollWidth - el.clientWidth"
            )
            assert overflow == 0, (
                f"API keys table overflows its wrapper by {overflow}px at "
                f"{width}x{height} with no scrollbar affordance"
            )

            revoke.click()
            page.wait_for_load_state("domcontentloaded")
            expect(
                page.locator('[data-testid="api-key-status"]').first
            ).to_contain_text("Revoked")
            # Re-seed for the next viewport in the loop.
            if width == 1280:
                _delete_all_api_keys()
                _create_key(
                    admin_email,
                    "nightly deploy pipeline health check and metrics exporter",
                    ["settings.read", "settings.write"],
                    APIKey.Kind.STAFF,
                )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestRevokeCutsRealAccess:
    """Scenario: operator retires a script and the key stops working."""

    @pytest.mark.core
    @browser_journey
    def test_revoking_through_the_list_kills_the_bearer_credential(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys-revoke@test.com"
        _create_staff_user(admin_email)
        _delete_all_api_keys()

        context = _auth_context(browser, admin_email)
        page = context.new_page()

        plaintext = _mint_key_through_the_ui(
            page, django_server, "doomed script", "settings.read"
        )
        assert _call_settings_api(browser, django_server, bearer=plaintext) == 200

        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}",
            wait_until="domcontentloaded",
        )
        status_cell = page.locator('[data-testid="api-key-status"]').first
        assert "Active" in status_cell.inner_text()
        expect(page.locator('[data-testid="api-key-revoke"]')).to_have_count(1)

        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="api-key-revoke"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.rstrip("/").endswith("/studio/api-keys")
        assert "API key revoked." in page.content()
        status_cell = page.locator('[data-testid="api-key-status"]').first
        assert "Revoked" in status_cell.inner_text()
        expect(page.locator('[data-testid="api-key-revoke"]')).to_have_count(0)

        # The row still lists the key for audit, but access is really gone.
        assert _call_settings_api(browser, django_server, bearer=plaintext) == 401

        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberKeysNeverSurfaceOnTheStaffPage:
    """Scenario: a member's key is unreachable by page or by guessed id."""

    @pytest.mark.core
    @browser_journey
    def test_member_key_is_absent_and_its_revoke_url_404s(
        self, django_server, browser
    ):
        from community_base.api.models import APIKey

        admin_email = "admin-apikeys-scope@test.com"
        member_email = "free@test.com"
        _create_staff_user(admin_email)
        _create_user(member_email, tier_slug="free")
        _delete_all_api_keys()

        member_key_id, _ = _create_key(
            member_email,
            "member laptop key",
            ["courses.read", "users.read"],
            APIKey.Kind.MEMBER,
        )
        _create_key(
            admin_email,
            "staff automation",
            ["settings.read"],
            APIKey.Kind.STAFF,
        )

        context = _auth_context(browser, admin_email)
        page = context.new_page()
        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}",
            wait_until="domcontentloaded",
        )

        # Exactly one row, and it is the staff-kind one.
        expect(page.locator('[data-testid="api-key-row"]')).to_have_count(1)
        body = page.content()
        assert "staff automation" in body
        assert member_email not in body
        assert "member laptop key" not in body
        assert "courses.read" not in body
        assert "users.read" not in body

        # Guessing the member key's id gets a 404, not a silent revocation.
        csrf = next(
            cookie["value"]
            for cookie in context.cookies()
            if cookie["name"] == "csrftoken"
        )
        response = context.request.post(
            f"{django_server}{API_KEYS_URL_PATH}{member_key_id}/revoke/",
            headers={
                "X-CSRFToken": csrf,
                "Referer": f"{django_server}{API_KEYS_URL_PATH}",
            },
            form={},
        )
        assert response.status == 404, response.status
        assert not _key_is_revoked(member_key_id)

        context.close()


@pytest.mark.django_db(transaction=True)
class TestSidebarDiscovery:
    """Scenario: operator finds the page through the sidebar."""

    @browser_journey
    def test_operations_sidebar_lists_api_keys_and_marks_it_current(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys-nav@test.com"
        _create_staff_user(admin_email)

        context = _auth_context(browser, admin_email)
        page = context.new_page()
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # The Operations section body is collapsed (``hidden``) unless it is
        # the active section, so on /studio/ the link is in the DOM but not
        # reachable. Open the section the way an operator would before
        # asserting on the link -- a count-only assertion passes on a
        # display:none node and would hide a genuinely unreachable entry.
        operations_toggle = page.locator(
            '#studio-sidebar-nav [aria-controls="studio-section-operations"]'
        )
        expect(operations_toggle).to_have_count(1)
        nav_link = page.locator('[data-testid="api-keys-nav-link"]')
        expect(nav_link).to_be_hidden()
        operations_toggle.click()

        expect(nav_link).to_be_visible()
        expect(
            page.locator('[data-testid="api-tokens-nav-link"]')
        ).to_be_visible()

        nav_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio/api-keys")
        # On its own page the section is active, so the entry is both
        # visible without a click and marked current.
        landed_link = page.locator('[data-testid="api-keys-nav-link"]')
        expect(landed_link).to_be_visible()
        expect(landed_link).to_have_attribute("aria-current", "page")

        context.close()


@pytest.mark.django_db(transaction=True)
class TestCredentialPagesCrossReference:
    """Scenario: operator on the wrong credential page is not stranded."""

    @browser_journey
    def test_keys_and_tokens_link_to_each_other_in_both_directions(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys-crosslink@test.com"
        _create_staff_user(admin_email)

        context = _auth_context(browser, admin_email)
        page = context.new_page()
        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}",
            wait_until="domcontentloaded",
        )

        header = page.locator('[data-testid="studio-header"]').inner_text()
        assert "/api/v1/" in header

        page.locator('[data-testid="api-keys-cross-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio/api-tokens")
        token_header = page.locator('[data-testid="studio-header"]').inner_text()
        assert "/api/contacts/" in token_header

        page.locator('[data-testid="api-tokens-cross-link"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio/api-keys")

        context.close()


@pytest.mark.django_db(transaction=True)
class TestOperatorFixesARejectedKey:
    """Scenario: a rejected key is corrected, not retyped from scratch."""

    @browser_journey
    def test_invalid_scope_keeps_the_typed_name_and_creates_nothing(
        self, django_server, browser
    ):
        admin_email = "admin-apikeys-form@test.com"
        _create_staff_user(admin_email)
        _delete_all_api_keys()

        context = _auth_context(browser, admin_email)
        page = context.new_page()
        page.goto(
            f"{django_server}{API_KEYS_URL_PATH}new/",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="key-name-input"]').fill("import script")
        page.locator('[data-testid="key-scopes-input"]').fill("Bad Scope!")
        page.locator('[data-testid="api-key-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert f"{API_KEYS_URL_PATH}created/" not in page.url
        error = page.locator('[data-testid="form-error-scopes"]').inner_text()
        assert "Bad Scope!" in error
        expect(page.locator('[data-testid="key-name-input"]')).to_have_value(
            "import script"
        )
        assert _key_count() == 0

        # Correcting only the scope completes the mint.
        page.locator('[data-testid="key-scopes-input"]').fill("settings.read")
        page.locator('[data-testid="api-key-create-submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert f"{API_KEYS_URL_PATH}created/" in page.url
        plaintext = page.locator('[data-testid="api-key-value"]').inner_text().strip()
        assert plaintext.startswith("cb_staff_")
        assert _key_count() == 1

        context.close()


@pytest.mark.django_db(transaction=True)
class TestStaffWithoutSuperuserCannotReachThePage:
    """Scenario: the page is neither advertised nor reachable for staff."""

    @browser_journey
    def test_no_sidebar_entry_no_page_no_revoke(self, django_server, browser):
        from community_base.api.models import APIKey

        staff_email = "staff-only-apikeys@test.com"
        admin_email = "admin-apikeys-guard@test.com"
        _create_staff_user(admin_email)
        _create_staff_only_user(staff_email)
        _delete_all_api_keys()
        key_id, _ = _create_key(
            admin_email, "staff automation", ["settings.read"], APIKey.Kind.STAFF
        )

        context = _auth_context(browser, staff_email)
        page = context.new_page()

        # 1. Not advertised in the sidebar.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        expect(page.locator('[data-testid="api-keys-nav-link"]')).to_have_count(0)

        # 2. Direct navigation is refused and leaks no key metadata.
        response = page.goto(
            f"{django_server}{API_KEYS_URL_PATH}",
            wait_until="domcontentloaded",
        )
        assert response is not None
        assert response.status == 403
        body = page.content()
        assert "staff automation" not in body
        assert admin_email not in body
        assert "settings.read" not in body

        # 3. A direct revoke POST is refused and the key stays active.
        cookies = {cookie["name"]: cookie["value"] for cookie in context.cookies()}
        headers = {"Referer": f"{django_server}{API_KEYS_URL_PATH}"}
        if "csrftoken" in cookies:
            headers["X-CSRFToken"] = cookies["csrftoken"]
        revoke = context.request.post(
            f"{django_server}{API_KEYS_URL_PATH}{key_id}/revoke/",
            headers=headers,
            form={},
        )
        assert revoke.status == 403, revoke.status
        assert not _key_is_revoked(key_id)

        context.close()
