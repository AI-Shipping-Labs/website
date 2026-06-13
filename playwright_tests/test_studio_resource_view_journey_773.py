"""End-to-end coverage for the per-user resource-view timeline (issue #773).

Staff reading a member's free->paid journey on the CRM user-detail page:
- pre-upgrade browsing rows + an "Upgraded to paid" marker at the first
  payment, click-through to the public resource;
- a member's live article view recorded + deduped on reload;
- anonymous browsing leaving no per-user trail;
- a paywalled teaser NOT counting as a view;
- the empty state with no upgrade marker.

Usage:
    uv run pytest playwright_tests/test_studio_resource_view_journey_773.py -v
"""

import os
from datetime import date, timedelta

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
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _user_id_for(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


def _make_article(slug, title, required_level=0):
    from content.models import Article

    article, _ = Article.objects.get_or_create(
        slug=slug,
        defaults={
            "title": title,
            "date": date(2026, 1, 1),
            "published": True,
            "required_level": required_level,
        },
    )
    article.title = title
    article.published = True
    article.required_level = required_level
    article.save()
    connection.close()
    return article


def _add_activity(email, event_type, label, target_url="", minutes_ago=0):
    from accounts.models import User
    from analytics.models import UserActivity

    user = User.objects.get(email=email)
    row = UserActivity.objects.create(
        user=user,
        event_type=event_type,
        label=label,
        target_url=target_url,
        occurred_at=timezone.now() - timedelta(minutes=minutes_ago),
    )
    connection.close()
    return row


def _resource_view_count(email, object_id):
    from accounts.models import User
    from analytics.models import UserActivity

    user = User.objects.get(email=email)
    count = UserActivity.objects.filter(
        user=user,
        event_type=UserActivity.EVENT_RESOURCE_VIEW,
        object_id=object_id,
    ).count()
    connection.close()
    return count


@pytest.mark.django_db(transaction=True)
class TestPreUpgradeJourney:
    def test_staff_sees_pre_upgrade_journey_with_marker(
        self, django_server, browser,
    ):
        from analytics.models import UserActivity

        _ensure_tiers()
        staff_email = "rv773-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("journey773@test.com")
        _make_article("article-a-773", "Alpha Article")

        # Pre-upgrade browsing (older), then payment, then post-upgrade view.
        _add_activity(
            "journey773@test.com", UserActivity.EVENT_RESOURCE_VIEW,
            "Viewed article: Alpha Article",
            target_url="/blog/article-a-773", minutes_ago=60,
        )
        _add_activity(
            "journey773@test.com", UserActivity.EVENT_RESOURCE_VIEW,
            "Viewed project: Bravo Project", minutes_ago=50,
        )
        _add_activity(
            "journey773@test.com", UserActivity.EVENT_RESOURCE_VIEW,
            "Viewed recording: Charlie Recording", minutes_ago=40,
        )
        _add_activity(
            "journey773@test.com", UserActivity.EVENT_PAYMENT,
            "Payment: Main", minutes_ago=30,
        )
        _add_activity(
            "journey773@test.com", UserActivity.EVENT_RESOURCE_VIEW,
            "Viewed article: Delta Article", minutes_ago=10,
        )
        member_pk = _user_id_for("journey773@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        section = page.locator('[data-testid="user-activity-section"]')
        assert section.count() == 1

        marker = page.locator(
            '[data-testid="user-activity-upgrade-marker"]',
        )
        assert marker.count() == 1
        # The marker uses a CSS uppercase transform, so inner_text() returns
        # "UPGRADED TO PAID"; compare case-insensitively.
        assert "upgraded to paid" in marker.inner_text().lower()

        # All four browsing rows + the post-upgrade one render.
        body = section.inner_text()
        assert "Viewed article: Alpha Article" in body
        assert "Viewed project: Bravo Project" in body
        assert "Viewed recording: Charlie Recording" in body
        assert "Viewed article: Delta Article" in body

        # Click-through to the public resource the member read.
        page.get_by_role(
            "link", name="Viewed article: Alpha Article",
        ).click()
        page.wait_for_load_state("domcontentloaded")
        assert "/blog/article-a-773" in page.url

        context.close()

    def test_empty_member_has_no_marker(self, django_server, browser):
        _ensure_tiers()
        staff_email = "rv773-empty-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("empty773@test.com")
        # Clear the signup row so the empty state shows.
        from analytics.models import UserActivity

        UserActivity.objects.filter(
            user__email="empty773@test.com",
        ).delete()
        connection.close()
        member_pk = _user_id_for("empty773@test.com")

        context = _auth_context(browser, staff_email)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )

        assert page.locator(
            '[data-testid="user-activity-empty"]',
        ).count() == 1
        assert page.locator(
            '[data-testid="user-activity-upgrade-marker"]',
        ).count() == 0

        context.close()


@pytest.mark.django_db(transaction=True)
class TestLiveBrowsingRecorded:
    def test_member_view_recorded_and_deduped(self, django_server, browser):
        _ensure_tiers()
        staff_email = "rv773-live-admin@test.com"
        _create_staff_user(staff_email)
        _create_user("live773@test.com", tier_slug="main")
        _make_article("open-live-773", "Open Live Article")

        # Member browses the article twice (reload deduped).
        member_ctx = _auth_context(browser, "live773@test.com")
        member_page = member_ctx.new_page()
        member_page.goto(
            f"{django_server}/blog/open-live-773",
            wait_until="domcontentloaded",
        )
        member_page.goto(
            f"{django_server}/blog/open-live-773",
            wait_until="domcontentloaded",
        )
        member_ctx.close()

        assert _resource_view_count("live773@test.com", "open-live-773") == 1

        # Staff sees exactly one "Viewed" row for it.
        member_pk = _user_id_for("live773@test.com")
        staff_ctx = _auth_context(browser, staff_email)
        staff_page = staff_ctx.new_page()
        staff_page.goto(
            f"{django_server}/studio/users/{member_pk}/",
            wait_until="domcontentloaded",
        )
        rows = staff_page.get_by_role(
            "link", name="Viewed article: Open Live Article",
        )
        assert rows.count() == 1
        staff_ctx.close()


@pytest.mark.django_db(transaction=True)
class TestAnonymousLeavesNoTrail:
    def test_anonymous_view_records_nothing(self, django_server, browser):
        from analytics.models import UserActivity

        _ensure_tiers()
        _make_article("anon-773", "Anon Article")

        context = browser.new_context()
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/anon-773",
            wait_until="domcontentloaded",
        )
        context.close()

        count = UserActivity.objects.filter(
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_id="anon-773",
        ).count()
        connection.close()
        assert count == 0


@pytest.mark.django_db(transaction=True)
class TestPaywalledTeaserExcluded:
    def test_gated_teaser_not_recorded(self, django_server, browser):
        _ensure_tiers()
        staff_email = "rv773-gate-admin@test.com"
        _create_staff_user(staff_email)
        # Free member without access to a Main-gated article.
        _create_user("gated773@test.com", tier_slug="free")
        _make_article("gated-773", "Gated Article", required_level=20)

        member_ctx = _auth_context(browser, "gated773@test.com")
        member_page = member_ctx.new_page()
        member_page.goto(
            f"{django_server}/blog/gated-773",
            wait_until="domcontentloaded",
        )
        member_ctx.close()

        assert _resource_view_count("gated773@test.com", "gated-773") == 0
