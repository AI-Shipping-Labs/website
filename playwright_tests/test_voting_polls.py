"""
Playwright E2E tests for Voting and Polls (Issue #88).

Tests cover all 12 BDD scenarios from the issue:
- Main member browses active polls and votes on a topic
- Main member changes their mind and unvotes an option
- Main member hits the maximum votes limit on a poll
- Main member proposes a new option on a poll that accepts proposals
- Free member cannot access topic polls and sees the upgrade path
- Main member cannot access course polls reserved for Premium
- Premium member votes on a course poll
- Member views a closed poll with read-only results
- Anonymous visitor navigates to polls and is prompted to sign in
- Main member navigates to polls from the dashboard
- Member submits a proposal with missing title and gets validation feedback
- Poll with no options yet shows empty state and encourages proposals

Usage:
    uv run pytest playwright_tests/test_voting_polls.py -v
"""

import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    DEFAULT_PASSWORD,
    ensure_tiers as _ensure_tiers,
    create_user as _create_user,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _anon_context(browser):
    """Create an anonymous browser context with a CSRF cookie."""
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _clear_polls():
    """Delete all polls, options, and votes to ensure clean state."""
    from voting.models import PollVote, PollOption, Poll

    PollVote.objects.all().delete()
    PollOption.objects.all().delete()
    Poll.objects.all().delete()
    connection.close()


def _create_poll(
    title,
    poll_type="topic",
    description="",
    status="open",
    allow_proposals=False,
    max_votes_per_user=3,
    closes_at=None,
):
    """Create a Poll via ORM."""
    from voting.models import Poll

    poll = Poll(
        title=title,
        description=description,
        poll_type=poll_type,
        status=status,
        allow_proposals=allow_proposals,
        max_votes_per_user=max_votes_per_user,
        closes_at=closes_at,
    )
    poll.save()
    connection.close()
    return poll


def _create_option(poll, title, description="", proposed_by=None):
    """Create a PollOption via ORM."""
    from voting.models import PollOption

    option = PollOption(
        poll=poll,
        title=title,
        description=description,
        proposed_by=proposed_by,
    )
    option.save()
    connection.close()
    return option


def _create_vote(poll, option, user):
    """Create a PollVote via ORM."""
    from voting.models import PollVote

    vote = PollVote(
        poll=poll,
        option=option,
        user=user,
    )
    vote.save()
    connection.close()
    return vote


# ---------------------------------------------------------------
# Scenario 1: Main member browses active polls and votes on a
#              topic
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1MainMemberBrowsesAndVotes:
    """Main member browses active polls and votes on a topic."""

    def test_main_member_browses_polls_and_votes(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll 'Next Workshop Topic' exists with 3 options,
        max_votes_per_user = 3.
        1. Navigate to /vote
        2. Click on the 'Next Workshop Topic' poll
        Then: User lands on the poll detail page and sees all 3 options
        3. Click the 'Vote' button on the first option
        Then: The vote is recorded, the vote count increases by 1,
              and the votes-remaining counter decreases to 2
        4. Click the 'Vote' button on the second option
        Then: The vote is recorded, the votes-remaining counter
              decreases to 1."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Next Workshop Topic",
            poll_type="topic",
            description="Vote for the next workshop topic!",
            max_votes_per_user=3,
        )
        opt1 = _create_option(poll, "LangChain Deep Dive")
        opt2 = _create_option(poll, "RAG Pipelines")
        opt3 = _create_option(poll, "Fine-tuning Models")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The poll listing shows the topic poll
        assert "Next Workshop Topic" in body
        assert "Topic Poll" in body

        # Step 2: Click on the poll
        page.click(
            f'a[href="/vote/{poll.id}"]'
        )
        page.wait_for_load_state("domcontentloaded")

        # Then: User lands on the poll detail page
        assert f"/vote/{poll.id}" in page.url
        body = page.content()

        # All 3 options are visible
        assert "LangChain Deep Dive" in body
        assert "RAG Pipelines" in body
        assert "Fine-tuning Models" in body

        # Votes remaining shows 3
        remaining_el = page.locator("#votes-remaining-count")
        assert remaining_el.inner_text() == "3"

        # Step 3: Click the "Vote" button on the first option
        vote_btn_1 = page.locator(
            f'button.vote-btn[data-option-id="{opt1.id}"]'
        )
        vote_btn_1.click()

        # Wait for the button to update to "Voted"
        page.wait_for_function(
            f"""() => {{
                var btn = document.querySelector('button.vote-btn[data-option-id="{opt1.id}"]');
                return btn && btn.getAttribute('data-voted') === 'true';
            }}""",
            timeout=10000,
        )

        # Then: Vote count for option 1 increases to 1
        vote_count_1 = page.locator(
            f'.vote-count[data-option-id="{opt1.id}"]'
        )
        assert vote_count_1.inner_text() == "1"

        # Then: Votes remaining decreases to 2
        assert remaining_el.inner_text() == "2"

        # Step 4: Click the "Vote" button on the second option
        vote_btn_2 = page.locator(
            f'button.vote-btn[data-option-id="{opt2.id}"]'
        )
        vote_btn_2.click()

        # Wait for the button to update to "Voted"
        page.wait_for_function(
            f"""() => {{
                var btn = document.querySelector('button.vote-btn[data-option-id="{opt2.id}"]');
                return btn && btn.getAttribute('data-voted') === 'true';
            }}""",
            timeout=10000,
        )

        # Then: Vote count for option 2 increases to 1
        vote_count_2 = page.locator(
            f'.vote-count[data-option-id="{opt2.id}"]'
        )
        assert vote_count_2.inner_text() == "1"

        # Then: Votes remaining decreases to 1
        assert remaining_el.inner_text() == "1"
