"""Playwright coverage for admin device login flow."""

import json
import os
import urllib.error
import urllib.request

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.local_only,
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
]


def _start_device_flow(base_url: str, label: str) -> dict:
    req = urllib.request.Request(
        f"{base_url}/api/auth/device",
        data=json.dumps({"label": label}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _poll_token(base_url: str, device_code: str) -> tuple[int, dict]:
    from community_base.device_login.codes import sha256_hex
    from community_base.device_login.models import DeviceGrant

    DeviceGrant.objects.filter(device_code_hash=sha256_hex(device_code)).update(last_poll_at=None)
    req = urllib.request.Request(
        f"{base_url}/api/auth/device/token",
        data=json.dumps({"device_code": device_code}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _assert_approved_token_is_single_use(base_url: str, device_code: str, email: str):
    status, credential = _poll_token(base_url, device_code)
    assert status == 200
    assert "token" in credential
    token = credential["token"]

    req = urllib.request.Request(
        f"{base_url}/api/whoami",
        headers={"Authorization": f"Token {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        whoami = json.loads(resp.read().decode("utf-8"))
        assert whoami["email"] == email

    # Single-use check
    status, second_poll = _poll_token(base_url, device_code)
    assert second_poll.get("error") == "expired_token"


@browser_journey
def test_staff_device_login_browser_flow(django_server, browser):
    email = "device-admin-pw@test.com"
    _create_staff_user(email)
    offer = _start_device_flow(django_server, "pw-agent@aisl")
    user_code = offer["user_code"]
    device_code = offer["device_code"]

    context = _auth_context(browser, email)
    page = context.new_page()

    page.goto(f"{django_server}/device?code={user_code}", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Authorize this machine?")).to_be_visible()
    expect(page.get_by_text("pw-agent@aisl")).to_be_visible()

    page.get_by_role("button", name="Authorize").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("status")).to_contain_text("Sign-in approved")

    _assert_approved_token_is_single_use(django_server, device_code, email)
    context.close()


@browser_journey
def test_non_staff_device_login_refusal(django_server, browser):
    email = "device-member-pw@test.com"
    _create_user(email)
    offer = _start_device_flow(django_server, "unauthorized@aisl")
    user_code = offer["user_code"]
    device_code = offer["device_code"]

    context = _auth_context(browser, email)
    page = context.new_page()

    page.goto(f"{django_server}/device?code={user_code}", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Authorize this machine?")).to_be_visible()
    expect(
        page.get_by_role("region", name="Authorize this machine?").get_by_role("alert")
    ).to_contain_text("Only admins can approve a device login.")
    expect(page.get_by_role("button", name="Authorize")).to_have_count(0)

    status, poll_result = _poll_token(django_server, device_code)
    assert poll_result.get("error") == "authorization_pending"
    context.close()


@browser_journey
def test_anonymous_device_page_redirects_to_login(django_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{django_server}/device", wait_until="domcontentloaded")
    assert "/accounts/login/" in page.url or "/login" in page.url
    context.close()
