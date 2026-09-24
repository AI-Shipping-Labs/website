"""Browser behavior for the nested course syllabus (issue #1770)."""

import os
import re
from pathlib import Path

import pytest
from django.db import connection
from playwright.sync_api import expect

from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
pytestmark = pytest.mark.local_only


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_mobile_visitor_opens_nested_topic_and_lesson_with_keyboard(browser, django_server):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title='Nested syllabus', slug='nested-syllabus-1770',
        status='published', required_level=0,
    )
    long_week = 'Week one: ' + 'ExtraordinarilyLongTechnicalTerm' * 3
    week = Module.objects.create(
        course=course, title=long_week, slug='week-one', sort_order=1,
    )
    Module.objects.create(
        course=course, title='Week two', slug='week-two', sort_order=2,
    )
    topic = Module.objects.create(
        course=course, parent=week,
        title='Researching retrieval and structured output with a long topic name',
        slug='research', sort_order=1,
    )
    Module.objects.create(
        course=course, parent=week, title='Optional topic',
        slug='optional', sort_order=2, is_bonus=True,
    )
    lesson = Unit.objects.create(
        module=topic, title='A' * 80, slug='long-lesson',
        sort_order=1, body='A lesson.',
    )
    Unit.objects.create(
        module=topic, title='Build the retrieval pipeline', slug='homework',
        sort_order=2, kind='homework', body='Homework.',
    )
    connection.close()

    context = browser.new_context(viewport={'width': 390, 'height': 844})
    context.add_cookies([{
        'name': 'aslab_analytics_consent', 'value': 'denied',
        'url': django_server,
    }])
    page = context.new_page()
    try:
        page.goto(f'{django_server}/courses/{course.slug}', wait_until='domcontentloaded')
        weeks = page.locator('.syllabus-week')
        expect(weeks).to_have_count(2)
        first_summary = weeks.first.locator(':scope > summary')
        expect(first_summary).to_contain_text(long_week)
        expect(first_summary).to_contain_text('2 topics · 2 lessons')
        assert first_summary.bounding_box()['height'] >= 44
        assert not weeks.nth(1).evaluate('(el) => el.open')

        first_summary.focus()
        first_summary.press('Enter')
        expect(weeks.first).to_have_attribute('open', '')
        topic_details = weeks.first.locator('.syllabus-topic').first
        topic_summary = topic_details.locator(':scope > summary')
        expect(topic_summary).to_contain_text('2 lessons')
        assert topic_summary.bounding_box()['height'] >= 44
        topic_summary.focus()
        topic_summary.press('Space')
        expect(topic_details).to_have_attribute('open', '')

        lesson_link = topic_details.locator(f'[data-syllabus-unit-row][href="{lesson.get_absolute_url()}"]')
        expect(lesson_link).to_be_visible()
        assert lesson_link.bounding_box()['height'] >= 44
        expect(topic_details.get_by_text('Homework', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not weeks.nth(1).evaluate('(el) => el.open')

        topic_summary.press('Space')
        assert not topic_details.evaluate('(el) => el.open')
        assert not lesson_link.is_visible()
        assert weeks.first.evaluate('(el) => el.open')

        topic_summary.press('Enter')
        lesson_link.click()
        expect(page).to_have_url(re.compile(r'/courses/nested-syllabus-1770/week-one/research/long-lesson/?$'))
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.core
@browser_journey
def test_python_and_buildcamp_use_shared_top_level_syllabus_row_geometry(browser, django_server):
    from content.models import Course, Module, Unit

    python = Course.objects.create(
        title='Python', slug='python', status='published', required_level=0,
    )
    python_intro = Module.objects.create(
        course=python, title='Introduction', slug='introduction', sort_order=1,
    )
    Unit.objects.create(
        module=python_intro, title='Why Python', slug='why-python', sort_order=1,
    )

    buildcamp = Course.objects.create(
        title='AI Buildcamp', slug='ai-buildcamp', status='published', required_level=0,
    )
    week = Module.objects.create(
        course=buildcamp, title='Foundations', slug='week-1', sort_order=1,
    )
    session_topic = Module.objects.create(
        course=buildcamp, parent=week, title='Session 1', slug='session', sort_order=1,
    )
    Unit.objects.create(
        module=session_topic, title='Session 1', slug='session-1',
        sort_order=1, kind='event', session_position=1,
    )
    connection.close()

    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    screenshot_dir = Path('.tmp/screenshots/course-syllabus-parity')
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    row_boxes = {}
    try:
        for slug, row_title in (
            ('python', 'Why Python'),
            ('ai-buildcamp', 'Session 1'),
        ):
            page.goto(f'{django_server}/courses/{slug}', wait_until='domcontentloaded')
            page.locator('[data-testid="syllabus-module-summary"]').first.click()
            row = page.locator('[data-syllabus-unit-row]').filter(has_text=row_title).first
            expect(row).to_be_visible()
            row_boxes[slug] = row.bounding_box()
            page.screenshot(
                path=str(screenshot_dir / f'{slug}-desktop-light.png'),
                full_page=True,
            )

        assert row_boxes['python']['x'] == pytest.approx(row_boxes['ai-buildcamp']['x'])
        assert row_boxes['python']['width'] == pytest.approx(row_boxes['ai-buildcamp']['width'])
    finally:
        context.close()
