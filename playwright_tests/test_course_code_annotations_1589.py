"""Reader journeys for structured course code annotations (issue #1589)."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = pytest.mark.local_only


ANNOTATED_BODY = """First example:

```python
name = "Ada"

message = f"Hello, {name}"
print(message)
```
<!--
structured: true
code_annotations:
  - line: 1
    text: Choose the name used by the greeting.
  - lines: "2-3"
    text: The blank line counts before the message is built.
-->

Second example:

```bash
python app.py --with-a-deliberately-long-option-that-needs-horizontal-scrolling
```
<!--
structured: true
code_annotations:
  - line: 1
    text: Run the completed program from your terminal.
-->

Ordinary example:

```text
copy only this ordinary code
```
"""


def _seed_reader():
    from django.db import connection

    from content.models import Course, Module, Unit

    email = 'annotation-reader@example.com'
    create_user(email, tier_slug='free')
    course = Course.objects.create(
        title='Code annotation course',
        slug='code-annotation-course',
        status='published',
        required_level=0,
    )
    module = Module.objects.create(
        course=course,
        title='Examples',
        slug='examples',
    )
    unit = Unit.objects.create(
        module=module,
        title='Annotated examples',
        slug='annotated-examples',
        body=ANNOTATED_BODY,
    )
    connection.close()
    return email, unit.get_absolute_url()


def _open_reader(browser, django_server, email, path, viewport=None):
    context = auth_context(browser, email)
    context.grant_permissions(
        ['clipboard-read', 'clipboard-write'],
        origin=django_server,
    )
    page = context.new_page()
    if viewport:
        page.set_viewport_size(viewport)
    page.goto(f'{django_server}{path}', wait_until='domcontentloaded')
    return context, page


@pytest.mark.django_db(transaction=True)
class TestCourseCodeAnnotationsReader:
    @browser_journey
    def test_reader_attaches_highlights_and_ordered_notes_to_each_block(
        self,
        browser,
        django_server,
    ):
        email, path = _seed_reader()
        context, page = _open_reader(browser, django_server, email, path)

        blocks = page.locator('.annotated-code-block')
        assert blocks.count() == 2
        first = blocks.nth(0)
        assert first.locator('.code-line-number').count() == 4
        assert first.locator('.code-annotation-line.is-highlighted').count() == 3
        assert first.locator('[data-line-number="2"].is-highlighted').count() == 1

        groups = page.get_by_test_id('code-annotations')
        assert groups.count() == 2
        assert page.get_by_role(
            'complementary', name='Code annotations'
        ).count() == 2
        assert groups.nth(0).get_by_role('heading').inner_text() == 'Code annotations'
        first_notes = groups.nth(0).get_by_test_id('code-annotation-note')
        assert first_notes.count() == 2
        assert first_notes.nth(0).inner_text().startswith('Line 1:')
        assert first_notes.nth(1).inner_text().startswith('Lines 2–3:')
        assert 'Run the completed program' not in groups.nth(0).inner_text()
        assert groups.nth(1).get_by_test_id('code-annotation-note').count() == 1

        body = page.get_by_test_id('course-unit-body')
        assert 'structured: true' not in body.inner_text()
        assert '<!--' not in body.inner_text()
        context.close()

    @browser_journey
    def test_keyboard_copy_uses_only_raw_code_and_ordinary_fence_stays_plain(
        self,
        browser,
        django_server,
    ):
        email, path = _seed_reader()
        context, page = _open_reader(browser, django_server, email, path)

        buttons = page.get_by_test_id('code-copy-btn')
        assert buttons.count() == 3
        first_button = buttons.nth(0)
        first_button.focus()
        assert first_button.get_attribute('aria-label') == 'Copy code to clipboard'
        assert first_button.evaluate(
            "button => getComputedStyle(button).outlineStyle"
        ) != 'none'
        first_button.press('Enter')
        expect(first_button).to_have_text('Copied!')
        assert page.evaluate('navigator.clipboard.readText()') == (
            'name = "Ada"\n\nmessage = f"Hello, {name}"\nprint(message)'
        )

        first_code = page.locator('.annotated-code-block code').nth(0)
        assert first_code.get_attribute('aria-hidden') is None
        assert first_code.text_content().count('name = "Ada"') == 1
        assert first_code.locator('.code-line-number').count() == 0
        assert page.locator('.annotated-code-block .code-line-gutter').nth(
            0
        ).get_attribute('aria-hidden') == 'true'

        ordinary_wrapper = page.locator('.code-copy-wrapper').nth(2)
        assert ordinary_wrapper.locator('.annotated-code-block').count() == 0
        assert ordinary_wrapper.locator('.code-line-gutter').count() == 0
        ordinary_button = buttons.nth(2)
        ordinary_button.focus()
        ordinary_button.press('Enter')
        expect(ordinary_button).to_have_text('Copied!')
        assert page.evaluate('navigator.clipboard.readText()') == (
            'copy only this ordinary code'
        )
        context.close()

    @browser_journey
    def test_mobile_notes_fit_reader_while_long_code_scrolls_and_copies(
        self,
        browser,
        django_server,
    ):
        email, path = _seed_reader()
        context, page = _open_reader(
            browser,
            django_server,
            email,
            path,
            viewport={'width': 390, 'height': 844},
        )

        second_block = page.locator('.annotated-code-block').nth(1)
        overflow = second_block.evaluate(
            'element => ({scroll: element.scrollWidth, client: element.clientWidth})'
        )
        assert overflow['scroll'] > overflow['client']
        second_block.evaluate('element => { element.scrollLeft = 80; }')
        assert second_block.evaluate('element => element.scrollLeft') > 0

        note_group = page.get_by_test_id('code-annotations').nth(1)
        note_width = note_group.evaluate(
            'element => ({scroll: element.scrollWidth, client: element.clientWidth})'
        )
        assert note_width['scroll'] <= note_width['client'] + 1
        assert note_group.get_by_text(
            'Run the completed program from your terminal.'
        ).is_visible()

        copy_button = page.get_by_test_id('code-copy-btn').nth(1)
        assert copy_button.is_visible()
        copy_button.click()
        expect(copy_button).to_have_text('Copied!')
        assert page.evaluate('navigator.clipboard.readText()') == (
            'python app.py '
            '--with-a-deliberately-long-option-that-needs-horizontal-scrolling'
        )
        context.close()
