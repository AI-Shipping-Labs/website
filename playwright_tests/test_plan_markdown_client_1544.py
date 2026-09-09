"""Browser regressions for the shared sprint-plan Markdown fallback (#1544)."""

import datetime
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only

REPO_ROOT = Path(__file__).resolve().parents[1]


def _clear_plan_data():
    from plans.models import (
        Checkpoint,
        Deliverable,
        NextStep,
        Plan,
        Sprint,
        SprintEnrollment,
        Week,
    )

    Checkpoint.objects.all().delete()
    Week.objects.all().delete()
    Deliverable.objects.all().delete()
    NextStep.objects.all().delete()
    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _seed_plan(owner_email, teammate_email="teammate-markdown@test.com"):
    from accounts.models import User
    from plans.models import (
        Checkpoint,
        Deliverable,
        NextStep,
        Plan,
        Sprint,
        SprintEnrollment,
        Week,
    )

    sprint = Sprint.objects.create(
        name="Shared Markdown Sprint",
        slug="shared-markdown-sprint",
        # date-rot-ok: fixed sprint fixture; lifecycle is not under test.
        start_date=datetime.date(2026, 9, 1),
        duration_weeks=2,
    )
    owner = User.objects.get(email=owner_email)
    teammate = User.objects.get(email=teammate_email)
    SprintEnrollment.objects.create(sprint=sprint, user=owner)
    SprintEnrollment.objects.create(sprint=sprint, user=teammate)
    plan = Plan.objects.create(
        member=owner,
        sprint=sprint,
        visibility="cohort",
        goal="Ship the first version",
    )
    Plan.objects.create(member=teammate, sprint=sprint, visibility="private")
    week = Week.objects.create(plan=plan, week_number=1, position=0)
    checkpoint = Checkpoint.objects.create(
        week=week,
        description="Add coverage",
        position=0,
    )
    deliverable = Deliverable.objects.create(
        plan=plan,
        description="Publish demo",
        position=0,
    )
    action = NextStep.objects.create(
        plan=plan,
        description="Choose a repository",
        kind="pre_sprint",
        position=0,
    )
    result = {
        "sprint_slug": sprint.slug,
        "plan_id": plan.pk,
        "checkpoint_id": checkpoint.pk,
        "deliverable_id": deliverable.pk,
        "action_id": action.pk,
    }
    connection.close()
    return result


def _seed_users(owner_email="main-markdown@test.com"):
    _ensure_tiers()
    _clear_plan_data()
    _create_user(owner_email, tier_slug="main", email_verified=True)
    _create_user(
        "teammate-markdown@test.com",
        tier_slug="main",
        email_verified=True,
    )
    return _seed_plan(owner_email)


def _owner_url(django_server, data):
    return (
        f"{django_server}/sprints/{data['sprint_slug']}"
        f"/plan/{data['plan_id']}"
    )


def _teammate_url(django_server, data):
    return (
        f"{django_server}/sprints/{data['sprint_slug']}"
        f"/plans/{data['plan_id']}"
    )


class TestSharedPlanMarkdownModule:
    @browser_journey
    def test_renders_supported_markdown_and_keeps_unsafe_values_inert(self, browser):
        context = browser.new_context()
        page = context.new_page()
        page.set_content('<div id="output"></div>')
        page.add_script_tag(
            path=str(REPO_ROOT / "static/js/plans/markdown.js"),
        )

        result = page.evaluate(
            """
            () => {
              const source = [
                '**bold**',
                '',
                '- first',
                '- `second`',
                '',
                '```',
                '<script>window.__planMarkdownExecuted = true</script>',
                '```',
                '',
                '[safe](https://example.com/docs)',
                '[bad](javascript:alert(1))',
                '[quote](https://example.com/\"onmouseover=alert)',
              ].join('\\n');
              const output = document.getElementById('output');
              output.innerHTML = window.SprintPlanMarkdown.renderMarkdown(source);
              return {
                html: output.innerHTML,
                strong: output.querySelector('strong')?.textContent,
                list: Array.from(output.querySelectorAll('li')).map((item) => item.textContent),
                code: Array.from(output.querySelectorAll('code')).map((item) => item.textContent),
                safeHref: output.querySelector('a')?.getAttribute('href'),
                quoteHref: output.querySelectorAll('a')[1]?.getAttribute('href'),
                unsafeHrefs: Array.from(output.querySelectorAll('a')).filter(
                  (link) => /^(?:javascript|data):/i.test(link.getAttribute('href') || '')
                ).length,
                scripts: output.querySelectorAll('script').length,
                executed: window.__planMarkdownExecuted === true,
                onmouseover: output.querySelector('[onmouseover]') !== null,
              };
            }
            """
        )

        assert result["strong"] == "bold"
        assert result["list"] == ["first", "second"]
        assert "<script>window.__planMarkdownExecuted = true</script>" in result["code"]
        assert result["safeHref"] == "https://example.com/docs"
        assert result["unsafeHrefs"] == 0
        assert result["scripts"] == 0
        assert result["executed"] is False
        assert result["onmouseover"] is False
        assert result["quoteHref"] == "https://example.com/&quot;onmouseover=alert"
        assert "&amp;quot;" in result["html"]
        context.close()


