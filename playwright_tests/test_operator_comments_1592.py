"""Cross-surface plain-text and idempotency journey for issue #1592."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user
from playwright_tests.test_workshop_comments import (
    _clear_workshops_and_courses,
    _create_course_with_unit,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.core, pytest.mark.local_only]


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_operator_reply_is_escaped_and_idempotent_in_course_discussion(
    browser, django_server,
):
    from accounts.models import Token
    from comments.models import ApiReplyOperation, Comment

    ApiReplyOperation.objects.all().delete()
    _clear_workshops_and_courses()
    _course, _module, unit = _create_course_with_unit(
        course_slug='operator-comments',
        module_slug='day-1',
        unit_slug='frontmatter',
    )
    learner = create_user(
        'operator-comments-learner@test.com', tier_slug='basic',
        first_name='Learner',
    )
    staff = create_staff_user('operator-comments-staff@test.com')
    Token.objects.filter(user=staff).delete()
    _token, plaintext = Token.create_for_user(user=staff, name='playwright comments')
    question = Comment.objects.create(
        content_id=unit.content_id,
        user=learner,
        body='Can you explain this step?',
    )
    connection.close()

    raw_body = '<img src=x onerror=alert(1)> **answer**'
    headers = {
        'Authorization': f'Token {plaintext}',
        'Idempotency-Key': 'playwright-1592-reply',
        'Content-Type': 'application/json',
    }
    endpoint = f'{django_server}/api/comments/{question.pk}/replies'
    operator_context = browser.new_context()
    try:
        created = operator_context.request.post(
            endpoint, headers=headers, data={'body': raw_body},
        )
        assert created.status == 201
        assert created.json()['idempotent_replay'] is False
    finally:
        operator_context.close()

    context = auth_context(browser, learner.email)
    page = context.new_page()
    try:
        page.goto(
            f'{django_server}/courses/operator-comments/day-1/frontmatter',
            wait_until='domcontentloaded',
        )
        card = page.locator(f'#qa-list > [data-comment-id="{question.pk}"]')
        expect(card).to_contain_text(raw_body)
        assert card.locator('img').count() == 0
        assert card.locator('script').count() == 0
        assert card.locator('[onerror]').count() == 0
        assert card.locator('strong').count() == 0

        replayed = page.request.post(endpoint, headers=headers, data={'body': raw_body})
        assert replayed.status == 200
        assert replayed.json()['idempotent_replay'] is True
        page.reload(wait_until='domcontentloaded')
        card = page.locator(f'#qa-list > [data-comment-id="{question.pk}"]')
        expect(card.get_by_text(raw_body, exact=True)).to_have_count(1)
        expect(page.locator('#qa-list > [data-comment-id]')).to_have_count(1)
    finally:
        context.close()
