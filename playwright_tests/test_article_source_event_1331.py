"""Reader and Studio journeys for article source-event attribution (#1331)."""

import datetime as dt
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _reset_source_event_content():
    from content.models import Article, Workshop
    from events.models import Event

    Article.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_public_source_fixture(*, workshop=False):
    from content.models import Article, Workshop
    from events.models import Event

    event = Event.objects.create(
        slug='source-event-browser',
        title='Source Event Browser',
        start_datetime=timezone.now() - dt.timedelta(hours=2),
        end_datetime=timezone.now() - dt.timedelta(hours=1),
        status='completed',
        published=True,
        kind='workshop' if workshop else 'standard',
    )
    if workshop:
        Workshop.objects.create(
            slug='source-event-workshop',
            title='Source Event Workshop',
            date=dt.date(2026, 7, 21),
            status='published',
            event=event,
        )
    older = Article.objects.create(
        slug='older-browser-source-article',
        title='Older Browser Source Article',
        description='Older browser article.',
        content_markdown='Older browser article body.',
        date=dt.date(2026, 7, 20),
        required_level=20,
        source_event=event,
        published=True,
    )
    newer = Article.objects.create(
        slug='newer-browser-source-article',
        title='Newer Browser Source Article',
        description='Newer browser article.',
        content_markdown='Newer browser article body.',
        date=dt.date(2026, 7, 22),
        source_event=event,
        published=True,
    )
    connection.close()
    return event, older, newer


