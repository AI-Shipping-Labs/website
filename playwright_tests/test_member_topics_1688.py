"""Member topics user journeys (#1688).

Covers the issue's Playwright scenarios that are practical locally: the
Basic member reading and following related links, the Free member's
upgrade path, the anonymous sign-in wall, hub-grid discovery, the hidden
draft, and the sitemap exclusion. Server-rendered gating details (the
full per-level matrix, context shapes) live in the Django tests at
``topics/tests/test_views.py``.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    goto_with_retry,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.core,
    pytest.mark.local_only,
]

TAGLINE = 'Topic guides built from every AISL course, workshop, and article.'
HUB_TAIL = 'the second half of this hub essay exists only for paying members'
RAG_TAIL = 'the rest of the RAG guide exists only for paying members'
AGENTS_TAIL = 'the rest of the agents guide exists only for paying members'
VECTOR_TAIL = 'the rest of the vector-search guide exists only for paying members'

# The index body is one sentence; give it a gated tail beyond the window.
HUB_TAIL_SENTENCE = (
    'The wiki turns the AISL material into topic-oriented guides that keep '
    'going well past the two hundred plain-text characters a denied render '
    'may show as a teaser, and every word after this point, including '
)


def _long_body(tail):
    """A body whose tail starts beyond the 200-char teaser window."""
    return (
        'A topic body that keeps going well past the two hundred plain-text '
        'characters a denied render may ever show as its teaser paragraph, '
        'and every single word after this point, including '
        f'{tail}, belongs to the gated tier and must never reach an '
        'anonymous or Free-tier response.'
    )


def _seed_topics(*, include_draft=False):
    """Reset and create the fixture topic set through the ORM."""
    from topics.models import TopicPage

    TopicPage.objects.all().delete()
    TopicPage.objects.create(
        slug='index',
        title='AISL Wiki',
        summary=TAGLINE,
        body=f'{HUB_TAIL_SENTENCE}{HUB_TAIL}, never leaks to denied tiers.',
    )
    TopicPage.objects.create(
        slug='rag',
        title='RAG',
        summary='Retrieval-augmented generation across the AISL material.',
        body=_long_body(RAG_TAIL),
        related=['agents', 'missing-topic'],
    )
    TopicPage.objects.create(
        slug='agents',
        title='Agents',
        summary='The loop, the frameworks, durable execution.',
        body=_long_body(AGENTS_TAIL),
        related=['rag'],
    )
    TopicPage.objects.create(
        slug='vector-search',
        title='Vector Search',
        summary='TF-IDF to embeddings, from first principles to SQLite.',
        body=_long_body(VECTOR_TAIL),
    )
    if include_draft:
        TopicPage.objects.create(
            slug='evaluation',
            title='Evaluation',
            summary='A draft page summary.',
            body='Draft-only body text.',
            status='draft',
        )


def _open_topics_from_nav(page):
    page.get_by_test_id('nav-learning-trigger').hover()
    menu = page.get_by_test_id('nav-learning-menu')
    menu.wait_for(state='visible')
    menu.get_by_test_id('nav-learning-link-topics').click()
    page.wait_for_load_state('domcontentloaded')


@browser_journey
def test_basic_member_reads_topic_and_follows_related_link(
    browser, django_server,
):
    """Basic member: nav -> hub -> RAG -> related topic, all in full."""
    _seed_topics()
    create_user('basic-1688@test.com', tier_slug='basic')
    context = auth_context(browser, 'basic-1688@test.com')
    page = context.new_page()

    goto_with_retry(page, f'{django_server}/')
    _open_topics_from_nav(page)
    expect(page.get_by_test_id('topics-grid')).to_be_visible()
    assert TAGLINE in page.content()
    assert HUB_TAIL in page.content()
    expect(page.get_by_test_id('topics-gated-cta')).to_have_count(0)

    page.locator('[data-testid="topic-card"]').filter(
        has_text='RAG',
    ).first.click()
    page.wait_for_load_state('domcontentloaded')
    assert '/topics/rag/' in page.url
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    assert RAG_TAIL in page.content()
    assert 'topics-gated-cta' not in page.content()
    assert 'topic-teaser-blur' not in page.content()

    related = page.get_by_test_id('topic-related-link')
    expect(related).to_have_count(1)
    assert related.first.get_attribute('href') == '/topics/agents/'
    related.first.click()
    page.wait_for_load_state('domcontentloaded')
    assert '/topics/agents/' in page.url
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    assert AGENTS_TAIL in page.content()


@browser_journey
def test_free_member_hits_teaser_and_finds_upgrade_path(
    browser, django_server,
):
    """Free member: teaser + blur + upgrade CTA that lands on /membership."""
    _seed_topics()
    create_user('free-1688@test.com', tier_slug='free')
    context = auth_context(browser, 'free-1688@test.com')
    page = context.new_page()

    goto_with_retry(page, f'{django_server}/topics/rag/')
    expect(page.get_by_test_id('topic-teaser')).to_be_visible()
    expect(page.get_by_test_id('topic-teaser-blur')).to_be_visible()
    assert RAG_TAIL not in page.content()

    page.get_by_test_id('topics-gated-cta').click()
    page.wait_for_load_state('domcontentloaded')
    assert '/membership' in page.url
    assert 'Basic' in page.content()


@browser_journey
def test_anonymous_visitor_gets_signin_prompt_and_no_body(
    browser, django_server,
):
    """Anonymous: sign-in prompt on hub and page, no body text anywhere."""
    _seed_topics()
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f'{django_server}/topics/')
    assert response.status == 200
    expect(page.get_by_test_id('topics-signin-link')).to_be_visible()
    assert HUB_TAIL not in page.content()

    response = goto_with_retry(page, f'{django_server}/topics/rag/')
    assert response.status == 200
    expect(page.get_by_test_id('topics-signin-link')).to_be_visible()
    assert RAG_TAIL not in page.content()
    assert 'topic-body' not in page.content()


@browser_journey
def test_main_member_discovers_topics_through_hub_grid(
    browser, django_server,
):
    """Main member: every published topic appears in the grid; one opens."""
    _seed_topics()
    create_user('main-1688@test.com', tier_slug='main')
    context = auth_context(browser, 'main-1688@test.com')
    page = context.new_page()

    goto_with_retry(page, f'{django_server}/topics/')
    grid = page.get_by_test_id('topics-grid')
    expect(grid).to_be_visible()
    cards = page.get_by_test_id('topic-card')
    expect(cards).to_have_count(3)
    for title in ('RAG', 'Agents', 'Vector Search'):
        expect(
            cards.filter(has_text=title),
        ).to_have_count(1)

    cards.filter(has_text='Vector Search').first.click()
    page.wait_for_load_state('domcontentloaded')
    assert '/topics/vector-search/' in page.url
    expect(page.get_by_test_id('topic-body')).to_be_visible()


@browser_journey
def test_draft_topic_stays_hidden_from_members(browser, django_server):
    """Draft topic: 404 on the page and absent from the hub grid."""
    _seed_topics(include_draft=True)
    create_user('basic-draft-1688@test.com', tier_slug='basic')
    context = auth_context(browser, 'basic-draft-1688@test.com')
    page = context.new_page()

    goto_with_retry(page, f'{django_server}/topics/')
    grid = page.get_by_test_id('topics-grid')
    expect(grid).to_be_visible()
    expect(page.get_by_test_id('topic-card')).to_have_count(3)
    assert 'Evaluation' not in page.content()

    response = goto_with_retry(
        page,
        f'{django_server}/topics/evaluation/',
        expected_status=404,
    )
    assert response.status == 404


@browser_journey
def test_gated_topics_stay_out_of_the_sitemap(browser, django_server):
    """Crawler surface: sitemap.xml lists no /topics/ URL."""
    _seed_topics()
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f'{django_server}/sitemap.xml')
    assert response.status == 200
    assert '/topics/' not in page.content()
