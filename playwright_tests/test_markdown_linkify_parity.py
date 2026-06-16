"""Browser coverage for markdown bare-URL linkify parity (issue #1000)."""

import datetime
import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear_content():
    from content.models import (
        Course,
        Instructor,
        InterviewCategory,
        UserCourseProgress,
        Workshop,
        WorkshopPage,
    )
    from events.models import Event

    UserCourseProgress.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    Instructor.objects.all().delete()
    InterviewCategory.objects.all().delete()
    connection.close()


def _create_workshop():
    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug='linkify-workshop',
        title='Linkify Workshop',
        date=datetime.date(2026, 4, 21),
        status='published',
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description='Reference: https://example.com/workshop-notes',
    )
    WorkshopPage.objects.create(
        workshop=workshop,
        slug='setup',
        title='Setup',
        sort_order=1,
        body=(
            '## Setup\n\n'
            '- Download setup notes: https://example.com/setup\n'
            '- Read [the docs](https://example.com/docs)\n\n'
            '```\n'
            'https://example.com/raw-code-url\n'
            '```\n'
        ),
    )
    connection.close()
    return workshop


def _create_course():
    from content.models import Course, CourseInstructor, Instructor

    course = Course.objects.create(
        title='Linkify Course',
        slug='linkify-course',
        description='Course with linked instructor bio.',
        status='published',
        required_level=0,
    )
    instructor = Instructor.objects.create(
        instructor_id='linkify-instructor',
        name='Linkify Instructor',
        bio='Profile: https://example.com/instructor',
        status='published',
    )
    CourseInstructor.objects.create(
        course=course,
        instructor=instructor,
        position=0,
    )
    connection.close()
    return course


def _create_interview_category():
    from content.models import InterviewCategory

    category = InterviewCategory.objects.create(
        slug='linkify-interview',
        title='Linkify Interview',
        description='Interview prep with links.',
        status='',
        body_markdown=(
            'Study guide: https://example.com/interview-guide\n\n'
            '<!-- after-questions -->\n\n'
            'More examples: https://example.com/examples'
        ),
        sections_json=[
            {
                'id': 'practice',
                'title': 'Practice',
                'intro': 'Practice set: https://example.com/practice',
                'qa': [
                    {
                        'question': (
                            'Question text with <strong>raw</strong> HTML?'
                        ),
                    },
                ],
            },
        ],
    )
    connection.close()
    return category


@pytest.mark.django_db(transaction=True)
class TestMarkdownLinkifyParity:
    @pytest.mark.core
    def test_workshop_landing_description_linkifies_bare_url(
        self, django_server, page,
    ):
        _clear_content()
        workshop = _create_workshop()

        page.goto(f'{django_server}{workshop.get_absolute_url()}', wait_until='domcontentloaded')

        link = page.locator(
            'a[href="https://example.com/workshop-notes"]',
        )
        assert link.count() == 1
        assert link.first.get_attribute('target') == '_blank'
        rel = link.first.get_attribute('rel') or ''
        assert 'noopener' in rel
        assert 'noreferrer' in rel

    @pytest.mark.core
    def test_workshop_tutorial_linkifies_bare_url_without_corrupting_links_or_code(
        self, django_server, page,
    ):
        _clear_content()
        workshop = _create_workshop()

        page.goto(
            f'{django_server}{workshop.get_absolute_url()}/tutorial/setup',
            wait_until='domcontentloaded',
        )

        setup_link = page.locator('a[href="https://example.com/setup"]')
        assert setup_link.count() == 1
        assert setup_link.first.get_attribute('target') == '_blank'
        docs_link = page.locator('a[href="https://example.com/docs"]')
        assert docs_link.count() == 1
        assert docs_link.first.inner_text() == 'the docs'
        code = page.locator('pre').first
        assert 'https://example.com/raw-code-url' in code.inner_text()
        assert code.locator('a[href="https://example.com/raw-code-url"]').count() == 0

    @pytest.mark.core
    def test_course_detail_renders_linkified_instructor_bio(
        self, django_server, page,
    ):
        _clear_content()
        course = _create_course()

        page.goto(f'{django_server}{course.get_absolute_url()}', wait_until='domcontentloaded')

        link = page.locator('a[href="https://example.com/instructor"]')
        assert link.count() == 1
        assert link.first.get_attribute('target') == '_blank'
        rel = link.first.get_attribute('rel') or ''
        assert 'noopener' in rel
        assert 'noreferrer' in rel

    @pytest.mark.core
    def test_interview_detail_linkifies_markdown_blocks_and_escapes_questions(
        self, django_server, page,
    ):
        _clear_content()
        category = _create_interview_category()

        page.goto(f'{django_server}{category.get_absolute_url()}', wait_until='domcontentloaded')

        for url in [
            'https://example.com/interview-guide',
            'https://example.com/practice',
            'https://example.com/examples',
        ]:
            link = page.locator(f'a[href="{url}"]')
            assert link.count() == 1
            assert link.first.get_attribute('target') == '_blank'
            rel = link.first.get_attribute('rel') or ''
            assert 'noopener' in rel
            assert 'noreferrer' in rel

        question = page.locator('li:has-text("Question text with") span.pt-0\\.5')
        assert question.count() == 1
        assert question.first.inner_text() == (
            'Question text with <strong>raw</strong> HTML?'
        )
        assert '<strong>' not in question.first.inner_html()
