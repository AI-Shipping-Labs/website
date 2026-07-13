"""User journeys for catalog tag presentation and semantics (#1228)."""

import datetime
import os
import re

import pytest
from playwright.sync_api import expect

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection

pytestmark = pytest.mark.local_only


def _clear_catalogs():
    from content.models import Article, CuratedLink, Download, Tutorial

    Article.objects.all().delete()
    CuratedLink.objects.all().delete()
    Download.objects.all().delete()
    Tutorial.objects.all().delete()
    connection.close()


def _article(slug, title, tags, description='Catalog article description.'):
    from content.models import Article

    article = Article.objects.create(
        title=title,
        slug=slug,
        description=description,
        content_markdown=f'{title} body.',
        author='Catalog Author',
        tags=tags,
        published=True,
        date=datetime.date(2026, 7, 13),
    )
    connection.close()
    return article


def _download(slug, title, tags, required_level=10):
    from content.models import Download

    download = Download.objects.create(
        title=title,
        slug=slug,
        description=f'{title} description.',
        file_url=f'https://example.com/{slug}.pdf',
        file_type='pdf',
        tags=tags,
        required_level=required_level,
        published=True,
    )
    connection.close()
    return download


def _tutorial(slug, title, tags):
    from content.models import Tutorial

    tutorial = Tutorial.objects.create(
        title=title,
        slug=slug,
        description=f'{title} description.',
        content_markdown=f'{title} body.',
        date=datetime.date(2026, 7, 13),
        tags=tags,
        published=True,
    )
    connection.close()
    return tutorial


def _curated_link(item_id, title, tags, required_level=0):
    from content.models import CuratedLink

    link = CuratedLink.objects.create(
        item_id=item_id,
        title=title,
        description=f'{title} description.',
        url=f'https://example.com/{item_id}',
        category='workshops',
        tags=tags,
        required_level=required_level,
        published=True,
    )
    connection.close()
    return link


@pytest.mark.django_db(transaction=True)
def test_reader_filters_blog_through_article_tag_chip(django_server, page):
    _clear_catalogs()
    _article(
        'agents-catalog-1228',
        'Agents Catalog Article 1228',
        ['agents', 'python', 'evaluation', 'production'],
    )
    _article('unrelated-catalog-1228', 'Unrelated Catalog Article 1228', ['rust'])

    page.goto(f'{django_server}/blog', wait_until='domcontentloaded')
    card = page.locator('article', has_text='Agents Catalog Article 1228')
    expect(card.locator('[aria-label="1 more article tags"]')).to_have_text('+1')
    card.get_by_test_id('blog-card-tags').get_by_role(
        'link', name='agents', exact=True,
    ).click()

    expect(page).to_have_url(re.compile(r'.*/blog\?tag=agents$'))
    expect(page.get_by_text('Agents Catalog Article 1228')).to_be_visible()
    expect(page.get_by_text('Unrelated Catalog Article 1228')).to_have_count(0)
    assert '/blog/agents-catalog-1228' not in page.url


@pytest.mark.django_db(transaction=True)
def test_visitor_filters_downloads_then_uses_primary_card_action(django_server, page):
    _clear_catalogs()
    _download(
        'python-download-1228',
        'Python Download 1228',
        ['python', 'agents', 'evaluation', 'production'],
    )
    _download('rust-download-1228', 'Rust Download 1228', ['rust'])

    page.goto(f'{django_server}/downloads', wait_until='domcontentloaded')
    card = page.get_by_test_id('download-card').filter(has_text='Python Download 1228')
    expect(card.locator('[aria-label="1 more download tags"]')).to_have_text('+1')
    card.get_by_test_id('download-card-tags').get_by_role(
        'link', name='python', exact=True,
    ).click()

    expect(page).to_have_url(re.compile(r'.*/downloads\?tag=python$'))
    expect(page.get_by_role('heading', name='Python Download 1228')).to_be_visible()
    expect(page.get_by_text('Rust Download 1228')).to_have_count(0)
    page.get_by_test_id('download-card-body-link').click()
    expect(page).to_have_url(re.compile(r'.*/pricing$'))


@pytest.mark.django_db(transaction=True)
def test_topic_explorer_opens_related_tag_instead_of_result(django_server, page):
    _clear_catalogs()
    _article('ai-python-1228', 'AI Python Result 1228', ['ai', 'python'])
    _article('python-only-1228', 'Python Only Result 1228', ['python'])

    page.goto(f'{django_server}/tags/ai', wait_until='domcontentloaded')
    page.get_by_test_id('tag-detail-related-tags').get_by_role(
        'link', name='python', exact=True,
    ).click()

    expect(page).to_have_url(re.compile(r'.*/tags/python$'))
    expect(page.get_by_text('Python Only Result 1228')).to_be_visible()
    assert '/blog/ai-python-1228' not in page.url


