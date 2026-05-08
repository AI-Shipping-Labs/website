"""Tests for the public Workshop surface (issue #296).

Covers:
- ``/workshops`` catalog (published only, draft hidden, tier badges,
  empty state, tag filter).
- ``/workshops/<slug>`` landing page (404 on draft / unknown, SEO content
  always rendered, landing-level paywall, pages list with locks).
- ``/workshops/<slug>/video`` recording page (gates by recording level,
  anonymous gets a paywall not a 403, recording embeds when accessible).
- ``/workshops/<slug>/tutorial/<page_slug>`` page detail (404 on bad
  page slug, prev/next ordering, gated visitors get the paywall not a
  403, body rendered when accessible).
- Sitemap includes workshops + pages, draft workshops excluded.
- Cross-links: events_list past cards switch to /workshops/<slug> when a
  workshop is linked, event_detail surfaces the writeup card.
- Workshop JSON-LD (Course schema) emitted on the landing page.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Instructor,
    Workshop,
    WorkshopInstructor,
    WorkshopPage,
)
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _attach_workshop_instructor(workshop, name, position=0):
    instructor, _ = Instructor.objects.get_or_create(
        name=name,
        defaults={
            'instructor_id': name.lower().replace(' ', '-'),
            'status': 'published',
        },
    )
    WorkshopInstructor.objects.create(
        workshop=workshop, instructor=instructor, position=position,
    )
    return instructor


def _make_event(**kwargs):
    """Create a published past Event, optionally configured as a workshop."""
    defaults = {
        'slug': 'default-event',
        'title': 'Event',
        'start_datetime': timezone.now(),
        'status': 'completed',
        'kind': 'standard',
        'recording_url': '',
        'published': True,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _make_workshop(slug='ws', title='Workshop', status='published',
                   landing=0, pages=10, recording=20, with_event=False,
                   recording_url='https://www.youtube.com/watch?v=abc',
                   materials=None, code_repo_url='', cover_image_url='',
                   description='# Hello\n\nDescription text.',
                   tags=None, instructor='Alice'):
    """Create a workshop (and optional linked event) for tests."""
    event = None
    if with_event:
        event = _make_event(
            slug=slug + '-event',
            title=title,
            kind='workshop',
            recording_url=recording_url,
            materials=materials or [],
        )
    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=date(2026, 4, 21),
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        description=description,
        code_repo_url=code_repo_url,
        cover_image_url=cover_image_url,
        tags=tags or [],
        event=event,
    )
    if instructor:
        _attach_workshop_instructor(workshop, instructor)
    return workshop


def _make_page(workshop, slug, title, sort_order, body='Hello'):
    return WorkshopPage.objects.create(
        workshop=workshop,
        slug=slug,
        title=title,
        sort_order=sort_order,
        body=body,
    )


class WorkshopsCatalogTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.published = _make_workshop(
            slug='one', title='Visible Workshop', tags=['python', 'agents'],
        )
        cls.draft = _make_workshop(
            slug='two', title='Hidden Draft', status='draft',
        )

    def test_catalog_lists_published_only(self):
        response = self.client.get('/workshops')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visible Workshop')
        self.assertNotContains(response, 'Hidden Draft')

    def test_catalog_shows_tier_badge_when_pages_gated(self):
        response = self.client.get('/workshops')
        self.assertContains(response, 'data-testid="workshop-tier-badge"')
        # Issue #481: badges read "Basic or above" not "Basic+".
        self.assertContains(response, 'Basic or above')
        self.assertNotContains(response, 'Basic+')

    def test_catalog_links_to_landing(self):
        response = self.client.get('/workshops')
        self.assertContains(response, 'href="/workshops/one"')

    def test_catalog_empty_state(self):
        Workshop.objects.all().delete()
        response = self.client.get('/workshops')
        self.assertContains(response, 'data-testid="workshops-empty-state"')
        self.assertContains(response, 'No workshops published yet')

    def test_catalog_filter_by_tag(self):
        _make_workshop(slug='three', title='Other Topic', tags=['rust'])
        response = self.client.get('/workshops?tag=rust')
        self.assertContains(response, 'Other Topic')
        self.assertNotContains(response, 'Visible Workshop')

    def test_catalog_filter_no_match_shows_empty_state(self):
        response = self.client.get('/workshops?tag=does-not-exist')
        self.assertContains(response, 'No workshops found')

    def test_catalog_missing_cover_uses_decorative_fallback_preview(self):
        response = self.client.get('/workshops')
        body = response.content.decode()
        fallback = body.split(
            'data-testid="workshop-card-preview-fallback"', 1,
        )[1].split('<div class="min-w-0 p-5', 1)[0]
        self.assertContains(response, 'data-testid="workshop-card-preview-fallback"')
        self.assertNotIn('Visible Workshop', fallback)
        self.assertNotIn('Alice', fallback)
        self.assertNotIn('Apr 21, 2026', fallback)
        self.assertNotIn('agents', fallback)
        self.assertContains(response, 'block h-full focus-visible:outline-none')
        self.assertNotContains(response, 'h-12 w-12 text-muted-foreground')

    def test_catalog_cover_image_has_alt_text_and_lazy_loading(self):
        self.published.cover_image_url = 'https://cdn.example/workshop-card.png'
        self.published.save()
        response = self.client.get('/workshops')
        self.assertContains(response, 'data-testid="workshop-card-preview-image"')
        self.assertContains(response, 'https://cdn.example/workshop-card.png')
        self.assertContains(response, 'alt="Cover image for Visible Workshop"')
        self.assertContains(response, 'loading="lazy"')
        self.assertNotContains(response, 'data-testid="workshop-card-preview-fallback"')


class WorkshopLandingTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws',
            title='Production Agents',
            with_event=True,
            code_repo_url='https://github.com/org/repo',
            cover_image_url='https://cdn.example/cover.png',
            tags=['agents'],
        )
        cls.page1 = _make_page(cls.workshop, 'intro', 'Intro', 1)
        cls.page2 = _make_page(cls.workshop, 'setup', 'Setup', 2)
        cls.page3 = _make_page(cls.workshop, 'deploy', 'Deploy', 3)

        cls.user_free = User.objects.create_user(
            email='free@x.com', password='pw', tier=cls.free_tier,
        )
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_landing_404_for_draft(self):
        Workshop.objects.create(
            slug='draft-ws', title='Draft', status='draft',
            date=date(2026, 4, 21),
        )
        response = self.client.get('/workshops/draft-ws')
        self.assertEqual(response.status_code, 404)

    def test_landing_404_for_unknown(self):
        response = self.client.get('/workshops/does-not-exist')
        self.assertEqual(response.status_code, 404)

    def test_landing_renders_seo_metadata(self):
        response = self.client.get('/workshops/ws')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Production Agents')
        self.assertContains(response, 'data-testid="workshop-title"')
        # description_html is rendered (markdown -> HTML)
        self.assertContains(response, '<h1>Hello</h1>')

    def test_landing_shows_cover_image_when_set(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'https://cdn.example/cover.png')
        self.assertContains(response, 'alt="Cover image for Production Agents"')
        self.assertContains(response, 'data-testid="workshop-detail-preview-image"')
        self.assertNotContains(response, 'data-testid="workshop-detail-preview-fallback"')

    def test_landing_missing_cover_uses_decorative_preview(self):
        ws = _make_workshop(
            slug='no-cover',
            title='No Cover Workshop',
            cover_image_url='',
            tags=['agents'],
        )
        response = self.client.get(f'/workshops/{ws.slug}')
        self.assertContains(response, 'data-testid="workshop-detail-preview-fallback"')
        self.assertNotContains(
            response,
            '<h3 class="line-clamp-2 break-words text-base font-semibold '
            'leading-snug text-foreground sm:text-lg">No Cover Workshop</h3>',
            html=True,
        )
        self.assertNotContains(response, 'h-12 w-12 text-muted-foreground')

    def test_landing_shows_instructor_and_date(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'Alice')
        self.assertContains(response, 'April 21, 2026')

    def test_landing_shows_code_repo_button(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-code-repo-link"')
        self.assertContains(response, 'https://github.com/org/repo')

    def test_landing_hides_code_repo_button_when_empty(self):
        ws = _make_workshop(slug='no-repo', title='No Repo')
        response = self.client.get(f'/workshops/{ws.slug}')
        self.assertNotContains(response, 'data-testid="workshop-code-repo-link"')

    def test_landing_anon_below_pages_gate_sees_paywall(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertContains(response, 'Upgrade to Basic to access this workshop')
        # Issue #481: paywall pill reads "Basic or above required".
        self.assertContains(response, 'Basic or above required')
        self.assertNotContains(response, 'Basic+ required')
        self.assertNotContains(response, 'data-testid="gated-current-state"')

    def test_landing_basic_user_does_not_see_pages_paywall(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws')
        self.assertNotContains(response, 'data-testid="workshop-pages-paywall"')

    def test_landing_lists_all_pages_in_order(self):
        response = self.client.get('/workshops/ws')
        body = response.content.decode()
        i_intro = body.index('Intro')
        i_setup = body.index('Setup')
        i_deploy = body.index('Deploy')
        self.assertLess(i_intro, i_setup)
        self.assertLess(i_setup, i_deploy)

    def test_landing_page_rows_show_lock_when_gated(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(
            response, 'data-testid="workshop-page-lock-icon"', count=3,
        )

    def test_landing_page_rows_link_to_tutorial(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, '/workshops/ws/tutorial/intro')
        self.assertContains(response, '/workshops/ws/tutorial/setup')
        self.assertContains(response, 'min-h-[44px]')
        self.assertContains(response, 'focus-visible:ring-2')

    def test_landing_video_card_shows_recording_tier_when_gated(self):
        # Basic user passes pages but not recording (level 20)
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'data-testid="workshop-video-locked"')

    def test_landing_event_cross_link_renders_when_event_exists(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(
            response, 'data-testid="workshop-event-cross-link"',
        )
        self.assertContains(response, '/events/ws-event')

    def test_landing_event_cross_link_hidden_when_no_event(self):
        ws = _make_workshop(slug='no-evt', title='No Event')
        response = self.client.get(f'/workshops/{ws.slug}')
        self.assertNotContains(
            response, 'data-testid="workshop-event-cross-link"',
        )

    def test_landing_landing_paywall_replaces_everything_when_landing_gated(self):
        ws = _make_workshop(
            slug='lg', title='Landing-gated',
            landing=10, pages=10, recording=20,
        )
        _make_page(ws, 'one', 'One', 1)
        # Anon user fails landing gate
        response = self.client.get(f'/workshops/{ws.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-landing-paywall"')
        # Issue #481: paywall pill uses "Basic or above required".
        self.assertContains(response, 'Basic or above required')
        # Description body is hidden
        self.assertNotContains(response, 'data-testid="workshop-description"')
        # Pages list is hidden
        self.assertNotContains(response, 'data-testid="workshop-pages-list"')

    def test_landing_premium_pages_paywall_drops_or_above(self):
        """Issue #481 AC: Premium-only paywall says "Premium required".

        Premium is the highest public tier so the paywall pill must NOT
        say "Premium or above required" — there is no higher public tier
        to upgrade to.
        """
        ws = _make_workshop(
            slug='premium-ws', title='Premium Only',
            landing=0, pages=30, recording=30,
        )
        _make_page(ws, 'one', 'One', 1)
        response = self.client.get(f'/workshops/{ws.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="workshop-pages-paywall"')
        self.assertContains(response, 'Premium required')
        self.assertNotContains(response, 'Premium or above required')
        self.assertNotContains(response, 'Premium+')

    def test_landing_emits_workshop_jsonld(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, '"@type": "Course"')
        self.assertContains(response, '"name": "Production Agents"')

    def test_landing_emits_og_tags(self):
        response = self.client.get('/workshops/ws')
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'Production Agents')


class WorkshopVideoTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws',
            title='Recording Workshop',
            with_event=True,
            recording_url='https://www.youtube.com/watch?v=abc',
            materials=[
                {'title': 'Slides', 'url': 'https://x/slides.pdf', 'type': 'pdf'},
            ],
        )
        cls.workshop.event.timestamps = [
            {'time_seconds': 0, 'label': 'Intro'},
        ]
        cls.workshop.event.transcript_text = 'Workshop transcript text.'
        cls.workshop.event.save()
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_video_404_for_draft(self):
        ws = Workshop.objects.create(
            slug='dft', title='dft', status='draft',
            date=date(2026, 4, 21),
        )
        response = self.client.get(f'/workshops/{ws.slug}/video')
        self.assertEqual(response.status_code, 404)

    def test_video_anon_below_pages_sees_recording_paywall(self):
        # Default workshop: landing=0, pages=10, recording=20.
        # Anon (level 0) passes landing only — so the recording-tier
        # paywall renders (anon is below pages too, but the recording
        # gate is what matters on the video page). Issue #515 returns 403
        # to mirror the course-unit teaser pattern.
        response = self.client.get('/workshops/ws/video')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Upgrade to Main to watch the recording',
            status_code=403,
        )

    def test_video_basic_below_recording_sees_paywall(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/video')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-paywall"', status_code=403,
        )
        # Issue #481: pill reads "Main or above required".
        self.assertContains(
            response, 'Main or above required', status_code=403,
        )
        self.assertNotContains(
            response, 'Main+ required', status_code=403,
        )
        self.assertContains(
            response, 'Current access: Basic member', status_code=403,
        )

    def test_video_main_renders_player(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertNotContains(response, 'data-testid="video-paywall"')
        self.assertContains(response, 'data-testid="video-player"')
        self.assertTemplateUsed(response, 'events/_recording_embed.html')
        self.assertContains(response, 'data-testid="video-chapters"')
        self.assertContains(response, 'class="video-timestamp')
        self.assertContains(response, 'data-time-seconds="0"')
        self.assertContains(response, 'data-source="youtube"')

    def test_video_main_renders_materials(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertTemplateUsed(response, 'events/_recording_materials.html')
        self.assertContains(response, 'data-testid="video-materials"')
        self.assertContains(response, 'Slides')

    def test_video_main_renders_transcript(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/ws/video')
        self.assertTemplateUsed(response, 'events/_recording_transcript.html')
        self.assertContains(response, 'data-testid="video-transcript"')
        self.assertContains(response, 'Workshop transcript text.')

    def test_video_landing_paywall_when_landing_gated(self):
        ws = _make_workshop(
            slug='lg', title='Landing gated',
            landing=20, pages=20, recording=20, with_event=True,
        )
        # Basic user fails landing gate (level 10 < 20)
        u = User.objects.create_user(
            email='b2@x.com', password='pw', tier=self.basic_tier,
        )
        self.client.force_login(u)
        response = self.client.get(f'/workshops/{ws.slug}/video')
        self.assertContains(response, 'data-testid="video-landing-paywall"')
        self.assertContains(response, 'Current access: Basic member')


class WorkshopPageDetailTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws', title='Tutorial Workshop',
        )
        cls.p1 = _make_page(
            cls.workshop, 'one', 'One', 1, body='# First page',
        )
        cls.p2 = _make_page(
            cls.workshop, 'two', 'Two', 2, body='Second page body',
        )
        cls.p3 = _make_page(cls.workshop, 'three', 'Three', 3)

        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )

    def test_page_404_for_draft_workshop(self):
        ws = Workshop.objects.create(
            slug='dft', title='dft', status='draft',
            date=date(2026, 4, 21),
        )
        WorkshopPage.objects.create(
            workshop=ws, slug='one', title='One', sort_order=1, body='x',
        )
        response = self.client.get(f'/workshops/{ws.slug}/tutorial/one')
        self.assertEqual(response.status_code, 404)

    def test_page_404_for_unknown_page(self):
        response = self.client.get('/workshops/ws/tutorial/nope')
        self.assertEqual(response.status_code, 404)

    def test_page_anon_returns_403_with_paywall(self):
        # Issue #515 ports the course-unit teaser pattern: gated tutorial
        # pages now return 403 (mirroring course units) and render the
        # title, breadcrumb, ~150-word teaser body, and paywall card.
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-title"', status_code=403,
        )
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Upgrade to Basic to access this workshop',
            status_code=403,
        )
        # Issue #481: paywall pill reads "Basic or above required".
        self.assertContains(
            response, 'Basic or above required', status_code=403,
        )
        self.assertNotContains(
            response, 'Basic+ required', status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="gated-current-state"', status_code=403,
        )
        # Full body must NOT render
        self.assertNotContains(
            response, 'data-testid="page-body"', status_code=403,
        )

    def test_page_free_member_sees_current_access_state(self):
        user_free = User.objects.create_user(
            email='free-page@x.com', password='pw', tier=self.free_tier,
        )
        self.client.force_login(user_free)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertContains(
            response, 'Current access: Free member', status_code=403,
        )

    def test_page_basic_renders_body(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertContains(response, 'data-testid="page-body"')
        self.assertContains(response, '<h1>First page</h1>')

    def test_page_breadcrumb_links_to_landing(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertContains(response, 'data-testid="page-breadcrumb"')
        self.assertContains(response, 'href="/workshops/ws"')

    def test_page_first_page_has_no_prev(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/one')
        self.assertNotContains(response, 'data-testid="page-prev-btn"')
        self.assertContains(response, 'data-testid="page-next-btn"')

    def test_page_middle_page_has_both(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/two')
        self.assertContains(response, 'data-testid="page-prev-btn"')
        self.assertContains(response, 'data-testid="page-next-btn"')

    def test_page_last_page_has_no_next(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/three')
        self.assertContains(response, 'data-testid="page-prev-btn"')
        self.assertNotContains(response, 'data-testid="page-next-btn"')

    def test_page_sidebar_highlights_current(self):
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/ws/tutorial/two')
        self.assertContains(response, 'data-testid="sidebar-current-page"')
        # The 'aria-current="page"' attribute is rendered on the active row
        self.assertContains(response, 'aria-current="page"')


class LegacyWorkshopPageRedirectTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='legacy-ws', title='Legacy Workshop',
        )
        cls.page = _make_page(
            cls.workshop, 'starting-notebook', 'Starting Notebook', 1,
        )
        cls.user_basic = User.objects.create_user(
            email='legacy-basic@x.com', password='pw', tier=cls.basic_tier,
        )

    def test_valid_legacy_page_redirects_permanently_to_tutorial(self):
        response = self.client.get(
            '/workshops/legacy-ws/starting-notebook',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/workshops/legacy-ws/tutorial/starting-notebook',
        )

    def test_valid_legacy_page_redirect_preserves_query_string(self):
        response = self.client.get(
            '/workshops/legacy-ws/starting-notebook?utm_source=old-link',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/workshops/legacy-ws/tutorial/starting-notebook'
            '?utm_source=old-link',
        )

    def test_redirect_target_renders_canonical_page(self):
        self.client.force_login(self.user_basic)
        response = self.client.get(
            '/workshops/legacy-ws/starting-notebook',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain,
            [
                (
                    '/workshops/legacy-ws/tutorial/starting-notebook',
                    301,
                ),
            ],
        )
        self.assertContains(response, 'data-testid="page-title"')
        self.assertContains(response, 'Starting Notebook')

    def test_canonical_tutorial_page_renders_directly(self):
        # Anonymous user fails the default pages gate (Basic+); issue #515
        # returns 403 with the teaser layout. The point of the test is
        # that we don't redirect — the gated render still happens.
        response = self.client.get(
            '/workshops/legacy-ws/tutorial/starting-notebook',
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Location', response)

    def test_video_route_is_not_captured_by_legacy_redirect(self):
        # Anonymous fails the default recording gate (Main+) so the gated
        # paywall render returns 403 (not a redirect).
        response = self.client.get('/workshops/legacy-ws/video')
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('Location', response)

    def test_unknown_workshop_stays_404(self):
        response = self.client.get(
            '/workshops/missing-workshop/starting-notebook',
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_unknown_page_stays_404(self):
        response = self.client.get('/workshops/legacy-ws/missing-page')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_draft_workshop_page_stays_404(self):
        draft = _make_workshop(
            slug='draft-legacy', title='Draft Legacy', status='draft',
        )
        _make_page(draft, 'starting-notebook', 'Starting Notebook', 1)
        response = self.client.get(
            '/workshops/draft-legacy/starting-notebook',
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)

    def test_reserved_tutorial_child_path_stays_404(self):
        _make_page(self.workshop, 'tutorial', 'Reserved Tutorial', 2)
        response = self.client.get('/workshops/legacy-ws/tutorial')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)


class EventWorkshopCrossLinksTest(TierSetupMixin, TestCase):
    """Past-event card and event-detail cross-links to /workshops."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws', title='WriteUp Workshop',
            with_event=True, landing=0, pages=0, recording=0,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_events_past_card_redirects_to_workshop(self):
        """When an event has kind='workshop' and a linked Workshop, the
        past card links to /workshops/<slug> not /events/<slug>."""
        response = self.client.get('/events?filter=past')
        self.assertContains(
            response, 'data-testid="past-card-workshop-link"',
        )
        self.assertContains(response, 'href="/workshops/ws"')

    def test_events_past_card_shows_workshop_badge(self):
        response = self.client.get('/events?filter=past')
        self.assertContains(
            response, 'data-testid="past-card-workshop-badge"',
        )

    def test_event_detail_shows_workshop_writeup_card(self):
        response = self.client.get('/events/ws-event')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="event-workshop-writeup"',
        )
        self.assertContains(
            response, 'data-testid="event-workshop-writeup-link"',
        )
        self.assertContains(response, 'href="/workshops/ws"')

    def test_orphan_workshop_event_links_back_to_event(self):
        """Event with kind='workshop' but no linked Workshop falls back to
        /events/<slug> so we don't 404."""
        Event.objects.create(
            slug='orphan-ws',
            title='Orphan',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            recording_url='https://x/y',
            published=True,
        )
        response = self.client.get('/events?filter=past')
        # Standard event link form on the orphan card
        self.assertContains(response, 'href="/events/orphan-ws"')

    def test_event_detail_no_writeup_for_standard_event(self):
        Event.objects.create(
            slug='std',
            title='Standard',
            start_datetime=timezone.now(),
            status='completed',
            kind='standard',
            recording_url='https://x/y',
            published=True,
        )
        response = self.client.get('/events/std')
        self.assertNotContains(
            response, 'data-testid="event-workshop-writeup"',
        )


