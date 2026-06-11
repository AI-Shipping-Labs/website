"""Tests for the workshop video <-> tutorial timestamp linking (issue #302).

Covers:
- ``parse_video_timestamp`` strict parser (valid + malformed inputs).
- ``append_query_param`` URL helper.
- ``normalize_timestamps`` handling both ``{time, title}`` (workshop YAML)
  and ``{time_seconds, label}`` (legacy / canonical) shapes.
- ``WorkshopPage.video_start`` field default + persistence.
- ``workshop_page_detail`` watch-bar visibility rules (gating + empty
  video_start + recording access).
- ``workshop_video`` ``?t=`` query parsing, inverse link rendering, and
  graceful fallback on malformed input.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Workshop, WorkshopPage
from content.templatetags.video_utils import (
    append_query_param,
    normalize_timestamps,
    parse_video_timestamp,
)
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_event(slug='ws-event', title='Workshop', recording_url='',
                timestamps=None, materials=None, recording_embed_url=''):
    return Event.objects.create(
        slug=slug,
        title=title,
        start_datetime=timezone.now(),
        status='completed',
        kind='workshop',
        recording_url=recording_url,
        recording_embed_url=recording_embed_url,
        timestamps=timestamps or [],
        materials=materials or [],
        published=True,
    )


def _make_workshop(slug='ws', title='Workshop', landing=0, pages=10,
                   recording=20, event=None):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        date=date(2026, 4, 21),
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
        status='published',
        description='Workshop body',
        event=event,
    )


# --- parse_video_timestamp tests ---------------------------------------

class ParseVideoTimestampValidTest(TestCase):
    """Strict MM:SS / H:MM:SS parser — accepted inputs."""

    def test_zero_padded_mm_ss(self):
        self.assertEqual(parse_video_timestamp('00:00'), 0)

    def test_bare_mm_ss(self):
        self.assertEqual(parse_video_timestamp('0:00'), 0)

    def test_minutes_seconds(self):
        self.assertEqual(parse_video_timestamp('16:00'), 960)

    def test_one_hour(self):
        self.assertEqual(parse_video_timestamp('1:00:00'), 3600)

    def test_one_hour_twenty_three_minutes(self):
        self.assertEqual(parse_video_timestamp('1:23:45'), 5025)

    def test_three_digit_hour(self):
        self.assertEqual(parse_video_timestamp('100:00:00'), 360000)

    def test_mm_ss_minutes_above_sixty_allowed(self):
        # MM:SS may legitimately have minutes >= 60 (an old YAML quirk
        # where authors wrote "75:00" instead of "1:15:00"). Accept it.
        self.assertEqual(parse_video_timestamp('75:00'), 4500)

    def test_strip_whitespace(self):
        self.assertEqual(parse_video_timestamp('  16:00  '), 960)


class ParseVideoTimestampInvalidTest(TestCase):
    """Strict MM:SS / H:MM:SS parser — rejected inputs raise ValueError."""

    def test_empty_string(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('')

    def test_whitespace_only(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('   ')

    def test_none(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp(None)

    def test_integer_value(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp(60)

    def test_single_component(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('16')

    def test_too_many_components(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('1:2:3:4')

    def test_alpha_components(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('abc:def')

    def test_negative_minutes(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('-1:00')

    def test_signed_seconds(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('1:+30')

    def test_h_mm_ss_minutes_too_high(self):
        # In H:MM:SS, minutes must be < 60.
        with self.assertRaises(ValueError):
            parse_video_timestamp('1:60:00')

    def test_h_mm_ss_seconds_too_high(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('1:00:60')

    def test_decimal_components(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('1.5:00')

    def test_empty_components(self):
        with self.assertRaises(ValueError):
            parse_video_timestamp('1::00')


# --- append_query_param tests -----------------------------------------

class AppendQueryParamTest(TestCase):
    def test_appends_first_param_with_question_mark(self):
        self.assertEqual(
            append_query_param('https://x/y', 'start', 60),
            'https://x/y?start=60',
        )

    def test_appends_second_param_with_ampersand(self):
        self.assertEqual(
            append_query_param('https://x/y?foo=1', 'start', 60),
            'https://x/y?foo=1&start=60',
        )

    def test_returns_url_when_value_none(self):
        self.assertEqual(
            append_query_param('https://x/y', 'start', None),
            'https://x/y',
        )

    def test_returns_empty_when_url_empty(self):
        self.assertEqual(append_query_param('', 'start', 60), '')

    def test_start_param_carries_no_autoplay(self):
        """Issue #899: the legacy iframe cue must only add ``start=N``.

        ``allow="autoplay"`` on the iframe permits playback but does not
        force it; ``start=N`` cues without auto-playing. Guard that the
        helper used to build ``recording_embed_url_with_start`` never
        injects an autoplay-triggering query param.
        """
        url = append_query_param('https://player.vimeo.com/video/123', 'start', 960)
        self.assertIn('start=960', url)
        self.assertNotIn('autoplay', url)
        self.assertNotIn('autoStart', url)
        self.assertNotIn('autostart', url)


# --- normalize_timestamps tests ---------------------------------------

class NormalizeTimestampsTest(TestCase):
    """Both timestamp dict shapes converge on the canonical form."""

    def test_workshop_yaml_shape(self):
        result = normalize_timestamps([
            {'time': '0:00', 'title': 'Intro'},
            {'time': '16:00', 'title': 'Setup'},
        ])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['time_seconds'], 0)
        self.assertEqual(result[0]['label'], 'Intro')
        self.assertEqual(result[0]['formatted_time'], '[00:00]')
        self.assertEqual(result[1]['time_seconds'], 960)
        self.assertEqual(result[1]['label'], 'Setup')

    def test_legacy_recording_shape(self):
        result = normalize_timestamps([
            {'time_seconds': 0, 'label': 'Intro'},
            {'time_seconds': 125, 'label': 'Build'},
        ])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['time_seconds'], 0)
        self.assertEqual(result[1]['time_seconds'], 125)
        self.assertEqual(result[1]['label'], 'Build')

    def test_skips_unparseable_workshop_entries(self):
        result = normalize_timestamps([
            {'time': 'not-a-time', 'title': 'broken'},
            {'time': '16:00', 'title': 'ok'},
        ])
        # Bad row is skipped, good row survives.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['time_seconds'], 960)

    def test_empty_list(self):
        self.assertEqual(normalize_timestamps([]), [])

    def test_none(self):
        self.assertEqual(normalize_timestamps(None), [])


# --- WorkshopPage.video_start field tests -----------------------------

class WorkshopPageVideoStartFieldTest(TierSetupMixin, TestCase):
    """The video_start field is additive — empty by default and stored verbatim."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.workshop = _make_workshop()

    def test_default_is_empty_string(self):
        page = WorkshopPage.objects.create(
            workshop=self.workshop, slug='p1', title='P1', sort_order=1,
            body='hi',
        )
        self.assertEqual(page.video_start, '')

    def test_value_persists_verbatim(self):
        # Stored as a string (not parsed to int) so the templates can
        # still display the original "16:00" form to the reader.
        page = WorkshopPage.objects.create(
            workshop=self.workshop, slug='p2', title='P2', sort_order=2,
            body='hi', video_start='16:00',
        )
        page.refresh_from_db()
        self.assertEqual(page.video_start, '16:00')


