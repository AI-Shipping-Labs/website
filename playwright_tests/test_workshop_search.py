"""Client-side search on the workshops catalog and the course Home page.

Both surfaces reuse the shared curriculum syllabus search component: typing
shows a suggestion dropdown whose entries link to the matching item.

Usage:
    uv run pytest playwright_tests/test_workshop_search.py -v
"""

import datetime
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path('.tmp/screenshots/workshop-search')


def _seed_workshops():
    from content.models import Workshop, WorkshopPage

    rag = Workshop.objects.create(
        slug='rag-search-e2e', title='Build a RAG assistant', status='published',
        date=datetime.date(2026, 8, 20), tags=['rag'],
        landing_required_level=0, pages_required_level=0,
        recording_required_level=0,
        description='Retrieval pipelines with hybrid search.',
    )
    WorkshopPage.objects.create(
        workshop=rag, slug='evaluate-rag', title='Evaluate RAG answers',
        sort_order=1,
    )
    Workshop.objects.create(
        slug='agents-search-e2e', title='Agents with tools', status='published',
        date=datetime.date(2026, 8, 19), tags=['ai-agents'],
        landing_required_level=0, pages_required_level=0,
        recording_required_level=0,
    )
    connection.close()


@pytest.mark.core
@browser_journey
def test_workshop_catalog_search_suggests_workshops_and_pages(django_server, page):
    _seed_workshops()

    page.goto(f'{django_server}/workshops/catalog', wait_until='domcontentloaded')
    search = page.get_by_role('combobox', name='Search workshops')
    search.fill('rag')

    listbox = page.get_by_role('listbox')
    expect(listbox).to_be_visible()
    def option(title):
        return listbox.locator(
            f'a:has(.cb-syllabus-search__suggestion-title:text-is("{title}"))',
        )

    workshop_option = option('Build a RAG assistant')
    expect(workshop_option).to_have_attribute(
        'href', f'{django_server}/workshops/rag-search-e2e',
    )
    page_option = option('Evaluate RAG answers')
    expect(page_option).to_have_attribute(
        'href', f'{django_server}/workshops/rag-search-e2e/evaluate-rag',
    )
    expect(page_option).to_contain_text('Build a RAG assistant')
    expect(option('Agents with tools')).to_have_count(0)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / 'catalog-suggestions.png'))

    # Submitting renders inline results in place of the workshop list.
    search.press('Enter')
    results = page.locator('[data-cb-syllabus-search-results]')
    expect(results).to_be_visible()
    expect(
        results.get_by_role('link', name='Build a RAG assistant', exact=True),
    ).to_be_visible()
    expect(page.locator('[data-testid="workshops-list"]')).to_be_hidden()
    assert page.url.endswith('/workshops/catalog?q=rag')

    results.get_by_role('link', name='Build a RAG assistant', exact=True).click()
    page.wait_for_url(f'{django_server}/workshops/rag-search-e2e')


@browser_journey
def test_course_home_search_suggests_unit_and_keeps_cohort(django_server, browser):
    from content.models import Cohort, CohortEnrollment, Course, Module, Unit

    user = create_user('course-home-search-e2e@test.com')
    course = Course.objects.create(
        title='Searchable course', slug='course-home-search-e2e',
        status='published', required_level=0,
    )
    module = Module.objects.create(
        course=course, title='Retrieval basics', slug='retrieval-basics',
        sort_order=1, available_after_days=0,
    )
    unit = Unit.objects.create(
        module=module, title='Vector embeddings explained',
        slug='vector-embeddings', sort_order=1,
    )
    today = datetime.date.today()
    cohort = Cohort.objects.create(
        course=course, name='Current cohort', external_key='current',
        start_date=today - datetime.timedelta(days=2),
        end_date=today + datetime.timedelta(days=60),
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    unit_url = unit.get_absolute_url()
    connection.close()

    context = auth_context(browser, user.email)
    page = context.new_page()
    page.goto(
        f'{django_server}/courses/{course.slug}/home?cohort=current',
        wait_until='domcontentloaded',
    )
    page.get_by_role('combobox', name='Search this syllabus').fill('embeddings')

    listbox = page.get_by_role('listbox')
    expect(listbox).to_be_visible()
    option = listbox.get_by_role('link').filter(has_text='Vector embeddings explained')
    expect(option).to_have_count(1)
    expect(option).to_have_attribute(
        'href', f'{django_server}{unit_url}?cohort=current',
    )
    context.close()
