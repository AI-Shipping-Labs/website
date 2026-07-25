"""Studio operator journey: linking an instructor to an account (#1345).

An operator on ``/studio/instructors/?filter=needs_link`` finds an unlinked
instructor that has comment-bearing content, uses the people picker to select
a verified account, and submits — the row then shows the linked account and
the instructor drops out of the unlinked view.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]
SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1345")


def _seed():
    from content.models import Course, Instructor, Module, Unit

    staff = create_user("inst-staff@test.com", is_staff=True)
    dane_account = create_user(
        "dane@test.com", first_name="Dane", email_verified=True,
    )
    member = create_user("inst-member@test.com", first_name="Mel")

    dane = Instructor.objects.create(
        instructor_id="dane", name="Dane", email="dane@test.com",
        status="published",
    )
    course = Course.objects.create(title="C", slug="c-1345", status="published")
    module = Module.objects.create(
        course=course, title="M", slug="m", sort_order=1,
    )
    unit = Unit.objects.create(
        module=module, title="U", slug="u", sort_order=1,
        content_id=uuid.uuid4(),
    )
    course.instructors.add(dane)

    from comments.services import create_comment
    create_comment(content_id=unit.content_id, user=member, body="A question")

    connection.close()
    return staff, dane_account


@pytest.mark.django_db(transaction=True)
def test_operator_links_instructor_from_studio(django_server, browser):
    ensure_tiers()
    staff, dane_account = _seed()
    context = auth_context(browser, staff.email)
    page = context.new_page()

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # needs_link surfaces Dane (unlinked + comment-bearing content).
    page.goto(f"{django_server}/studio/instructors/?filter=needs_link")
    row = page.get_by_test_id("instructor-row").filter(has_text="Dane")
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("instructor-not-linked")).to_be_visible()
    page.screenshot(path=str(SCREENSHOT_DIR / "needs_link.png"))

    # Use the people picker to select the verified account.
    search = row.get_by_test_id("link-dane-search")
    search.click()
    search.fill("dane@test.com")
    suggestion = page.get_by_test_id("link-dane-suggestion").first
    expect(suggestion).to_be_visible()
    suggestion.click()

    # The form auto-submits and redirects back with a success message.
    expect(page).to_have_url(re.compile(r"/studio/instructors/"))
    expect(page.get_by_text("Linked Dane to dane@test.com")).to_be_visible()
    page.screenshot(path=str(SCREENSHOT_DIR / "linked.png"))

    # Dane no longer appears under the unlinked filter.
    page.goto(f"{django_server}/studio/instructors/?filter=unlinked")
    expect(
        page.get_by_test_id("instructor-row").filter(has_text="Dane")
    ).to_have_count(0)