# --- workshop_page_detail watch-bar visibility tests ------------------

class WatchBarVisibilityTest(TierSetupMixin, TestCase):
    """The watch bar shows iff: video_start is set AND recording access
    AND page itself isn't gated."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='wb-event',
            title='WB',
            recording_url='https://www.youtube.com/watch?v=abc',
            timestamps=[
                {'time': '0:00', 'title': 'Start'},
                {'time': '16:00', 'title': 'Setup'},
            ],
        )
        cls.workshop = _make_workshop(slug='wb', event=cls.event)
        cls.page_no_ts = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='no-ts', title='No timestamp',
            sort_order=1, body='hi',
        )
        cls.page_with_ts = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup', title='Setup',
            sort_order=2, body='hi', video_start='16:00',
        )
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_main_user_sees_watch_bar_when_video_start_set(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-wb/tutorial/setup')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="watch-this-section"')
        # Bar links to the video page with the same ?t= value.
        self.assertContains(response, 'href="/workshops/2026-04-21-wb/video?t=16:00"')
        # Visible label uses the original MM:SS string.
        self.assertContains(response, 'Watch this section (16:00)')

    def test_main_user_no_bar_when_video_start_empty(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-wb/tutorial/no-ts')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="watch-this-section"')

    def test_basic_user_passes_pages_but_not_recording_no_bar(self):
        # Basic = level 10, pages gate = 10, recording gate = 20.
        # Page renders, but watch bar must not (recording gate fails).
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/2026-04-21-wb/tutorial/setup')
        self.assertContains(response, 'data-testid="page-body"')
        self.assertNotContains(response, 'data-testid="watch-this-section"')

    def test_anon_no_bar_even_if_video_start_set(self):
        # Anonymous = level 0, fails both gates => paywall + no bar.
        # Issue #515: gated tutorial pages return 403.
        response = self.client.get('/workshops/2026-04-21-wb/tutorial/setup')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="page-paywall"', status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="watch-this-section"', status_code=403,
        )


# --- workshop_video ?t= and inverse-links tests -----------------------

class WorkshopVideoTimestampLinksTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='vl-event',
            title='Video Links',
            recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            timestamps=[
                {'time': '0:00', 'title': 'Welcome'},
                {'time': '8:30', 'title': 'No matching page'},
                {'time': '16:00', 'title': 'Setup'},
            ],
        )
        cls.workshop = _make_workshop(slug='vl', event=cls.event)
        cls.p_first = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro Page',
            sort_order=1, body='hi', video_start='0:00',
        )
        cls.p_second = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup-page', title='Setup Page',
            sort_order=2, body='hi', video_start='16:00',
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )
        cls.user_basic = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )

    def test_inverse_links_render_for_matching_timestamps(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video')
        self.assertContains(response, 'data-testid="video-chapters"')
        self.assertContains(
            response,
            'data-testid="timestamp-tutorial-link"',
            count=2,
        )
        # Both linked pages are reachable from the timestamps panel.
        self.assertContains(
            response, 'href="/workshops/2026-04-21-vl/tutorial/intro"',
        )
        self.assertContains(
            response, 'href="/workshops/2026-04-21-vl/tutorial/setup-page"',
        )
        self.assertContains(response, 'Tutorial: Intro Page')
        self.assertContains(response, 'Tutorial: Setup Page')

    def test_unmatched_timestamp_has_no_tutorial_link(self):
        # The 8:30 (== 510s) timestamp has no corresponding page so the
        # tutorial sub-link must be absent. We assert this by counting
        # links above (== 2, not 3).
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video')
        # Sanity check: the 8:30 row label still renders.
        self.assertContains(response, 'No matching page')

    def test_query_t_propagates_to_youtube_player_vars(self):
        # ?t=16:00 -> 960 seconds -> rendered into playerVars.start.
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video?t=16:00')
        self.assertEqual(response.status_code, 200)
        # Look for the start: 960 line inside the playerVars object.
        self.assertContains(response, 'start: 960')

    def test_youtube_player_does_not_autoplay_on_load(self):
        # Issue #899: the YT player is created with start=960 (cued) but
        # must not request autoplay nor call playVideo() on load — it
        # arrives parked at the offset and paused.
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video?t=16:00')
        html = response.content.decode()
        self.assertIn('start: 960', html)
        # No autoplay playerVar and no programmatic play on load.
        self.assertNotIn('autoplay', html)
        self.assertNotIn('playVideo()', html)

    def test_malformed_t_does_not_break_page(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video?t=not-a-time')
        self.assertEqual(response.status_code, 200)
        # No start parameter rendered when ?t= was unparseable.
        self.assertNotContains(response, 'start:')

    def test_no_t_param_omits_start(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-vl/video')
        self.assertNotContains(response, 'start:')

    def test_paywalled_user_does_not_get_inverse_links(self):
        # Below the recording gate the paywall renders and the inverse
        # links section is absent (no timestamps panel either).
        # Issue #515: gated workshop video page returns 403.
        self.client.force_login(self.user_basic)
        response = self.client.get('/workshops/2026-04-21-vl/video?t=16:00')
        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response, 'data-testid="video-paywall"', status_code=403,
        )
        self.assertNotContains(
            response, 'data-testid="video-chapters"', status_code=403,
        )
        self.assertNotContains(
            response, 'start: 960', status_code=403,
        )


class WorkshopVideoDuplicateVideoStartTest(TierSetupMixin, TestCase):
    """When two pages claim the same video_start, the lowest sort_order wins."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='dup-event',
            recording_url='https://www.youtube.com/watch?v=abc',
            timestamps=[{'time': '0:00', 'title': 'Start'}],
        )
        cls.workshop = _make_workshop(slug='dup', event=cls.event)
        # Both pages claim "0:00" — we expect the sort_order=1 page to
        # be the one linked from the video page.
        cls.first = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='first', title='First Page',
            sort_order=1, body='hi', video_start='0:00',
        )
        cls.second = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='second', title='Second Page',
            sort_order=2, body='hi', video_start='0:00',
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_lowest_sort_order_page_wins(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-dup/video')
        # First page link rendered, second page is silently ignored.
        self.assertContains(response, 'href="/workshops/2026-04-21-dup/tutorial/first"')
        self.assertNotContains(
            response, 'href="/workshops/2026-04-21-dup/tutorial/second"',
        )
        self.assertContains(
            response, 'data-testid="timestamp-tutorial-link"', count=1,
        )


class WorkshopVideoLegacyTimestampShapeTest(TierSetupMixin, TestCase):
    """Legacy ``{time_seconds, label}`` events still render correctly."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='leg-event',
            recording_url='https://www.youtube.com/watch?v=abc',
            # Legacy shape used by classic event recordings.
            timestamps=[
                {'time_seconds': 0, 'label': 'Welcome'},
                {'time_seconds': 960, 'label': 'Setup'},
            ],
        )
        cls.workshop = _make_workshop(slug='leg', event=cls.event)
        cls.page_setup = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='setup', title='Setup Page',
            sort_order=1, body='hi', video_start='16:00',
        )
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_legacy_shape_links_to_tutorial(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-leg/video')
        # The 960s legacy timestamp matches video_start="16:00".
        self.assertContains(
            response, 'href="/workshops/2026-04-21-leg/tutorial/setup"',
        )
        self.assertContains(response, 'Tutorial: Setup Page')


class WorkshopVideoFallbackEmbedStartTest(TierSetupMixin, TestCase):
    """Fallback iframe path also receives ``?start=`` from the ?t= param."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='fb-event',
            # No recording_url — only an embed URL, exercising the
            # fallback iframe path.
            recording_url='',
            recording_embed_url='https://drive.example.com/embed/xyz',
        )
        cls.workshop = _make_workshop(slug='fb', event=cls.event)
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_fallback_iframe_url_carries_start(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-fb/video?t=16:00')
        self.assertEqual(response.status_code, 200)
        # The augmented URL is rendered into the iframe src.
        self.assertContains(
            response, 'https://drive.example.com/embed/xyz?start=960',
        )

    def test_fallback_iframe_url_requests_no_autoplay(self):
        # Issue #899: the legacy iframe is cued via start=N but must never
        # request autoplay through the constructed URL. The allow=autoplay
        # attribute permits playback but does not force it.
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-fb/video?t=16:00')
        html = response.content.decode()
        # The constructed src carries start= but no autoplay param.
        self.assertIn('embed/xyz?start=960', html)
        self.assertNotIn('autoplay=1', html)
        self.assertNotIn('autoStart=1', html)

    def test_fallback_iframe_unchanged_without_t(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-fb/video')
        self.assertContains(
            response, 'src="https://drive.example.com/embed/xyz"',
        )


class WorkshopVideoSelfHostedCueTest(TierSetupMixin, TestCase):
    """Self-hosted <video> is cued to ?t= on load but never auto-plays.

    Issue #899: the live paused/currentTime state is asserted via
    Playwright (DOM media API). These view tests lock in the rendered
    markup contract the JS depends on: the cue script is present with
    the right offset when ?t= is supplied, absent otherwise, and the
    <video> element never carries the autoplay attribute.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event = _make_event(
            slug='sh-event',
            recording_url='https://cdn.example.com/recordings/ws.mp4',
            timestamps=[
                {'time': '0:00', 'title': 'Welcome'},
                {'time': '16:00', 'title': 'Setup'},
            ],
        )
        cls.workshop = _make_workshop(slug='sh', event=cls.event)
        cls.user_main = User.objects.create_user(
            email='main@x.com', password='pw', tier=cls.main_tier,
        )

    def test_self_hosted_video_element_renders(self):
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-sh/video')
        self.assertContains(response, 'id="video-player-self-hosted"')

    def test_self_hosted_video_has_no_autoplay_attribute(self):
        # The <video> tag must remain controls/preload only — never
        # autoplay — regardless of whether ?t= is supplied.
        self.client.force_login(self.user_main)
        for url in (
            '/workshops/2026-04-21-sh/video',
            '/workshops/2026-04-21-sh/video?t=16:00',
        ):
            response = self.client.get(url)
            html = response.content.decode()
            # Isolate the <video ...> opening tag and assert no autoplay.
            start = html.index('<video')
            tag = html[start:html.index('>', start) + 1]
            self.assertNotIn('autoplay', tag, msg=f'autoplay on {url}')

    def test_cue_script_sets_currenttime_without_play_when_t_present(self):
        # ?t=16:00 -> 960s. The initial-load cue script must set
        # currentTime to 960 and must NOT call video.play() — that
        # would auto-play. (The chapter-click handler still plays, but
        # that handler is keyed on .video-timestamp clicks, not load.)
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-sh/video?t=16:00')
        html = response.content.decode()
        self.assertIn('var startSeconds = 960;', html)
        self.assertIn('video.currentTime = startSeconds;', html)
        # The load-time cue block (between the cue marker and the click
        # handlers) must not auto-play. The chapter-click handler's
        # video.play() lives in a separate <script> and is allowed. We
        # strip JS line comments first so explanatory prose mentioning
        # play() doesn't trip the assertion — only an actual invocation
        # (`video.play();`) counts.
        cue_block_start = html.index('var startSeconds = 960;')
        cue_block_end = html.index('Timestamp click handlers')
        cue_block = html[cue_block_start:cue_block_end]
        code_only = '\n'.join(
            line.split('//', 1)[0] for line in cue_block.splitlines()
        )
        self.assertNotIn('.play(', code_only)

    def test_no_cue_script_without_t_param(self):
        # Without ?t= the load-time cue script is not emitted at all, so
        # the player parks at 0 (paused) with no JS touching currentTime.
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-sh/video')
        html = response.content.decode()
        self.assertNotIn('var startSeconds', html)

    def test_chapter_click_handler_still_seeks_and_plays(self):
        # Boundary guard: the explicit chapter-click handler keeps its
        # seek-AND-play behavior (this issue only governs initial load).
        self.client.force_login(self.user_main)
        response = self.client.get('/workshops/2026-04-21-sh/video')
        html = response.content.decode()
        self.assertIn("source === 'self_hosted'", html)
        self.assertIn('video.play();', html)