@pytest.mark.django_db(transaction=True)
class TestMemberPlanMarkdownEdits:
    @browser_journey
    def test_pre_sprint_action_matches_server_render_after_reload(
        self, django_server, browser,
    ):
        data = _seed_users()
        context = _auth_context(browser, "main-markdown@test.com")
        page = context.new_page()
        page.goto(_owner_url(django_server, data), wait_until="domcontentloaded")

        action = page.get_by_test_id("plan-next-step").first
        action.get_by_test_id("plan-item-edit").click()
        action.get_by_test_id("plan-item-markdown-input").fill(
            "Ship **docs** and a [repo](https://github.com/example/plan)",
        )
        with page.expect_response("**/api/next-steps/*"):
            action.get_by_test_id("plan-item-save").click()

        expect(action.locator("strong")).to_have_text("docs")
        expect(action.locator("a")).to_have_attribute(
            "href", "https://github.com/example/plan",
        )
        screenshot_path = (
            REPO_ROOT / ".tmp" / "issue-1544" / "member-action-markdown.png"
        )
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        action.screenshot(path=str(screenshot_path))
        page.reload(wait_until="domcontentloaded")
        action = page.get_by_test_id("plan-next-step").first
        expect(action.locator("strong")).to_have_text("docs")
        expect(action.locator("a")).to_have_attribute(
            "href", "https://github.com/example/plan",
        )
        context.close()

    @browser_journey
    def test_client_rerender_does_not_execute_script_or_javascript_link(
        self, django_server, browser,
    ):
        data = _seed_users()
        context = _auth_context(browser, "main-markdown@test.com")
        page = context.new_page()
        dialogs = []
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        page.goto(_owner_url(django_server, data), wait_until="domcontentloaded")

        deliverable = page.get_by_test_id("plan-deliverable").first
        deliverable.get_by_test_id("plan-item-edit").click()
        deliverable.get_by_test_id("plan-item-markdown-input").fill(
            '<script>window.__planMarkdownExecuted = true</script> '
            '[click](javascript:alert(1))',
        )
        with page.expect_response("**/api/deliverables/*"):
            deliverable.get_by_test_id("plan-item-save").click()

        expect(deliverable.locator("script")).to_have_count(0)
        expect(deliverable.locator('a[href^="javascript:"]')).to_have_count(0)
        assert page.evaluate("window.__planMarkdownExecuted === true") is False
        assert dialogs == []
        context.close()

    @browser_journey
    def test_goal_markdown_is_visible_to_owner_and_teammate(
        self, django_server, browser,
    ):
        data = _seed_users()
        owner_context = _auth_context(browser, "main-markdown@test.com")
        owner_page = owner_context.new_page()
        owner_page.goto(
            _owner_url(django_server, data),
            wait_until="domcontentloaded",
        )

        owner_page.get_by_test_id("plan-goal-edit").click()
        owner_page.get_by_test_id("plan-goal-input").fill(
            "Ship a **RAG** prototype",
        )
        with owner_page.expect_response("**/goal"):
            owner_page.get_by_test_id("plan-goal-save").click()
        expect(owner_page.get_by_test_id("plan-goal-text").locator("strong")).to_have_text(
            "RAG",
        )
        expect(owner_page.get_by_test_id("plan-goal-status")).to_have_text("Saved")

        teammate_context = _auth_context(browser, "teammate-markdown@test.com")
        teammate_page = teammate_context.new_page()
        teammate_page.goto(
            _teammate_url(django_server, data),
            wait_until="domcontentloaded",
        )
        expect(
            teammate_page.get_by_test_id("plan-goal-text").locator("strong"),
        ).to_have_text("RAG")
        expect(teammate_page.get_by_test_id("plan-goal-edit")).to_have_count(0)
        teammate_context.close()
        owner_context.close()

    @browser_journey
    def test_checkpoint_save_prefers_server_description_html(
        self, django_server, browser,
    ):
        data = _seed_users()
        context = _auth_context(browser, "main-markdown@test.com")
        page = context.new_page()

        def _supply_canonical_html(route):
            response = route.fetch()
            payload = response.json()
            payload["description_html"] = (
                '<p data-testid="server-checkpoint-html">Canonical server HTML</p>'
            )
            route.fulfill(
                response=response,
                body=json.dumps(payload),
                content_type="application/json",
            )

        page.route("**/api/checkpoints/*", _supply_canonical_html)
        page.goto(_owner_url(django_server, data), wait_until="domcontentloaded")
        checkpoint = page.get_by_test_id("plan-checkpoint").first
        checkpoint.locator("[data-checkpoint-text]").click()
        checkpoint.get_by_test_id("plan-item-markdown-input").fill(
            "Use a [link](https://example.com)",
        )
        checkpoint.get_by_test_id("plan-item-save").click()

        expect(checkpoint.get_by_test_id("server-checkpoint-html")).to_have_text(
            "Canonical server HTML",
        )
        context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioPlanMarkdownEdit:
    @browser_journey
    def test_staff_checkpoint_markdown_matches_after_reload(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_plan_data()
        _create_staff_user("staff-markdown@test.com")
        _create_user(
            "member-markdown@test.com",
            tier_slug="main",
            email_verified=True,
        )
        _create_user(
            "teammate-markdown@test.com",
            tier_slug="main",
            email_verified=True,
        )
        data = _seed_plan("member-markdown@test.com")
        context = _auth_context(browser, "staff-markdown@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/plans/{data['plan_id']}/edit/",
            wait_until="domcontentloaded",
        )
        page.get_by_test_id("plan-editor").wait_for(state="visible")

        checkpoint = page.get_by_test_id("checkpoint-chip").first
        checkpoint.get_by_test_id("checkpoint-text").click()
        page.get_by_test_id("checkpoint-edit-textarea").fill(
            "Add `eval` coverage",
        )
        with page.expect_response("**/api/checkpoints/*"):
            page.get_by_test_id("summary-goal").click()
        expect(checkpoint.locator("code")).to_have_text("eval")

        page.reload(wait_until="domcontentloaded")
        checkpoint = page.get_by_test_id("checkpoint-chip").first
        expect(checkpoint.locator("code")).to_have_text("eval")
        context.close()