@pytest.mark.django_db(transaction=True)
class TestArticleSourceEventReaderJourneys:
    def test_reader_can_move_between_article_event_and_source_articles(
        self, django_server, page,
    ):
        _reset_source_event_content()
        event, older, newer = _create_public_source_fixture(workshop=True)

        page.goto(
            f'{django_server}{newer.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        handoff = page.get_by_test_id('article-source-event')
        expect(handoff).to_contain_text('From the event')
        expect(handoff).to_contain_text(event.title)
        page.get_by_test_id('article-source-event-link').click()
        expect(page).to_have_url(f'{django_server}{event.get_absolute_url()}')

        cards = page.get_by_test_id('event-source-article-card')
        expect(cards).to_have_count(2)
        expect(cards.nth(0)).to_contain_text(newer.title)
        expect(cards.nth(1)).to_contain_text(older.title)
        expect(page.get_by_test_id('event-workshop-writeup')).to_be_visible()
        cards.nth(1).click()
        expect(page).to_have_url(f'{django_server}{older.get_absolute_url()}')
        expect(page.get_by_test_id('article-source-event')).to_contain_text(
            event.title,
        )

    def test_free_member_sees_article_access_before_navigation_and_source_after(
        self, django_server, browser,
    ):
        _reset_source_event_content()
        event, older, _ = _create_public_source_fixture()
        create_user('source-event-free@example.com', tier_slug='free')
        context = auth_context(browser, 'source-event-free@example.com')
        page = context.new_page()

        page.goto(
            f'{django_server}{event.get_absolute_url()}',
            wait_until='domcontentloaded',
        )
        older_card = page.get_by_test_id('event-source-article-card').filter(
            has_text=older.title,
        )
        expect(older_card.get_by_test_id('event-source-article-access')).to_contain_text(
            'Main or above',
        )
        older_card.click()
        expect(page.get_by_test_id('gated-access-card')).to_be_visible()
        expect(page.get_by_test_id('article-source-event')).to_contain_text(
            event.title,
        )
        context.close()

    def test_article_never_leaks_non_public_source_event(
        self, django_server, page,
    ):
        _reset_source_event_content()
        event, article, _ = _create_public_source_fixture()

        for status, published in (
            ('draft', True),
            ('cancelled', True),
            ('completed', False),
        ):
            from events.models import Event

            Event.objects.filter(pk=event.pk).update(
                status=status,
                published=published,
            )
            connection.close()
            page.goto(
                f'{django_server}{article.get_absolute_url()}',
                wait_until='domcontentloaded',
            )
            expect(page.get_by_test_id('article-source-event')).to_have_count(0)
            expect(page.locator('main')).not_to_contain_text(event.title)

    def test_event_without_qualifying_source_articles_omits_section_and_blank_container(
        self, django_server, page,
    ):
        _reset_source_event_content()
        from content.models import Article
        from events.models import Event

        event = Event.objects.create(
            slug='source-event-without-articles',
            title='Source Event Without Articles',
            description='Existing event experience remains visible.',
            start_datetime=timezone.now() - dt.timedelta(hours=2),
            end_datetime=timezone.now() - dt.timedelta(hours=1),
            status='completed',
            published=True,
            materials=[
                {
                    'title': 'Existing event material',
                    'url': 'https://example.com/material',
                    'type': 'slides',
                },
            ],
        )
        connection.close()

        event_url = f'{django_server}{event.get_absolute_url()}'
        page.goto(event_url, wait_until='domcontentloaded')
        expect(page.get_by_test_id('event-source-articles')).to_have_count(0)
        expect(page.get_by_text('Articles from this event', exact=True)).to_have_count(0)
        expect(page.locator('main')).to_contain_text(event.title)
        expect(page.locator('main')).to_contain_text(
            'Existing event experience remains visible.',
        )
        expect(page.get_by_test_id('event-post-resources')).to_contain_text(
            'Existing event material',
        )

        Article.objects.create(
            slug='browser-draft-source-article',
            title='Browser Draft Source Article',
            date=dt.date(2026, 7, 22),
            source_event=event,
            published=False,
        )
        Article.objects.create(
            slug='browser-non-blog-source-article',
            title='Browser Non-Blog Source Article',
            date=dt.date(2026, 7, 22),
            source_event=event,
            published=True,
            page_type='learning_path',
        )
        connection.close()

        page.reload(wait_until='domcontentloaded')
        expect(page.get_by_test_id('event-source-articles')).to_have_count(0)
        expect(page.get_by_text('Articles from this event', exact=True)).to_have_count(0)
        expect(page.locator('main')).to_contain_text(event.title)
        expect(page.get_by_test_id('event-post-resources')).to_contain_text(
            'Existing event material',
        )


@pytest.mark.django_db(transaction=True)
class TestArticleSourceEventStudioJourneys:
    def test_staff_can_verify_both_sides_without_relationship_controls(
        self, django_server, browser,
    ):
        _reset_source_event_content()
        event, published, _ = _create_public_source_fixture()
        from content.models import Article

        draft = Article.objects.create(
            slug='studio-browser-draft-source',
            title='Studio Browser Draft Source',
            date=dt.date(2026, 7, 21),
            source_event=event,
            published=False,
        )
        connection.close()
        create_staff_user('source-event-staff@example.com')
        context = auth_context(browser, 'source-event-staff@example.com')
        page = context.new_page()

        page.goto(
            f'{django_server}{published.get_studio_edit_url()}',
            wait_until='domcontentloaded',
        )
        row = page.get_by_test_id('article-source-event-row')
        expect(row).to_contain_text(event.title)
        expect(row.get_by_role('link', name='Edit in Studio')).to_have_attribute(
            'href',
            f'/studio/events/{event.pk}/edit',
        )
        expect(page.locator('[name="source_event"]')).to_have_count(0)

        page.goto(
            f'{django_server}/studio/events/{event.pk}/edit',
            wait_until='domcontentloaded',
        )
        section = page.get_by_test_id('event-source-articles')
        expect(section).to_contain_text(published.title)
        expect(section).to_contain_text(draft.title)
        expect(section).to_contain_text('Published')
        expect(section).to_contain_text('Draft')
        published_row = section.get_by_test_id('event-source-article-row').filter(
            has_text=published.title,
        )
        draft_row = section.get_by_test_id('event-source-article-row').filter(
            has_text=draft.title,
        )
        expect(published_row.get_by_role('link', name='View article')).to_have_attribute(
            'href',
            published.get_absolute_url(),
        )
        expect(draft_row.get_by_role('link', name='Preview draft')).to_have_attribute(
            'href',
            draft.get_preview_url(),
        )
        expect(page.locator('[name="source_articles"]')).to_have_count(0)
        context.close()