class WorkshopSitemapTest(TierSetupMixin, TestCase):
    """Sitemap exposes published workshops and their pages."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop(
            slug='ws-pub', title='Pub WS',
        )
        _make_page(cls.workshop, 'page-one', 'Page One', 1)
        cls.draft = _make_workshop(
            slug='ws-draft', title='Draft WS', status='draft',
        )
        _make_page(cls.draft, 'hidden-page', 'Hidden Page', 1)

    def test_sitemap_contains_published_workshop_landing(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/workshops/ws-pub')

    def test_sitemap_contains_published_workshop_page(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(
            response, '/workshops/ws-pub/tutorial/page-one',
        )
        self.assertNotContains(response, '/workshops/ws-pub/page-one')

    def test_sitemap_excludes_draft_workshop(self):
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(response, '/workshops/ws-draft')

    def test_sitemap_excludes_draft_workshop_pages(self):
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(response, 'hidden-page')


class WorkshopPageGetAbsoluteUrlTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws = Workshop.objects.create(
            slug='abs-url',
            title='Abs URL',
            date=date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.ws, slug='page', title='P', sort_order=1, body='x',
        )

    def test_workshop_page_get_absolute_url(self):
        self.assertEqual(
            self.page.get_absolute_url(),
            '/workshops/abs-url/tutorial/page',
        )
