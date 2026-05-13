"""Playwright E2E tests for external-link rewriting (issue #303).

Covers the two scenarios in the spec:

1. A workshop tutorial page contains a mix of external + internal links.
   The external link must open in a new tab and the internal links must
   stay in the current tab.
2. An author who hand-wrote ``target="_self"`` on a raw ``<a>`` tag has
   that override preserved, while a default markdown link to the same
   external host is rewritten to ``target="_blank"``.

Usage:
    uv run pytest playwright_tests/test_external_links.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

# Playwright spins up its own async loop; Django's async-safety check
# trips on synchronous ORM calls inside that loop.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402


def _clear_workshops_and_articles():
    from content.models import Article, Workshop, WorkshopPage
    from events.models import Event
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Article.objects.all().delete()
    connection.close()


def _create_workshop_with_links(slug='architecture-walk-through'):
    """Create a workshop with the mixed-link tutorial body from the spec."""
    from django.utils import timezone
    from django.utils.text import slugify

    from content.models import Instructor, Workshop, WorkshopInstructor, WorkshopPage
    from events.models import Event

    event = Event.objects.create(
        slug=f'{slug}-event',
        title='Architecture Walkthrough',
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        published=True,
    )
    workshop = Workshop.objects.create(
        slug=slug,
        title='Architecture Walkthrough',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description='A workshop about agent architecture.',
        event=event,
    )
    instructor_name = 'Alexey'
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200] or 'test-instructor',
        defaults={
            'name': instructor_name,
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop,
        instructor=instructor,
        defaults={'position': 0},
    )
    # An "intro" page so the relative link "01-intro" resolves to a
    # real workshop page. We don't actually click that link, so the
    # path doesn't have to match — but having it makes the markup
    # realistic.
    WorkshopPage.objects.create(
        workshop=workshop, slug='01-intro', title='Intro',
        sort_order=1, body='# Intro',
    )
    qa_body = (
        "## Prerequisites\n\n"
        "Some setup notes here.\n\n"
        "See the [`tmuxctl` repo](https://github.com/alexeygrigorev/tmuxctl) "
        "for a CLI version. Jump to [Prerequisites](#prerequisites) or "
        "read [part 1](01-intro) on this site, or visit our "
        "[home page](/).\n"
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug='qa', title='Q&A',
        sort_order=2, body=qa_body,
    )
    connection.close()
    return workshop


def _create_article_with_target_self(slug='target-self-article'):
    from content.models import Article

    body = (
        "Override: <a href=\"https://example.com\" target=\"_self\">"
        "stay here</a>.\n\n"
        "Default: [also example](https://example.com).\n"
    )
    article = Article(
        title='Target Self Article',
        slug=slug,
        date=datetime.date(2026, 4, 21),
        author='Tester',
        content_markdown=body,
        published=True,
    )
    article.save()
    connection.close()
    return article


# ---------------------------------------------------------------------
# Scenario 1: Workshop reader follows an external reference and stays
# in the workshop on internal navigation.
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWorkshopExternalLinkOpensInNewTab:
    def test_external_link_has_target_blank_and_noopener(
        self, browser, django_server,
    ):
        _clear_workshops_and_articles()
        _create_workshop_with_links()
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/architecture-walk-through/'
            f'tutorial/qa',
            wait_until='domcontentloaded',
        )

        external_link = page.locator(
            'a[href="https://github.com/alexeygrigorev/tmuxctl"]',
        ).first
        assert external_link.get_attribute('target') == '_blank'
        rel = external_link.get_attribute('rel') or ''
        assert 'noopener' in rel.split()

        ctx.close()

    def test_internal_links_have_no_target_blank(
        self, browser, django_server,
    ):
        _clear_workshops_and_articles()
        _create_workshop_with_links()
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/architecture-walk-through/'
            f'tutorial/qa',
            wait_until='domcontentloaded',
        )

        # Anchor link, relative path, and root-relative path must NOT
        # have target="_blank". We scope the lookups to ``main`` so that
        # site-chrome links (e.g. logo "/") in the header don't bleed
        # into the assertion.
        anchor = page.locator(
            '[data-testid="page-body"] a[href="#prerequisites"]',
        ).first
        assert anchor.get_attribute('target') is None

        relative = page.locator(
            '[data-testid="page-body"] a[href="01-intro"]',
        ).first
        assert relative.get_attribute('target') is None

        root_relative = page.locator(
            '[data-testid="page-body"] a[href="/"]',
        ).first
        assert root_relative.get_attribute('target') is None

        ctx.close()

    def test_clicking_external_link_opens_new_tab(
        self, browser, django_server,
    ):
        _clear_workshops_and_articles()
        _create_workshop_with_links()
        _create_user('free@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'free@test.com')
        page = ctx.new_page()
        page.goto(
            f'{django_server}/workshops/architecture-walk-through/'
            f'tutorial/qa',
            wait_until='domcontentloaded',
        )

        # Block all outbound navigations except the local Django server
        # so the test never actually touches github.com. The chrome-error
        # page that results from ``route.abort()`` still counts as a new
        # ``Page`` object, which is exactly what we need to assert.
        def _block_external(route):
            if route.request.url.startswith(django_server):
                route.continue_()
            else:
                route.abort()
        ctx.route('**/*', _block_external)

        external_link = page.locator(
            'a[href="https://github.com/alexeygrigorev/tmuxctl"]',
        ).first
        with ctx.expect_page() as new_page_info:
            external_link.click()
        new_page = new_page_info.value
        # The new tab was created (any URL — we aborted the load).
        # The original tab is still on the workshop page (proof the
        # click did not navigate the original tab).
        assert new_page is not None
        assert (
            '/workshops/architecture-walk-through/tutorial/qa' in page.url
        )

        ctx.close()


# ---------------------------------------------------------------------
# Scenario 2: Author hand-wrote target="_self" — extension respects it.
# ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestArticleTargetSelfRespected:
    def test_handwritten_target_self_preserved_default_link_rewritten(
        self, django_server, page,
    ):
        _clear_workshops_and_articles()
        _create_article_with_target_self()

        page.goto(
            f'{django_server}/blog/target-self-article',
            wait_until='domcontentloaded',
        )

        # Both anchors point at https://example.com but they have
        # different inner text. Disambiguate on text content with
        # ``has-text`` so we never grab a parent block element.
        stay = page.locator(
            'a[href="https://example.com"]:has-text("stay here")',
        ).first
        assert stay.get_attribute('target') == '_self'

        default = page.locator(
            'a[href="https://example.com"]:has-text("also example")',
        ).first
        assert default.get_attribute('target') == '_blank'
        rel = default.get_attribute('rel') or ''
        assert 'noopener' in rel.split()