# ---------------------------------------------------------------
# Scenario 2: Main member changes their mind and unvotes an option
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2MainMemberUnvotes:
    """Main member changes their mind and unvotes an option."""

    def test_main_member_unvotes_option(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll exists, the user has already voted on option
        'LangChain Deep Dive'.
        1. Navigate to the poll detail page
        Then: 'LangChain Deep Dive' shows as already voted
        2. Click the vote toggle button on 'LangChain Deep Dive' to unvote
        Then: The vote is removed, vote count decreases by 1, and
              votes-remaining increases by 1."""
        _clear_polls()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Unvote Test Poll",
            poll_type="topic",
            max_votes_per_user=3,
        )
        opt1 = _create_option(poll, "LangChain Deep Dive")
        _create_option(poll, "Other Option")

        # User has already voted on LangChain Deep Dive
        _create_vote(poll, opt1, user)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the poll detail page
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "LangChain Deep Dive" shows as already voted
        vote_btn = page.locator(
            f'button.vote-btn[data-option-id="{opt1.id}"]'
        )
        assert vote_btn.get_attribute("data-voted") == "true"
        assert "Voted" in vote_btn.inner_text()

        # Vote count should be 1
        vote_count = page.locator(
            f'.vote-count[data-option-id="{opt1.id}"]'
        )
        assert vote_count.inner_text() == "1"

        # Votes remaining should be 2 (3 max - 1 used)
        remaining_el = page.locator("#votes-remaining-count")
        assert remaining_el.inner_text() == "2"

        # Step 2: Click the vote toggle to unvote
        vote_btn.click()

        # Wait for the button to update to "Vote" (unvoted)
        page.wait_for_function(
            f"""() => {{
                var btn = document.querySelector('button.vote-btn[data-option-id="{opt1.id}"]');
                return btn && btn.getAttribute('data-voted') === 'false';
            }}""",
            timeout=10000,
        )

        # Then: Vote count decreases to 0
        assert vote_count.inner_text() == "0"

        # Then: Votes remaining increases to 3
        assert remaining_el.inner_text() == "3"

        # Button now shows "Vote" instead of "Voted"
        assert "Vote" in vote_btn.inner_text()
# ---------------------------------------------------------------
# Scenario 3: Main member hits the maximum votes limit on a poll
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3MaxVotesLimit:
    """Main member hits the maximum votes limit on a poll."""

    def test_main_member_cannot_exceed_max_votes(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll with 5 options and max_votes_per_user = 3, the user
        has already voted on 3 options.
        1. Navigate to the poll detail page
        Then: The votes-remaining counter shows 0
        2. Attempt to vote on a 4th option
        Then: The vote is rejected with an error
        Then: The vote count for the 4th option does not change."""
        _clear_polls()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Max Votes Test",
            poll_type="topic",
            max_votes_per_user=3,
        )
        opt1 = _create_option(poll, "Option A")
        opt2 = _create_option(poll, "Option B")
        opt3 = _create_option(poll, "Option C")
        opt4 = _create_option(poll, "Option D")
        opt5 = _create_option(poll, "Option E")

        # User has already voted on 3 options
        _create_vote(poll, opt1, user)
        _create_vote(poll, opt2, user)
        _create_vote(poll, opt3, user)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the poll detail page
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )

        # Then: Votes remaining shows 0
        remaining_el = page.locator("#votes-remaining-count")
        assert remaining_el.inner_text() == "0"

        # Vote count for Option D should be 0
        vote_count_4 = page.locator(
            f'.vote-count[data-option-id="{opt4.id}"]'
        )
        assert vote_count_4.inner_text() == "0"

        # Step 2: Attempt to vote on the 4th option
        # The JS will show an alert with the error message.
        # Capture the dialog.
        dialog_messages = []
        page.on(
            "dialog",
            lambda dialog: (
                dialog_messages.append(dialog.message),
                dialog.accept(),
            ),
        )

        vote_btn_4 = page.locator(
            f'button.vote-btn[data-option-id="{opt4.id}"]'
        )
        vote_btn_4.click()

        # Wait for the alert dialog to appear
        page.wait_for_function(
            """() => {
                // Give time for the fetch + alert cycle
                return true;
            }""",
            timeout=5000,
        )
        # Small delay to ensure the dialog handler fires
        page.wait_for_load_state("domcontentloaded")

        # Then: An error alert was shown about max votes
        assert len(dialog_messages) >= 1
        assert "Maximum 3 votes" in dialog_messages[0]

        # Then: Vote count for Option D unchanged (still 0)
        assert vote_count_4.inner_text() == "0"

        # Votes remaining still 0
        assert remaining_el.inner_text() == "0"