@pytest.mark.django_db(transaction=True)
def test_topic_explorer_opens_result_from_card_body(django_server, page):
    _clear_catalogs()
    _article(
        'tag-card-body-1228',
        'Tag Card Body Result 1228',
        ['ai', 'python'],
        description='Open this result from its card body.',
    )

    page.goto(f'{django_server}/tags/ai', wait_until='domcontentloaded')
    card = page.locator('article', has_text='Tag Card Body Result 1228')
    expect(card.locator('a a')).to_have_count(0)
    card.get_by_text('Open this result from its card body.').click()

    expect(page).to_have_url(re.compile(r'.*/blog/tag-card-body-1228$'))


@pytest.mark.django_db(transaction=True)
def test_mobile_tag_result_has_no_detached_arrow_and_stays_clickable(
    django_server, page,
):
    _clear_catalogs()
    _article('mobile-ai-one-1228', 'Mobile AI One 1228', ['ai', 'python'])
    _article('mobile-ai-two-1228', 'Mobile AI Two 1228', ['ai', 'agents'])
    page.set_viewport_size({'width': 390, 'height': 844})

    page.goto(f'{django_server}/tags/ai', wait_until='domcontentloaded')
    cards = page.locator('article')
    expect(cards).to_have_count(2)
    for index in range(cards.count()):
        expect(cards.nth(index).locator('[data-lucide="arrow-right"]')).to_be_hidden()
    cards.filter(has_text='Mobile AI One 1228').locator('h2').click()

    expect(page).to_have_url(re.compile(r'.*/blog/mobile-ai-one-1228$'))


@pytest.mark.django_db(transaction=True)
def test_mobile_tutorial_keeps_static_tags_and_whole_card_navigation(
    django_server, page,
):
    _clear_catalogs()
    _tutorial('mobile-tutorial-1228', 'Mobile Tutorial 1228', ['python', 'agents'])
    page.set_viewport_size({'width': 390, 'height': 844})

    page.goto(f'{django_server}/tutorials', wait_until='domcontentloaded')
    card = page.locator('article', has_text='Mobile Tutorial 1228')
    expect(card.locator('[data-lucide="arrow-right"]')).to_be_hidden()
    expect(card.get_by_text('python', exact=True)).to_have_js_property('tagName', 'SPAN')
    expect(card.get_by_role('link', name='python', exact=True)).to_have_count(0)
    card.get_by_text('Mobile Tutorial 1228 description.').click()

    expect(page).to_have_url(re.compile(r'.*/tutorials/mobile-tutorial-1228$'))


@pytest.mark.django_db(transaction=True)
def test_accessible_curated_link_static_tag_keeps_external_card_action(
    django_server, page,
):
    _clear_catalogs()
    _curated_link(
        'accessible-resource-1228',
        'Accessible Resource 1228',
        ['python', 'agents', 'evaluation', 'production'],
    )

    page.goto(f'{django_server}/resources', wait_until='domcontentloaded')
    card = page.locator('a[target="_blank"]', has_text='Accessible Resource 1228')
    expect(card.get_by_role('link', name='python', exact=True)).to_have_count(0)
    expect(card.locator('[aria-label="1 more resource tags"]')).to_have_text('+1')
    with page.expect_popup() as popup_info:
        card.get_by_text('python', exact=True).click()
    popup = popup_info.value
    popup.wait_for_url(re.compile(r'.*/accessible-resource-1228$'))
    assert popup.url == 'https://example.com/accessible-resource-1228'
    popup.close()


@pytest.mark.django_db(transaction=True)
def test_gated_curated_link_static_tag_reveals_existing_access_options(
    django_server, page,
):
    _clear_catalogs()
    _curated_link(
        'gated-resource-1228',
        'Gated Resource 1228',
        ['python', 'agents', 'evaluation', 'production'],
        required_level=20,
    )

    page.goto(f'{django_server}/resources', wait_until='domcontentloaded')
    card = page.get_by_role(
        'button', name='Show access options for Gated Resource 1228',
    )
    expect(card.get_by_role('link', name='python', exact=True)).to_have_count(0)
    card.get_by_text('python', exact=True).click()
    expect(card).to_have_attribute('aria-expanded', 'true')
    expect(card.get_by_role('link', name='View Plans')).to_be_visible()
    expect(card.get_by_role('link', name='View Plans')).to_have_attribute(
        'href', '/pricing',
    )


@pytest.mark.django_db(transaction=True)
def test_keyboard_reader_sees_focus_and_filters_blog_with_enter(django_server, page):
    _clear_catalogs()
    _article('keyboard-agents-1228', 'Keyboard Agents 1228', ['agents'])

    page.goto(f'{django_server}/blog', wait_until='networkidle')
    chip = page.get_by_test_id('blog-card-tags').get_by_role(
        'link', name='agents', exact=True,
    )
    for _ in range(60):
        page.keyboard.press('Tab')
        if chip.evaluate('(element) => document.activeElement === element'):
            break
    expect(chip).to_be_focused()
    box_shadow = chip.evaluate('(element) => getComputedStyle(element).boxShadow')
    assert box_shadow != 'none'
    page.keyboard.press('Enter')

    expect(page).to_have_url(re.compile(r'.*/blog\?tag=agents$'))
    expect(page.get_by_text('Keyboard Agents 1228')).to_be_visible()
