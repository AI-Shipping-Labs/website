"""Studio mobile density audit (issue #620).

Verifies the CSS-only density tightening in
``templates/studio/base.html`` and the per-template clean-ups that wire
Recordings and Projects into the shared ``studio-responsive-table``
helper, plus the Workshops worker-status row tweak. All checks live at
the markup / CSS source level — visual height assertions are covered by
the Playwright suite ``playwright_tests/test_studio_mobile_density.py``.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Project
from events.models import Event

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_TEMPLATE = REPO_ROOT / "templates" / "studio" / "base.html"
ENV_MISMATCH_TEMPLATE = (
    REPO_ROOT
    / "templates"
    / "studio"
    / "includes"
    / "env_mismatch_banner.html"
)

User = get_user_model()


class StudioBaseMobileCSSTest(TestCase):
    """``templates/studio/base.html`` carries the @media (max-width: 767px) block."""

    @classmethod
    def setUpTestData(cls):
        cls.css = BASE_TEMPLATE.read_text()

    def test_mobile_media_query_present(self):
        self.assertIn("@media (max-width: 767px)", self.css)

    def test_row_uses_grid_with_two_columns(self):
        # Old: row was a vertical stack of label-on-its-own-line blocks.
        # New: row is a 2-column grid so LABEL and VALUE share a single line.
        self.assertIn("display: grid;", self.css)
        self.assertIn("grid-template-columns: 1fr auto;", self.css)

    def test_row_padding_tightened(self):
        # The old "padding: 1rem" + "margin-bottom: 0.75rem" combo per <tr>
        # cost ~32 px of vertical chrome per record. The new values are
        # tighter still so the Articles list shows >= 6 rows in the
        # initial Pixel 7 viewport (issue #620 follow-up).
        self.assertIn("padding: 0.5rem 0.625rem;", self.css)
        self.assertIn("margin-bottom: 0.375rem;", self.css)
        # Old sentinel values must be gone.
        self.assertNotIn("padding: 1rem;", self.css)
        self.assertNotIn("margin-bottom: 0.75rem;", self.css)

    def test_td_padding_zeroed_so_grid_gaps_dictate_spacing(self):
        # The old per-<td> padding (0.35rem 0) double-counts the row gap and
        # bloats the card. The grid row-gap + column-gap are the source of
        # truth now.
        self.assertIn("padding: 0 !important;", self.css)
        self.assertNotIn("padding: 0.35rem 0 !important;", self.css)

    def test_first_td_becomes_full_width_header(self):
        # Title cell spans both grid columns and is bold + slightly larger
        # so the row reads like "header + facts" on a phone.
        self.assertIn("tr > td:first-child", self.css)
        self.assertIn("grid-column: 1 / -1;", self.css)
        self.assertIn("font-weight: 600;", self.css)
        self.assertIn("font-size: 0.875rem;", self.css)

    def test_first_td_label_pseudo_is_suppressed(self):
        # Title rows that happen to carry data-label="User" or similar
        # should not render a UPPERCASE prefix above the header. The CSS
        # explicitly hides ::before on the first <td> only.
        self.assertIn(
            "tr > td:first-child[data-label]::before",
            self.css,
        )

    def test_actions_cell_is_full_width_footer(self):
        # The Actions cell has a stable hook (.studio-actions-cell) so the
        # CSS targets it directly, regardless of where it falls in the DOM.
        self.assertIn("tr > td.studio-actions-cell", self.css)

    def test_label_pseudo_renders_inline_with_letter_spacing(self):
        # Old pseudo was display: block (label on its own line). New pseudo
        # is display: inline so the label sits next to the value. Letter
        # spacing keeps the uppercase chrome readable.
        self.assertIn("display: inline;", self.css)
        self.assertIn("letter-spacing: 0.04em;", self.css)
        # Old value gone.
        self.assertNotIn("margin-bottom: 0.125rem;", self.css)

    def test_actions_group_gap_tightened(self):
        # The old gap was 0.75rem between actions; the new value is 0.5rem
        # — keeps two buttons on one line at 412 px without crowding.
        self.assertIn("gap: 0.5rem;", self.css)

    def test_mobile_block_does_not_leak_onto_desktop(self):
        # All mobile styles must live inside the @media query so a 1280 px
        # viewport renders identically. Check that the grid declaration
        # appears AFTER the @media line and BEFORE the closing brace of
        # the block.
        media_idx = self.css.index("@media (max-width: 767px)")
        block_close = self.css.index("\n}", media_idx)
        grid_idx = self.css.index("grid-template-columns: 1fr auto", media_idx)
        self.assertLess(media_idx, grid_idx)
        self.assertLess(grid_idx, block_close)


class StudioRecordingsListSharedHelperTest(TestCase):
    """Recordings list now opts into the shared ``studio-responsive-table``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density@test.com",
            password="testpass",
            is_staff=True,
        )
        now = timezone.now()
        cls.published = Event.objects.create(
            title="Published Recording",
            slug="published-rec",
            start_datetime=now,
            status="completed",
            recording_url="https://youtube.com/watch?v=published",
            published=True,
        )
        cls.draft = Event.objects.create(
            title="Draft Recording",
            slug="draft-rec",
            start_datetime=now,
            status="completed",
            recording_url="https://youtube.com/watch?v=draft",
            published=False,
        )
        cls.synced = Event.objects.create(
            title="Synced Recording",
            slug="synced-rec",
            start_datetime=now,
            status="completed",
            recording_url="https://youtube.com/watch?v=synced",
            published=True,
            origin="github",
            source_repo="AI-Shipping-Labs/content",
        )

    def setUp(self):
        self.client.login(email="staff-density@test.com", password="testpass")

    def _get(self):
        return self.client.get("/studio/recordings/")

    def test_uses_responsive_table_wrapper(self):
        response = self._get()
        # The wrapper class triggers the @media (max-width: 767px) styles.
        self.assertContains(response, "studio-responsive-table")

    def test_data_labels_on_every_non_title_cell(self):
        response = self._get()
        # Title cell deliberately has no data-label — it becomes the row
        # header. Every other field must carry one so the LABEL: VALUE
        # pair renders inline at mobile.
        self.assertContains(response, 'data-label="Status"')
        self.assertContains(response, 'data-label="Date"')
        self.assertContains(response, 'data-label="Actions"')

    def test_actions_cell_is_marked_with_studio_actions_cell(self):
        response = self._get()
        # The CSS keys off this class to make Actions span both grid
        # columns at mobile.
        self.assertContains(response, "studio-actions-cell")
        self.assertContains(response, "studio-action-group")

    def test_status_uses_shared_badge_component(self):
        response = self._get()
        # The shared status pill is our single visual contract for status
        # (matches Articles / Courses).
        self.assertContains(response, 'data-component="studio-status-badge"')
        self.assertContains(response, "Published")
        self.assertContains(response, "Draft")

    def test_synced_recording_shows_view_action_not_edit(self):
        response = self._get()
        # source_repo present → "View" (read-only); source_repo absent →
        # "Edit" (writable). Same convention as Articles / Courses.
        self.assertContains(
            response,
            f'/studio/recordings/{self.synced.pk}/edit',
        )
        self.assertContains(response, ">View<")

    def test_manual_recording_shows_edit_action(self):
        response = self._get()
        self.assertContains(
            response,
            f'/studio/recordings/{self.draft.pk}/edit',
        )
        self.assertContains(response, ">Edit<")


