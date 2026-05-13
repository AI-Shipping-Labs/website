"""Server-side template assertions for the Studio sidebar mobile fixes
in issue #624.

The behaviour these guard against is purely template / class-list:

- The mobile toggle pill must use theme tokens, not the legacy
  ``bg-gray-800`` palette.
- The drawer ``<aside>`` must use the wider ``w-[min(85vw,18rem)]``
  rule on mobile (with ``md:w-64`` preserving the desktop layout).
- All five section toggles must use ``text-foreground/70``,
  ``font-semibold``, and ``min-h-[44px]``.
- The Users row must be a single ``<a>`` — no ``data-studio-users-toggle``
  ``<button>``, no ``aria-controls="studio-users-children"`` chevron,
  no ``aria-label="Toggle Users sub-menu"`` half.

Playwright covers the visual / measurement checks at the Pixel 7 viewport
(see ``playwright_tests/test_studio_sidebar_mobile_density.py``); these
faster Django tests pin the markup contract.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioSidebarMobileFixesTest(TestCase):
    """Markup-level guards for the four #624 mobile fixes."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _dashboard_body(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # ------------------------------------------------------------------
    # Fix 1: drawer width — mobile uses ``min(85vw, 18rem)``, desktop
    # keeps ``w-64``.
    # ------------------------------------------------------------------

    def test_aside_uses_wide_mobile_width_with_desktop_w64(self):
        body = self._dashboard_body()
        # The mobile rule + desktop override appear on the same element.
        self.assertIn(
            'id="studio-sidebar"',
            body,
        )
        self.assertIn('w-[min(85vw,18rem)]', body)
        self.assertIn('md:w-64', body)

    def test_aside_no_longer_uses_bare_w64(self):
        body = self._dashboard_body()
        # The pre-#624 markup had ``hidden md:block w-64 bg-card`` —
        # the bare ``w-64`` token (without the ``md:`` prefix) on the
        # aside meant the drawer was 256 px wide on mobile too.
        self.assertNotIn('md:block w-64 bg-card', body)

    # ------------------------------------------------------------------
    # Fix 2: toggle pill uses theme tokens, not bg-gray-800
    # ------------------------------------------------------------------

    def test_sidebar_toggle_uses_theme_tokens(self):
        body = self._dashboard_body()
        # The toggle pill carries the theme-aware classes.
        toggle_idx = body.find('id="studio-sidebar-toggle"')
        self.assertGreater(toggle_idx, -1, 'toggle button must render')
        # Slice the rest of the opening tag to scope the assertions.
        toggle_tag = body[toggle_idx:body.find('>', toggle_idx)]
        for required in (
            'border border-border',
            'bg-card',
            'text-foreground',
            'hover:bg-secondary',
            'focus-visible:ring-accent',
        ):
            self.assertIn(
                required,
                toggle_tag,
                f'toggle pill should carry {required!r}; tag was {toggle_tag!r}',
            )

    def test_sidebar_toggle_drops_legacy_gray_classes(self):
        body = self._dashboard_body()
        toggle_idx = body.find('id="studio-sidebar-toggle"')
        toggle_tag = body[toggle_idx:body.find('>', toggle_idx)]
        for forbidden in (
            'bg-gray-800',
            'bg-gray-700',
            'text-gray-300',
            'hover:text-white',
        ):
            self.assertNotIn(
                forbidden,
                toggle_tag,
                f'toggle pill must not carry legacy {forbidden!r}; '
                f'tag was {toggle_tag!r}',
            )

    # ------------------------------------------------------------------
    # Fix 3: section toggles use text-foreground/70, font-semibold,
    # and min-h-[44px] — all five sections.
    # ------------------------------------------------------------------

    SECTIONS = ('events', 'content', 'people', 'marketing', 'operations')

    def test_each_section_toggle_uses_lifted_contrast_and_44px(self):
        body = self._dashboard_body()
        for slug in self.SECTIONS:
            with self.subTest(slug=slug):
                anchor = f'aria-controls="studio-section-{slug}"'
                idx = body.find(anchor)
                self.assertGreater(idx, -1, f'missing section toggle: {slug!r}')
                # The class= attribute follows aria-controls in the markup
                # but is on the previous lines too — find the enclosing
                # <button ...> tag by scanning back to the nearest '<button'
                # then forward to the first '>' after the anchor.
                btn_open = body.rfind('<button', 0, idx)
                btn_close = body.find('>', idx)
                self.assertGreater(btn_open, -1)
                self.assertGreater(btn_close, idx)
                tag = body[btn_open:btn_close + 1]
                for required in (
                    'text-foreground/70',
                    'font-semibold',
                    'min-h-[44px]',
                ):
                    self.assertIn(
                        required,
                        tag,
                        f'{slug!r} toggle should carry {required!r}',
                    )
                for forbidden in ('text-muted-foreground', 'font-medium'):
                    self.assertNotIn(
                        forbidden,
                        tag,
                        f'{slug!r} toggle should drop legacy {forbidden!r}',
                    )

    # ------------------------------------------------------------------
    # Fix 4: Users row is a single <a> — no split-button chevron
    # ------------------------------------------------------------------

    def test_users_row_renders_single_anchor_no_chevron_button(self):
        body = self._dashboard_body()
        # The Users anchor still points at /studio/users/.
        self.assertIn('href="/studio/users/"', body)
        # The chevron sub-toggle <button> and its data hooks are gone.
        self.assertNotIn('data-studio-users-toggle', body)
        self.assertNotIn(
            'aria-controls="studio-users-children"', body,
        )
        self.assertNotIn('aria-label="Toggle Users sub-menu"', body)
        self.assertNotIn('studio-users-chevron', body)

    def test_users_children_list_renders_unconditionally_inside_people(self):
        body = self._dashboard_body()
        # The Users children <ul> no longer carries a conditional ``hidden``
        # class — when People is expanded the children render in flow.
        self.assertIn(
            'id="studio-users-children" class="ml-6 mt-1 space-y-1"',
            body,
        )
        # And the variant with a trailing ``hidden`` token must NOT appear.
        self.assertNotIn(
            'id="studio-users-children" class="ml-6 mt-1 space-y-1 hidden"',
            body,
        )

    def test_users_row_has_no_split_button_wrapper(self):
        body = self._dashboard_body()
        # The pre-#624 wrapper used ``flex items-stretch`` to lay the
        # anchor and chevron button side-by-side. Confirm it is gone.
        self.assertNotIn('flex items-stretch rounded-lg', body)

    # ------------------------------------------------------------------
    # JS handler for the removed Users toggle is gone
    # ------------------------------------------------------------------

    def test_users_toggle_js_handler_is_removed(self):
        body = self._dashboard_body()
        # The handler block iterated ``[data-studio-users-toggle]`` and
        # called ``toggleControlled`` — both are gone.
        self.assertNotIn("[data-studio-users-toggle]", body)
        self.assertNotIn(".studio-users-chevron", body)