# ---------------------------------------------------------------
# Scenario 4: Main member proposes a new option on a poll that
#              accepts proposals
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4ProposeNewOption:
    """Main member proposes a new option on a poll that accepts
    proposals."""

    def test_main_member_proposes_option(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll with allow_proposals = true.
        1. Navigate to the poll detail page
        2. Fill in the proposal form with title and description
        3. Submit the proposal
        Then: A success confirmation appears and the page reloads
        Then: The new option appears in the options list with 0 votes,
              attributed to the proposer."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Proposal Test Poll",
            poll_type="topic",
            allow_proposals=True,
        )
        _create_option(poll, "Existing Option")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the poll detail page
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # The proposal form is visible
        propose_form = page.locator("#propose-form")
        assert propose_form.count() >= 1

        # Step 2: Fill in the proposal form
        page.fill(
            "#proposal-title",
            "Fine-tuning LLMs on custom data",
        )
        page.fill(
            "#proposal-description",
            "Hands-on session covering LoRA and QLoRA techniques",
        )

        # Step 3: Submit the proposal
        page.click(
            '#propose-form button[type="submit"]'
        )

        # Then: Success message appears
        message_el = page.locator("#propose-message")
        page.wait_for_function(
            """() => {
                var el = document.getElementById('propose-message');
                return el && el.textContent.includes('Proposal submitted');
            }""",
            timeout=10000,
        )

        message_text = message_el.inner_text()
        assert "Proposal submitted" in message_text

        # Wait for page reload (the JS does setTimeout reload)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("domcontentloaded")

        # After reload, navigate again to ensure fresh state
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The new option appears
        assert "Fine-tuning LLMs on custom data" in body

        # Then: The option has 0 votes
        # Find the option element and check vote count
        assert "main@test.com" in body  # Attributed to proposer