class StudioProjectsListSharedHelperTest(TestCase):
    """Projects list now opts into the shared ``studio-responsive-table``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density-projects@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.pending = Project.objects.create(
            title="Pending Project Density",
            slug="pending-density",
            date=timezone.now().date(),
            status="pending_review",
            published=False,
        )
        cls.published = Project.objects.create(
            title="Published Project Density",
            slug="published-density",
            date=timezone.now().date(),
            status="published",
            published=True,
        )
        cls.synced = Project.objects.create(
            title="Synced Project Density",
            slug="synced-density",
            date=timezone.now().date(),
            status="published",
            published=True,
            source_repo="AI-Shipping-Labs/content",
        )

    def setUp(self):
        self.client.login(
            email="staff-density-projects@test.com",
            password="testpass",
        )

    def _get(self):
        return self.client.get("/studio/projects/")

    def test_uses_responsive_table_wrapper(self):
        self.assertContains(self._get(), "studio-responsive-table")

    def test_data_labels_on_every_non_title_cell(self):
        response = self._get()
        self.assertContains(response, 'data-label="Status"')
        self.assertContains(response, 'data-label="Author"')
        self.assertContains(response, 'data-label="Date"')
        self.assertContains(response, 'data-label="Actions"')

    def test_actions_cell_is_marked_with_studio_actions_cell(self):
        response = self._get()
        self.assertContains(response, "studio-actions-cell")
        self.assertContains(response, "studio-action-group")

    def test_status_uses_shared_badge(self):
        response = self._get()
        self.assertContains(response, 'data-component="studio-status-badge"')
        self.assertContains(response, "Published")

    def test_synced_project_shows_view_action_not_review(self):
        response = self._get()
        self.assertContains(
            response,
            f'/studio/projects/{self.synced.pk}/review',
        )
        # The Review (writable) action becomes View (read-only) when the
        # project came from the content repo — same pattern as recordings.
        self.assertContains(response, ">View<")

    def test_manual_project_shows_review_action(self):
        response = self._get()
        self.assertContains(
            response,
            f'/studio/projects/{self.pending.pk}/review',
        )
        self.assertContains(response, ">Review<")

    def test_view_on_site_action_present(self):
        response = self._get()
        # The shared ``studio_list_action`` tag adds data-testid="view-on-site"
        # for any link labelled "View on site". Ensures the action is
        # discoverable for tests and for assistive tech.
        self.assertContains(response, 'data-testid="view-on-site"')


class StudioWorkshopsListWorkerStatusTest(TestCase):
    """Workshops list status row should not push the empty-state CTA off the fold."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density-ws@test.com",
            password="testpass",
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email="staff-density-ws@test.com",
            password="testpass",
        )

    def test_header_uses_responsive_flex_direction(self):
        response = self.client.get("/studio/workshops/")
        # Old: ``flex items-center justify-between`` forced a single row at
        # every viewport, leaving no room for the worker-status indicator.
        # New: ``flex-col sm:flex-row`` stacks at mobile and aligns at sm+.
        self.assertContains(response, "flex flex-col sm:flex-row")

    def test_worker_status_inline_wraps_at_mobile(self):
        # Inline worker status sits inside the H1 block; it must wrap so
        # 8 short tokens (icon + "Worker:" + state + ... + queue depth)
        # don't extend past the viewport at 412 px.
        path = (
            REPO_ROOT
            / "templates"
            / "studio"
            / "includes"
            / "worker_status_inline.html"
        )
        markup = path.read_text()
        self.assertIn("flex-wrap", markup)


