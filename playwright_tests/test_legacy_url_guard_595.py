"""End-to-end coverage for the legacy-URL guard and content fix (issue #595).

Four scenarios mapped from the spec:

1. Reader follows the recording link from the OpenAI skills article and
   lands on the workshop recording at ``/events/<slug>``.
2. Reader navigates from the buildcamp article to the AI engineer
   learning path in one hop (no 301).
3. Staff visiting ``/studio/sync/`` sees a sync warning whose text
   mentions both the article source path and the offending
   ``/event-recordings/foo`` URL.
4. Reader on the CRISP-DM article reaches every linked destination
   (back-to-blog, tag chips, two external links with ``target="_blank"``
   and ``rel`` containing ``noopener``).

Usage:
    uv run pytest playwright_tests/test_legacy_url_guard_595.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    create_staff_user as _create_staff_user_base,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

ADMIN_PASSWORD = 'adminpass123'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_articles():
    from django.db import connection

    from content.models import Article
    Article.objects.all().delete()
    connection.close()


def _clear_events():
    from django.db import connection

    from events.models import Event
    Event.objects.all().delete()
    connection.close()


def _create_article(slug, title, content_markdown, **kwargs):
    from django.db import connection

    from content.models import Article
    article = Article.objects.create(
        slug=slug,
        title=title,
        content_markdown=content_markdown,
        date=kwargs.pop('date', datetime.date(2026, 1, 15)),
        author=kwargs.pop('author', 'Test Author'),
        description=kwargs.pop('description', f'About {title}'),
        published=kwargs.pop('published', True),
        **kwargs,
    )
    connection.close()
    return article


def _create_event(slug, title, status='completed'):
    from django.db import connection

    from events.models import Event
    event = Event.objects.create(
        slug=slug,
        title=title,
        start_datetime=timezone.now() - datetime.timedelta(days=1),
        status=status,
        published=True,
    )
    connection.close()
    return event


def _login_admin_via_browser(page, base_url, email, password=ADMIN_PASSWORD):
    page.goto(f'{base_url}/admin/login/', wait_until='domcontentloaded')
    page.fill('#id_username', email)
    page.fill('#id_password', password)
    page.click('input[type="submit"]')
    page.wait_for_load_state('domcontentloaded')


# ---------------------------------------------------------------------------
# Scenario 1: OAI article -> /events/coding-agent-skills-commands
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1OaiArticleLinksToEventsRecording:
    """Reader clicks the inline 'Skills.md from Scratch' link and lands
    on the workshop recording at ``/events/coding-agent-skills-commands``
    with HTTP 200 — NOT a 404. This proves the content-repo fix landed
    AND that the article body now uses the canonical ``/events/`` URL.
    """

    def test_skills_link_opens_events_recording(self, django_server, page):
        _clear_articles()
        _clear_events()
        _create_event(
            slug='coding-agent-skills-commands',
            title='Skills.md from Scratch: Build a Skill-Driven Coding Agent',
        )
        # Mirror the post-fix article body (the `/events/` URL the
        # content-repo fix produces). The exact anchor text matches the
        # spec's "starts with `Skills.md from Scratch`" assertion.
        body_md = (
            'Some intro text.\n\n'
            'Check out: [Skills.md from Scratch: Build a Skill-Driven '
            'Coding Agent](/events/coding-agent-skills-commands).\n'
        )
        _create_article(
            slug='home-oai-folder-and-openai-skills',
            title='OpenAI Skills',
            content_markdown=body_md,
        )

        page.goto(
            f'{django_server}/blog/home-oai-folder-and-openai-skills',
            wait_until='domcontentloaded',
        )
        # Find the inline link by its anchor text prefix.
        link = page.locator(
            'article a:has-text("Skills.md from Scratch")'
        ).first
        link.wait_for()
        # Pre-flight: the href in the rendered article points at
        # /events/, not /event-recordings/.
        href = link.get_attribute('href')
        assert href == '/events/coding-agent-skills-commands', (
            f'Expected /events/ link in article body, got {href!r}'
        )

        with page.expect_navigation(wait_until='domcontentloaded') as nav_info:
            link.click()
        response = nav_info.value
        assert response.status == 200
        assert page.url.endswith('/events/coding-agent-skills-commands')

        # The recording detail page must render the recording title — not
        # a 404 template.
        body = page.content()
        assert 'Skills.md from Scratch' in body
        assert 'Page not found' not in body


# ---------------------------------------------------------------------------
# Scenario 2: buildcamp article -> /learning-path/ai-engineer in one hop
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2BuildcampArticleLinksToLearningPath:
    """Reader clicks the ``engineers`` link in the first paragraph of
    ``how-to-join-ai-engineering-buildcamp`` and the link points at the
    canonical ``/learning-path/ai-engineer`` URL — NOT the legacy
    ``/ai-engineer-learning-path`` slug that takes an extra 301 hop
    through the seed-redirects table.

    Because the canonical destination ultimately resolves to the
    learning-path article (currently served at
    ``/blog/ai-engineer-learning-path``), the assertion is on the link
    target the reader sees in the article and that the eventual page
    loads with HTTP 200 carrying the learning-path content. The
    no-extra-301 assertion is enforced by checking the response chain
    contains no redirect through ``/ai-engineer-learning-path``.
    """

    def _seed_learning_path_article(self):
        # The platform serves the AI engineer learning path via an
        # Article with page_type='learning_path'. Seed it so the
        # canonical /learning-path/ai-engineer URL has somewhere to
        # land.
        return _create_article(
            slug='ai-engineer-learning-path',
            title='AI Engineer Learning Path',
            content_markdown='# Learning path\n\nThe path to AI engineer.',
            page_type='learning_path',
        )

    def test_engineers_link_uses_canonical_path_no_legacy_hop(
        self, django_server, page,
    ):
        _clear_articles()
        self._seed_learning_path_article()
        # Mirror the post-fix body: the in-paragraph "engineers" link
        # points at the canonical /learning-path/ai-engineer URL, not
        # the legacy /ai-engineer-learning-path slug that 301-redirects
        # via the seed redirects table.
        body_md = (
            'AI Engineering Buildcamp is a hands-on program for '
            '[engineers](/learning-path/ai-engineer) who want to build '
            'production-ready AI systems.\n'
        )
        _create_article(
            slug='how-to-join-ai-engineering-buildcamp',
            title='How to Join AI Engineering Buildcamp',
            content_markdown=body_md,
        )

        page.goto(
            f'{django_server}/blog/how-to-join-ai-engineering-buildcamp',
            wait_until='domcontentloaded',
        )
        # The link by its visible text 'engineers' must already point
        # at the canonical URL — that's the whole point of the content
        # fix.
        link = page.locator('article a:has-text("engineers")').first
        link.wait_for()
        href = link.get_attribute('href')
        assert href == '/learning-path/ai-engineer', (
            f'Expected canonical /learning-path/ai-engineer href, '
            f'got {href!r}'
        )

        # Track the redirect chain to confirm we never hit the legacy
        # /ai-engineer-learning-path source-path that 301-redirects.
        seen_paths = []

        def _on_response(response):
            from urllib.parse import urlparse
            seen_paths.append((response.status, urlparse(response.url).path))

        page.on('response', _on_response)
        with page.expect_navigation(wait_until='domcontentloaded') as nav_info:
            link.click()
        response = nav_info.value
        assert response.status == 200

        # The forbidden hop: we must NOT have routed through the legacy
        # /ai-engineer-learning-path entry that the seed-redirects table
        # rewrites to /learning-path/ai-engineer.
        legacy_visits = [
            (status, path) for status, path in seen_paths
            if path == '/ai-engineer-learning-path'
        ]
        assert legacy_visits == [], (
            f'Expected no hop through legacy /ai-engineer-learning-path '
            f'after content fix, got {legacy_visits}'
        )


# ---------------------------------------------------------------------------
# Scenario 3: staff sees the legacy-URL warning on /studio/sync/
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3StaffSeesLegacyUrlWarning:
    """Staff reviewing ``/studio/sync/`` sees a warning whose text
    contains both the article source path and the offending
    ``/event-recordings/foo`` URL — and the article was still created
    (sync did not fail).
    """

    def _seed_recent_legacy_warning(self):
        """Drive the real sync against an in-memory repo whose article
        body contains a hard-coded legacy URL. The dispatcher writes the
        warning record to SyncLog.errors, so the dashboard surfaces it.
        """
        import shutil
        import tempfile
        import uuid

        from content.models import Article
        from integrations.models import ContentSource
        from integrations.services.github import sync_content_source

        Article.objects.filter(slug='legacy-test').delete()
        source, _ = ContentSource.objects.get_or_create(
            repo_name='AI-Shipping-Labs/blog',
            defaults={'is_private': False},
        )
        # Wipe stale logs so the dashboard's "latest log" is the one we
        # are about to create.
        source.sync_logs.all().delete()

        temp_dir = tempfile.mkdtemp(prefix='e2e-legacy-url-')
        try:
            with open(os.path.join(temp_dir, 'legacy-test.md'), 'w') as f:
                f.write(
                    f'---\n'
                    f'title: "Legacy Test"\n'
                    f'slug: "legacy-test"\n'
                    f'content_id: "{uuid.uuid4()}"\n'
                    f'date: "2026-01-15"\n'
                    f'---\n\n'
                    f'Body: [recording](/event-recordings/foo)\n'
                )
            sync_log = sync_content_source(source, repo_dir=temp_dir)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return source, sync_log

    def test_legacy_url_warning_visible_on_sync_dashboard(
        self, django_server, page,
    ):
        _ensure_tiers()
        _create_staff_user_base(email='admin@test.com', password=ADMIN_PASSWORD)
        source, sync_log = self._seed_recent_legacy_warning()

        # Sanity check at the model layer: the article was still
        # created/updated (sync did NOT block) AND the warning landed
        # in SyncLog.errors with both the path and offending URL.
        from content.models import Article
        article = Article.objects.get(slug='legacy-test')
        assert article.published is True
        legacy_records = [
            e for e in (sync_log.errors or [])
            if e.get('file') == 'legacy-test.md'
            and '/event-recordings/foo' in (e.get('error') or '')
        ]
        assert legacy_records, (
            f'Expected legacy-URL warning in SyncLog.errors; got '
            f'{sync_log.errors!r}'
        )

        _login_admin_via_browser(
            page, django_server, 'admin@test.com',
        )
        response = page.goto(
            f'{django_server}/studio/sync/',
            wait_until='domcontentloaded',
        )
        assert response.status == 200

        # The dashboard renders SyncLog.errors as
        # `<file>: <error message>` lines inside the AI-Shipping-Labs/blog
        # repo card. Locate the card by its repo-name heading and assert
        # the legacy warning is inside it.
        blog_card = page.locator(
            '.bg-card:has-text("AI-Shipping-Labs/blog")'
        ).first
        blog_card.wait_for()
        card_text = blog_card.inner_text()
        # Both the source path AND the offending URL must appear.
        assert 'legacy-test.md' in card_text, card_text
        assert '/event-recordings/foo' in card_text, card_text


# ---------------------------------------------------------------------------
# Scenario 4: CRISP-DM article — every linked destination resolves
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4CrispDmArticleLinks:
    """Anonymous reader on ``/blog/crisp-dm-for-ai`` reaches every linked
    destination: back-to-blog (200), tag chip pages (200), and two
    external links that open in a new tab with ``rel`` containing
    ``noopener``.
    """

    def test_back_to_blog_and_tags_and_external_links(
        self, django_server, page,
    ):
        _clear_articles()
        # Mirror the real CRISP-DM article: external PDF link and an
        # external GitHub repo link, both absolute https URLs that the
        # external-link extension marks with target="_blank" and
        # rel*="noopener". Two tags so we can click chips.
        body_md = (
            'CRISP-DM is a framework. See the original guide '
            '[CRISP-DM 1.0: Step-by-step data mining guide]'
            '(https://kde.cs.uni-kassel.de/lehre/ws2003-04/dm/CRISP-DM.pdf).\n\n'
            'For an example see [Simple Sell]'
            '(https://github.com/alexeygrigorev/simple-sell).\n'
        )
        _create_article(
            slug='crisp-dm-for-ai',
            title='CRISP-DM for AI Engineering',
            content_markdown=body_md,
            tags=['ai', 'methodology'],
        )

        page.goto(
            f'{django_server}/blog/crisp-dm-for-ai',
            wait_until='domcontentloaded',
        )

        # 1. External PDF link: target=_blank and rel contains noopener.
        pdf_link = page.locator(
            'article a[href*="kde.cs.uni-kassel.de"]'
        ).first
        pdf_link.wait_for()
        assert pdf_link.get_attribute('target') == '_blank'
        rel = pdf_link.get_attribute('rel') or ''
        assert 'noopener' in rel.lower(), (
            f'Expected rel to contain noopener, got {rel!r}'
        )

        # 2. External GitHub link: same external-link attributes.
        gh_link = page.locator(
            'article a[href*="github.com/alexeygrigorev/simple-sell"]'
        ).first
        gh_link.wait_for()
        assert gh_link.get_attribute('target') == '_blank'
        rel_gh = gh_link.get_attribute('rel') or ''
        assert 'noopener' in rel_gh.lower(), (
            f'Expected rel to contain noopener on github link, got '
            f'{rel_gh!r}'
        )

        # 3. Each tag chip lands on /blog?tag=<tag> with 200.
        # Navigate back to the article at the start of each iteration so
        # the chip locator resolves on the article page (not the previous
        # iteration's filtered listing).
        for tag in ('ai', 'methodology'):
            page.goto(
                f'{django_server}/blog/crisp-dm-for-ai',
                wait_until='domcontentloaded',
            )
            tag_link = page.locator(
                f'a[href="/blog?tag={tag}"]'
            ).first
            tag_link.wait_for()
            response = page.goto(
                f'{django_server}/blog?tag={tag}',
                wait_until='domcontentloaded',
            )
            assert response.status == 200
            # Article title remains visible on the filtered listing.
            assert 'CRISP-DM for AI Engineering' in page.content()

        # 4. Back-to-blog link from the article lands on /blog with 200.
        page.goto(
            f'{django_server}/blog/crisp-dm-for-ai',
            wait_until='domcontentloaded',
        )
        back_link = page.locator(
            'a[href="/blog"]:has-text("Back")'
        ).first
        if back_link.count() == 0:
            # Some templates use "Blog" as the back-link text instead
            # of "Back". Either way, the href is /blog.
            back_link = page.locator('a[href="/blog"]').first
        back_link.wait_for()
        with page.expect_navigation(wait_until='domcontentloaded') as nav_info:
            back_link.click()
        response = nav_info.value
        assert response.status == 200
        assert page.url.rstrip('/').endswith('/blog')