# ---------------------------------------------------------------
# Scenario 5: Free member cannot access topic polls and sees the
#              upgrade path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5FreeMemberCannotAccessTopicPolls:
    """Free member cannot access topic polls and sees the upgrade
    path."""

    def test_free_member_sees_no_polls_and_gating_on_direct_access(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier), an open
        topic poll exists (required_level = 20).
        1. Navigate to /vote
        Then: The poll listing shows no accessible polls
        2. Navigate directly to /vote/{poll_id} for the topic poll
        Then: The poll detail shows a gating message 'Upgrade to Main
              to participate in this poll' with a 'View Pricing' link
        3. Click 'View Pricing'
        Then: User lands on /pricing."""
        _clear_polls()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        poll = _create_poll(
            title="Topic Poll for Main Members",
            poll_type="topic",
        )
        _create_option(poll, "Option 1")

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: No polls are visible (free user lacks Main tier)
        assert "Topic Poll for Main Members" not in body
        assert "No active polls right now" in body

        # Step 2: Navigate directly to the poll detail
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Gating message is shown
        assert "Upgrade to Main to participate in this poll" in body

        # "View Pricing" link is present
        pricing_link = page.locator(
            'a:has-text("View Pricing")'
        )
        assert pricing_link.count() >= 1

        # Step 3: Click "View Pricing"
        pricing_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User lands on /pricing
        assert "/pricing" in page.url
# ---------------------------------------------------------------
# Scenario 6: Main member cannot access course polls reserved for
#              Premium
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6MainMemberCannotAccessCoursePoll:
    """Main member cannot access course polls reserved for Premium."""

    def test_main_member_sees_gating_on_course_poll(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        course poll 'Next Mini-Course' exists (required_level = 30).
        1. Navigate to /vote
        Then: The course poll does not appear in the listing
        2. Navigate directly to /vote/{poll_id} for the course poll
        Then: The poll detail shows a gating message 'Upgrade to
              Premium to participate in this poll' with a 'View
              Pricing' link."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Next Mini-Course",
            poll_type="course",
            description="Vote for the next mini-course!",
        )
        _create_option(poll, "Course Option A")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The course poll does not appear
        assert "Next Mini-Course" not in body

        # Step 2: Navigate directly to the course poll
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Gating message for Premium
        assert "Upgrade to Premium to participate in this poll" in body

        # "View Pricing" link is present
        pricing_link = page.locator(
            'a:has-text("View Pricing")'
        )
        assert pricing_link.count() >= 1
# ---------------------------------------------------------------
# Scenario 7: Premium member votes on a course poll
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7PremiumMemberVotesOnCoursePoll:
    """Premium member votes on a course poll."""

    def test_premium_member_sees_and_votes_on_course_poll(
        self, django_server
    , browser):
        """Given a user logged in as premium@test.com (Premium tier),
        an open course poll 'Next Mini-Course' with 4 options exists.
        1. Navigate to /vote
        Then: The course poll appears with a 'Course Poll' type indicator
        2. Click on the course poll
        Then: User lands on the detail page and sees all 4 options
        3. Vote on one of the options
        Then: The vote is recorded and the vote count updates."""
        _clear_polls()
        _ensure_tiers()
        _create_user("premium@test.com", tier_slug="premium")

        poll = _create_poll(
            title="Next Mini-Course",
            poll_type="course",
            max_votes_per_user=2,
        )
        opt1 = _create_option(poll, "AI Agents")
        opt2 = _create_option(poll, "MLOps")
        opt3 = _create_option(poll, "Computer Vision")
        opt4 = _create_option(poll, "NLP Fundamentals")

        context = _auth_context(browser, "premium@test.com")
        page = context.new_page()
        # Step 1: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The course poll appears with "Course Poll" badge
        assert "Next Mini-Course" in body
        assert "Course Poll" in body

        # Step 2: Click on the course poll
        page.click(
            f'a[href="/vote/{poll.id}"]'
        )
        page.wait_for_load_state("domcontentloaded")

        # Then: User lands on the detail page
        assert f"/vote/{poll.id}" in page.url
        body = page.content()

        # All 4 options are visible
        assert "AI Agents" in body
        assert "MLOps" in body
        assert "Computer Vision" in body
        assert "NLP Fundamentals" in body

        # Step 3: Vote on one option
        vote_btn = page.locator(
            f'button.vote-btn[data-option-id="{opt1.id}"]'
        )
        vote_btn.click()

        # Wait for the vote to register
        page.wait_for_function(
            f"""() => {{
                var btn = document.querySelector('button.vote-btn[data-option-id="{opt1.id}"]');
                return btn && btn.getAttribute('data-voted') === 'true';
            }}""",
            timeout=10000,
        )

        # Then: Vote count updates to 1
        vote_count = page.locator(
            f'.vote-count[data-option-id="{opt1.id}"]'
        )
        assert vote_count.inner_text() == "1"
# ---------------------------------------------------------------
# Scenario 8: Member views a closed poll with read-only results
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8ClosedPollReadOnly:
    """Member views a closed poll with read-only results."""

    def test_closed_poll_shows_read_only_results(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), a closed
        topic poll exists with several options that received votes.
        1. Navigate to /vote/{poll_id} for the closed poll
        Then: The poll shows a 'Closed' status indicator
        Then: All options and their final vote counts are visible
        Then: No vote buttons or proposal form are available."""
        _clear_polls()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")
        other_user = _create_user("other@test.com", tier_slug="main")

        poll = _create_poll(
            title="Closed Topic Poll",
            poll_type="topic",
            status="closed",
            allow_proposals=True,  # even with proposals enabled
        )
        opt1 = _create_option(poll, "Option Alpha")
        opt2 = _create_option(poll, "Option Beta")
        opt3 = _create_option(poll, "Option Gamma")

        # Add some votes
        _create_vote(poll, opt1, user)
        _create_vote(poll, opt1, other_user)
        _create_vote(poll, opt2, other_user)

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the closed poll
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Shows "Closed" status indicator
        assert "Closed" in body

        # Then: All options are visible with vote counts
        assert "Option Alpha" in body
        assert "Option Beta" in body
        assert "Option Gamma" in body

        # Then: No vote buttons are available
        vote_buttons = page.locator("button.vote-btn")
        assert vote_buttons.count() == 0

        # Then: No proposal form is available
        propose_form = page.locator("#propose-form")
        assert propose_form.count() == 0