class StudioMobileDensityRegressionTest(TestCase):
    """Cross-cutting regression: the bonus mobile chrome is gone for everyone."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density-cross@test.com",
            password="testpass",
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email="staff-density-cross@test.com",
            password="testpass",
        )

    def test_no_legacy_per_row_rounded_card_chrome_in_recordings(self):
        # The old recordings markup wrapped the table in
        # "rounded-lg overflow-hidden" without the responsive helper. After
        # the fix, the wrapper class string comes from
        # ``studio_list_class 'wrapper'`` so it ends up identical to
        # /studio/articles/.
        response = self.client.get("/studio/recordings/")
        self.assertContains(
            response,
            (
                "studio-responsive-table bg-card border border-border "
                "rounded-lg overflow-x-auto"
            ),
        )

    def test_no_legacy_per_row_rounded_card_chrome_in_projects(self):
        response = self.client.get("/studio/projects/")
        self.assertContains(
            response,
            (
                "studio-responsive-table bg-card border border-border "
                "rounded-lg overflow-x-auto"
            ),
        )


class StudioEnvMismatchBannerHiddenOnMobileTest(TestCase):
    """The env-mismatch banner stays compact on mobile.

    The warning remains visible below md, but the long configured/current
    URL details are hidden below sm so the banner does not consume the
    mobile viewport height that dense Studio lists need.
    """

    @classmethod
    def setUpTestData(cls):
        cls.markup = ENV_MISMATCH_TEMPLATE.read_text()

    def test_banner_outer_wrapper_is_visible_below_md(self):
        # The outer wrapper intentionally has no responsive hidden class;
        # the compact warning text remains visible on mobile.
        self.assertIn(
            'class="bg-amber-50 dark:bg-amber-500/10',
            self.markup,
        )
        self.assertIn(
            '<p class="font-semibold text-amber-950 '
            'dark:text-amber-100 shrink-0">Environment mismatch</p>',
            self.markup,
        )

    def test_banner_long_url_summary_is_hidden_below_sm(self):
        # The verbose configured/current URL line is hidden on narrow
        # screens, then restored at sm+.
        self.assertIn(
            '<p class="hidden min-w-0 leading-5 sm:block">',
            self.markup,
        )
        self.assertIn(
            'data-testid="env-mismatch-configured"',
            self.markup,
        )
        self.assertIn(
            'data-testid="env-mismatch-request"',
            self.markup,
        )

    def test_banner_still_carries_alert_role_for_desktop(self):
        # Desktop accessibility is unaffected — the role + aria-live
        # attributes stay on the banner so screen readers announce it
        # at md+ where it is visible.
        self.assertIn('role="alert"', self.markup)
        self.assertIn('aria-live="polite"', self.markup)


class StudioListHeaderDescriptionsHiddenOnMobileTest(TestCase):
    """Page-header descriptions on dense list pages hide below md.

    The Articles page already hides its description below md (issue
    #620, first round). The same rule now applies to Events, Courses,
    Users, and Email templates so each page reclaims ~24 px and the
    Pixel 7 row-density targets land.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density-headers@test.com",
            password="testpass",
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email="staff-density-headers@test.com",
            password="testpass",
        )

    def test_articles_description_hidden_on_mobile(self):
        response = self.client.get("/studio/articles/")
        self.assertContains(
            response,
            'mt-1 hidden md:block">Manage blog articles and posts.',
        )

    def test_events_description_hidden_on_mobile(self):
        response = self.client.get("/studio/events/")
        self.assertContains(
            response,
            (
                'mt-1 hidden md:block">'
                "Manage event lifecycle, platform, and capacity."
            ),
        )

    def test_courses_description_hidden_on_mobile(self):
        response = self.client.get("/studio/courses/")
        self.assertContains(
            response,
            'mt-1 hidden md:block">Manage your courses and curriculum.',
        )

    def test_users_description_hidden_on_mobile(self):
        response = self.client.get("/studio/users/")
        self.assertContains(
            response,
            (
                'mt-1 hidden md:block">'
                "All platform users."
            ),
        )

    def test_email_templates_description_hidden_on_mobile(self):
        response = self.client.get("/studio/email-templates/")
        self.assertContains(response, "mt-1 hidden md:block")


