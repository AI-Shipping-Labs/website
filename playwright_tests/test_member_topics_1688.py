"""Topics wiki user journeys (#1688, open access per #1804).

Covers the issue's Playwright scenarios that are practical locally:
anonymous visitors read full topic pages and follow related links, the
hub grid discovery journey, the conversion CTAs to /membership,
/workshops, and /courses/ai-buildcamp, the free member reading without
upsell friction, and the draft pages staying out of the hub grid and the
sitemap. The full per-level matrix and context shapes live in the Django
tests at ``topics/tests/test_views.py``.
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
HUB_TAIL = 'this clause exists only in the stored hub body'
RAG_TAIL = 'this clause exists only in the stored RAG body'
AGENTS_TAIL = 'this clause exists only in the stored agents body'
VECTOR_TAIL = 'this clause exists only in the stored vector-search body'


def _topic_body(tail):
    """A body with a distinctive tail proving the full render."""
    return f'A topic guide body that ends with {tail} and nothing gated.'


def _seed_topics(*, include_draft=False):
    """Reset and create the fixture topic set through the ORM."""
    from topics.models import TopicPage

    TopicPage.objects.all().delete()
    TopicPage.objects.create(
        slug='index',
        title='AISL Wiki',
        summary=TAGLINE,
        body=f'The wiki hub body runs long and ends where {HUB_TAIL}.',
    )
    TopicPage.objects.create(
        slug='rag',
        title='RAG',
        summary='Retrieval-augmented generation across the AISL material.',
        body=_topic_body(RAG_TAIL),
        related=['agents', 'missing-topic'],
    )
    TopicPage.objects.create(
        slug='agents',
        title='Agents',
        summary='The loop, the frameworks, durable execution.',
        body=_topic_body(AGENTS_TAIL),
        related=['rag'],
    )
    TopicPage.objects.create(
        slug='vector-search',
        title='Vector Search',
        summary='TF-IDF to embeddings, from first principles to SQLite.',
        body=_topic_body(VECTOR_TAIL),
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


def _assert_open_render(page, body_tail):
    """The full render contract: full body, conversion band, no gate."""
    content = page.content()
    assert body_tail in content
    assert 'topics-gated-card' not in content
    assert 'topics-gated-cta' not in content
    assert 'topic-teaser' not in content
    assert 'topics-signin-link' not in content


@browser_journey
def test_anonymous_reads_topic_from_search_and_follows_related(
    browser, django_server,
):
    """Anonymous: a search-result landing reads in full, related too."""
    _seed_topics()
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f'{django_server}/topics/rag/')
    assert response.status == 200
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    _assert_open_render(page, RAG_TAIL)

    related = page.get_by_test_id('topic-related-link')
    expect(related).to_have_count(1)
    assert related.first.get_attribute('href') == '/topics/agents/'
    related.first.click()
    page.wait_for_load_state('domcontentloaded')
    assert '/topics/agents/' in page.url
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    _assert_open_render(page, AGENTS_TAIL)


@browser_journey
def test_anonymous_explores_hub_grid_and_opens_topic(
    browser, django_server,
):
    """Anonymous: nav -> hub body + grid -> full topic page."""
    _seed_topics()
    page = browser.new_context().new_page()

    goto_with_retry(page, f'{django_server}/')
    _open_topics_from_nav(page)
    expect(page.get_by_test_id('topics-grid')).to_be_visible()
    assert TAGLINE in page.content()
    _assert_open_render(page, HUB_TAIL)

    page.locator('[data-testid="topic-card"]').filter(
        has_text='Vector Search',
    ).first.click()
    page.wait_for_load_state('domcontentloaded')
    assert '/topics/vector-search/' in page.url
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    _assert_open_render(page, VECTOR_TAIL)


@browser_journey
def test_anonymous_follows_membership_cta_from_topic(
    browser, django_server,
):
    """Anonymous: the topic conversion band lands on /membership."""
    _seed_topics()
    page = browser.new_context().new_page()

    goto_with_retry(page, f'{django_server}/topics/rag/')
    expect(page.get_by_test_id('topics-conversion')).to_be_visible()
    page.get_by_test_id('topics-cta-membership').click()
    page.wait_for_load_state('domcontentloaded')
    assert '/membership' in page.url


@browser_journey
def test_anonymous_follows_buildcamp_and_workshops_ctas(
    browser, django_server,
):
    """Anonymous: detail CTA to the Buildcamp, hub CTA to workshops."""
    _seed_topics()
    page = browser.new_context().new_page()

    goto_with_retry(page, f'{django_server}/topics/rag/')
    page.get_by_test_id('topics-cta-buildcamp').click()
    page.wait_for_load_state('domcontentloaded')
    assert '/courses/ai-buildcamp' in page.url

    goto_with_retry(page, f'{django_server}/topics/')
    expect(page.get_by_test_id('topics-conversion')).to_be_visible()
    page.get_by_test_id('topics-cta-workshops').click()
    page.wait_for_load_state('domcontentloaded')
    assert '/workshops' in page.url


@browser_journey
def test_free_member_reads_full_page_without_upsell_friction(
    browser, django_server,
):
    """Free member: full body, no gated render, conversion band present."""
    _seed_topics()
    create_user('free-1688@test.com', tier_slug='free')
    context = auth_context(browser, 'free-1688@test.com')
    page = context.new_page()

    goto_with_retry(page, f'{django_server}/topics/rag/')
    expect(page.get_by_test_id('topic-body')).to_be_visible()
    _assert_open_render(page, RAG_TAIL)
    expect(page.get_by_test_id('topics-conversion')).to_be_visible()


@browser_journey
def test_draft_topic_stays_hidden(browser, django_server):
    """Draft topic: 404 on the page and absent from the hub grid."""
    _seed_topics(include_draft=True)
    page = browser.new_context().new_page()

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
def test_topics_urls_in_the_sitemap(browser, django_server):
    """Crawler surface: sitemap.xml lists the hub and published pages."""
    _seed_topics(include_draft=True)
    page = browser.new_context().new_page()

    response = goto_with_retry(page, f'{django_server}/sitemap.xml')
    assert response.status == 200
    content = page.content()
    assert '/topics/' in content
    assert '/topics/rag/' in content
    assert '/topics/evaluation/' not in content