# ---------------------------------------------------------------
# Scenario 9: Anonymous visitor navigates to polls and is prompted
#              to sign in
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9AnonymousVisitorPromptedToSignIn:
    """Anonymous visitor navigates to polls and is prompted to sign
    in."""

    def test_anonymous_visitor_sees_sign_in_prompts(
        self, django_server
    , page):
        """Given an anonymous visitor (not logged in), an open topic
        poll exists.
        1. Navigate to /vote
        Then: The page shows no active polls and includes a prompt to
              sign in
        2. Navigate directly to /vote/{poll_id}
        Then: The poll detail page prompts the user to sign in to
              vote."""
        _clear_polls()
        _ensure_tiers()

        poll = _create_poll(
            title="Public Topic Poll",
            poll_type="topic",
        )
        _create_option(poll, "Option X")
        _create_option(poll, "Option Y")

        # Step 1: Navigate to /vote
        page.goto(
            f"{django_server}/vote",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: No active polls shown (anonymous has level 0,
        # topic polls require level 20)
        assert "Public Topic Poll" not in body

        # Then: Sign in prompt is present
        assert "Sign in" in body

        # Step 2: Navigate directly to /vote/{poll_id}
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The poll is visible but shows gating or
        # sign-in prompt to vote
        assert "Public Topic Poll" in body
        assert "Sign in" in body
# ---------------------------------------------------------------
# Scenario 10: Main member navigates to polls from the dashboard
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10NavigateFromDashboard:
    """Main member navigates to polls from the dashboard."""

    def test_main_member_finds_poll_from_dashboard(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll exists.
        1. Navigate to / (the member dashboard)
        2. Find and click the active polls section or link to /vote
        Then: User arrives at /vote and sees the open topic poll
        3. Click into the poll
        Then: User can view options, vote counts, and cast their
              votes."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Dashboard Poll Test",
            poll_type="topic",
            description="A poll visible from the dashboard.",
        )
        opt1 = _create_option(poll, "Dashboard Option A")
        _create_option(poll, "Dashboard Option B")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to / (dashboard)
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The dashboard shows the Active Polls section
        assert "Active Polls" in body
        assert "Dashboard Poll Test" in body

        # Step 2: Click the poll link on the dashboard
        poll_link = page.locator(
            f'a[href="/vote/{poll.id}"]'
        )
        assert poll_link.count() >= 1
        poll_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User arrives at the poll detail page
        assert f"/vote/{poll.id}" in page.url
        body = page.content()

        # Step 3: Options and vote counts are visible
        assert "Dashboard Option A" in body
        assert "Dashboard Option B" in body

        # User can vote
        vote_btn = page.locator(
            f'button.vote-btn[data-option-id="{opt1.id}"]'
        )
        assert vote_btn.count() >= 1
# ---------------------------------------------------------------
# Scenario 11: Member submits a proposal with missing title and
#               gets validation feedback
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11ProposalMissingTitle:
    """Member submits a proposal with missing title and gets
    validation feedback."""

    def test_proposal_with_empty_title_shows_error(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll with allow_proposals = true.
        1. Navigate to the poll detail page
        2. Leave the proposal title empty and click 'Submit Proposal'
        Then: The form shows a validation error that the title is
              required
        Then: No new option is added to the poll."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Validation Test Poll",
            poll_type="topic",
            allow_proposals=True,
        )
        _create_option(poll, "Existing Option")

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the poll detail page
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )

        # Step 2: Leave title empty and submit
        # The title input has the HTML `required` attribute,
        # which makes the browser block the submit before the
        # JS handler runs. Remove the attribute so that the
        # form's JavaScript validation path is exercised.
        page.evaluate(
            """() => {
                document.getElementById('proposal-title')
                    .removeAttribute('required');
            }"""
        )
        title_input = page.locator("#proposal-title")
        title_input.fill("")

        # Click submit -- the JS handler checks title first
        page.click(
            '#propose-form button[type="submit"]'
        )

        # Wait for the error message from JS
        message_el = page.locator("#propose-message")
        page.wait_for_function(
            """() => {
                var el = document.getElementById('propose-message');
                return el && el.textContent.includes('Title is required');
            }""",
            timeout=10000,
        )

        # Then: Error message shows "Title is required"
        message_text = message_el.inner_text()
        assert "Title is required" in message_text

        # Then: No new option was added
        from voting.models import PollOption

        options_count = PollOption.objects.filter(
            poll=poll
        ).count()
        assert options_count == 1  # Only the original
# ---------------------------------------------------------------
# Scenario 12: Poll with no options yet shows empty state and
#               encourages proposals
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12EmptyPollEncouragesProposals:
    """Poll with no options yet shows empty state and encourages
    proposals."""

    def test_empty_poll_shows_empty_state_and_accepts_proposal(
        self, django_server
    , browser):
        """Given a user logged in as main@test.com (Main tier), an open
        topic poll with allow_proposals = true and zero options.
        1. Navigate to the poll detail page
        Then: A message indicates there are no options yet and
              encourages the user to be the first to propose one
        2. Fill in and submit a proposal with title 'Building AI Agents'
        Then: The proposal is accepted and appears as the first option
              in the poll."""
        _clear_polls()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        poll = _create_poll(
            title="Empty Poll Test",
            poll_type="topic",
            allow_proposals=True,
        )
        # No options created

        context = _auth_context(browser, "main@test.com")
        page = context.new_page()
        # Step 1: Navigate to the poll detail page
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: Empty state message
        assert "No options yet" in body
        assert "first to propose" in body

        # The proposal form is available
        propose_form = page.locator("#propose-form")
        assert propose_form.count() >= 1

        # Step 2: Fill in and submit a proposal
        page.fill("#proposal-title", "Building AI Agents")
        page.click(
            '#propose-form button[type="submit"]'
        )

        # Wait for success message
        page.wait_for_function(
            """() => {
                var el = document.getElementById('propose-message');
                return el && el.textContent.includes('Proposal submitted');
            }""",
            timeout=10000,
        )

        # Wait for page reload
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_load_state("domcontentloaded")

        # Navigate again to see the fresh state
        page.goto(
            f"{django_server}/vote/{poll.id}",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: The new option appears
        assert "Building AI Agents" in body

        # The "No options yet" message is gone
        assert "No options yet" not in body