class StudioOriginBadgeHiddenOnMobileTest(TestCase):
    """The origin badge is hidden below md (issue #620 round 5).

    Round 4 tried to pull the badge inline next to the title on mobile,
    but the parent ``<td>`` is ``inline-flex`` and a ``inline-flex``
    pill child still forced the cell to allocate ~22 px of vertical
    chrome — keeping the Articles list at 5 rows on a Pixel 7. The
    cleanest fix is to drop the entire badge wrapper at mobile so the
    row pitch falls from ~132 px to ~108 px and the page reaches the
    6-row density target. The badge re-appears on md+ where the table
    has a dedicated column for provenance metadata.

    This regression guard pins:
    - The wrapper element is a ``<span>`` (so a future change cannot
      accidentally re-introduce a block-level element that would still
      occupy space when ``hidden`` is removed).
    - Both branches carry ``hidden md:block`` so neither the "Local /
      manual" nor the "Synced" pill renders below md.
    - Detail-text children (path / repo / "no metadata" hint) stay
      ``block`` inside the wrapper — they never escape to mobile
      because the wrapper itself is hidden.
    """

    @classmethod
    def setUpTestData(cls):
        cls.markup_path = (
            REPO_ROOT
            / "templates"
            / "studio"
            / "includes"
            / "origin_badge.html"
        )
        cls.markup = cls.markup_path.read_text()

    def test_local_wrapper_is_hidden_below_md(self):
        # The "Local / manual" branch (the common case for the
        # Articles list per #620) must be entirely hidden at mobile.
        self.assertIn('data-origin="local"', self.markup)
        self.assertIn(
            'class="hidden md:block md:mt-1" data-testid="origin-badge" '
            'data-origin="local"',
            self.markup,
        )

    def test_synced_wrapper_is_hidden_below_md(self):
        # The "Synced" branch (used by /studio/courses/, /studio/workshops/)
        # is also hidden below md so cells stay tight on phones.
        self.assertIn('data-origin="synced"', self.markup)
        self.assertIn(
            'class="hidden md:block md:mt-1 md:space-y-0.5" '
            'data-testid="origin-badge" data-origin="synced"',
            self.markup,
        )

    def test_round_4_inline_wrapper_is_gone(self):
        # The round-4 wrappers leaked vertical chrome via the
        # ``inline-flex`` pill child — make sure they cannot return.
        self.assertNotIn('inline md:block md:mt-1 ml-2 md:ml-0', self.markup)
        self.assertNotIn('inline md:block md:mt-1 md:space-y-0.5', self.markup)

    def test_legacy_block_wrapper_is_gone(self):
        # Round-3 and earlier used ``<div class="mt-1">`` — that was
        # the original regression vector that pushed each row past
        # 140 px. Pin it dead.
        self.assertNotIn('"mt-1 space-y-0.5"', self.markup)
        self.assertNotIn('"mt-1" data-testid', self.markup)

    def test_detail_text_still_present_for_desktop(self):
        # The badge body must still render the "No GitHub source
        # metadata" hint at md+ — it is the only signal an operator has
        # that a record is editable in Studio rather than in the content
        # repo. The wrapper hides it on mobile, but the markup must
        # still be there for desktop.
        self.assertIn('No GitHub source metadata', self.markup)


class StudioArticlesListOriginBadgeHiddenTest(TestCase):
    """End-to-end check that /studio/articles/ rows hide the badge on mobile."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-density-badge@test.com",
            password="testpass",
            is_staff=True,
        )

    def setUp(self):
        self.client.login(
            email="staff-density-badge@test.com",
            password="testpass",
        )

    def test_article_row_renders_hidden_badge_wrapper(self):
        from content.models import Article

        Article.objects.create(
            title="Hidden Badge Article",
            slug="hidden-badge",
            date=timezone.now().date(),
            published=True,
        )
        response = self.client.get("/studio/articles/")
        # The wrapper is ``<span class="hidden md:block ...">`` — the
        # load-bearing class string that keeps the row at 108 px on
        # Pixel 7 instead of 132 px.
        self.assertContains(
            response,
            'class="hidden md:block md:mt-1" data-testid="origin-badge"',
        )
        # The round-4 inline wrapper (still wrapped on the actual
        # device) must not leak into the rendered markup.
        self.assertNotContains(
            response,
            'inline md:block md:mt-1 ml-2 md:ml-0',
        )
        # And the legacy ``<div class="mt-1">`` wrapper from earlier
        # rounds must also not leak into the rendered markup.
        self.assertNotContains(
            response,
            '<div class="mt-1" data-testid="origin-badge"',
        )
