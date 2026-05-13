"""Tests for the Studio user detail page layout reshuffle (issue #586).

Locks the structural contract the issue spec lays out:

- Header is identity-only (no buttons).
- A single action row sits directly under the header with exactly two
  controls: ``Login as user`` (POST -> studio_impersonate) and
  ``View in Django admin`` (link).
- The duplicate ``View as user`` button is removed.
- Profile and Membership sit in their own full-width section cards
  (no ``lg:grid-cols-2`` grid wrapper between them).
- ``Grant temporary upgrade`` is a top-level section card with its own
  ``<h2>`` between Membership and Tags.
- Slack ID row is read-only — no input, no save button, no form posting
  to ``studio_user_slack_id_set``. When the ID is missing the row shows
  ``Not linked`` plus an ``Edit in Django admin`` link.
- The ``studio_user_slack_id_set`` URL stays defined and reachable from
  Django admin / scripts (template just stops surfacing it).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class _Base586(TestCase):
    """Shared fixtures: tiers + staff session."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff-586@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-586@test.com', password='pw')

    def _make_member(self, email, tier=None, **extras):
        user = User.objects.create_user(email=email, password='pw')
        if tier is not None:
            user.tier = tier
        for key, value in extras.items():
            setattr(user, key, value)
        user.save()
        return user

    def _make_override(self, user, override_tier=None, days=14):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=override_tier or self.main,
            expires_at=timezone.now() + timedelta(days=days),
            granted_by=self.staff,
            is_active=True,
        )


class HeaderAndActionRowTest(_Base586):
    """Header is identity-only; action row sits below with two buttons."""

    def test_header_renders_email_with_no_action_buttons_inside(self):
        member = self._make_member('hdr@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()

        # Locate the closing tag of the header element.
        header_open = body.index('data-testid="user-detail-header"')
        # Find the next "</header>" after the header open marker.
        header_close = body.index('</header>', header_open)
        header_slice = body[header_open:header_close]

        # Email h1 + the visible-to-staff hint live in the header.
        self.assertIn('data-testid="user-detail-email"', header_slice)
        self.assertIn('hdr@test.com', header_slice)
        self.assertIn('Visible to staff only', header_slice)

        # The action testid + each button testid must NOT appear inside
        # the header slice. They live in a separate row below.
        self.assertNotIn('data-testid="user-detail-actions"', header_slice)
        self.assertNotIn('data-testid="user-detail-impersonate"', header_slice)
        self.assertNotIn(
            'data-testid="user-detail-django-admin"', header_slice,
        )

    def test_action_row_immediately_after_header_with_two_controls(self):
        member = self._make_member('row@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()

        header_open = body.index('data-testid="user-detail-header"')
        actions_open = body.index('data-testid="user-detail-actions"')
        # The action row sits AFTER the header opens.
        self.assertLess(header_open, actions_open)

        # And BEFORE the first content section card (Profile).
        profile_open = body.index('data-testid="user-detail-profile-section"')
        self.assertLess(actions_open, profile_open)

        # Action row contains Login as user (impersonate) + View in
        # Django admin (link). Exactly those two testids.
        actions_close = body.index('</div>', actions_open)
        actions_slice = body[actions_open:actions_close]
        self.assertEqual(
            actions_slice.count('data-testid="user-detail-impersonate"'), 1,
        )
        self.assertEqual(
            actions_slice.count('data-testid="user-detail-django-admin"'), 1,
        )
        self.assertIn('Login as user', actions_slice)
        self.assertIn('View in Django admin', actions_slice)

    def test_view_as_user_button_is_removed_page_wide(self):
        member = self._make_member('noview@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        # Both the testid and the visible label must be gone.
        self.assertNotContains(response, 'data-testid="user-detail-view-as"')
        self.assertNotContains(response, 'View as user')

    def test_login_as_user_remains_a_post_form(self):
        member = self._make_member('postlogin@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        # The button is wrapped in a POST form with CSRF and points at
        # the canonical impersonate endpoint.
        self.assertContains(
            response,
            f'action="{reverse("studio_impersonate", args=[member.pk])}"',
        )
        self.assertContains(response, 'method="post"')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_django_admin_link_uses_secondary_label(self):
        # Issue #586 renamed the visible label from "Django Admin" to
        # "View in Django admin" for parity with other Studio detail
        # action rows.
        member = self._make_member('djl@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'View in Django admin')
        self.assertContains(
            response,
            f'href="/admin/accounts/user/{member.pk}/change/"',
        )


class FullWidthSectionsTest(_Base586):
    """Profile / Membership are stacked, not in a 2-column grid."""

    def test_no_lg_grid_cols_2_wrapper_for_profile_membership(self):
        member = self._make_member('grid@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        # The previous template wrapped Profile + Membership in a
        # `grid grid-cols-1 lg:grid-cols-2` div. After #586 each section
        # is its own card. The exact wrapper class string must be gone.
        self.assertNotIn('lg:grid-cols-2', body)

    def test_membership_section_appears_below_profile_in_dom(self):
        member = self._make_member('order@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        profile_idx = body.index(
            'data-testid="user-detail-profile-section"',
        )
        membership_idx = body.index(
            'data-testid="user-detail-membership-section"',
        )
        self.assertLess(profile_idx, membership_idx)

    def test_section_dom_order_profile_membership_override_tags_crm(self):
        member = self._make_member('full-order@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        order = [
            'user-detail-profile-section',
            'user-detail-membership-section',
            'user-detail-tier-override-section',
            'user-tags-section',
            'user-crm-section',
        ]
        positions = [body.index(f'data-testid="{t}"') for t in order]
        # Every section appears exactly once and in the documented order.
        self.assertEqual(positions, sorted(positions))


class GrantTemporaryUpgradeSectionTest(_Base586):
    """Standalone Grant temporary upgrade section."""

    def test_section_card_has_h2_titled_grant_temporary_upgrade(self):
        member = self._make_member('upgrade@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        section_idx = body.index(
            'data-testid="user-detail-tier-override-section"',
        )
        # The next </section> after the section open is what we care
        # about; assert the h2 with the right title sits inside.
        section_close = body.index('</section>', section_idx)
        section_slice = body[section_idx:section_close]
        self.assertIn('<h2', section_slice)
        self.assertIn('Grant temporary upgrade', section_slice)

    def test_section_sits_between_membership_and_tags(self):
        member = self._make_member('between@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        membership_idx = body.index(
            'data-testid="user-detail-membership-section"',
        )
        section_idx = body.index(
            'data-testid="user-detail-tier-override-section"',
        )
        tags_idx = body.index('data-testid="user-tags-section"')
        self.assertLess(membership_idx, section_idx)
        self.assertLess(section_idx, tags_idx)

    def test_active_override_state_renders_revoke_form(self):
        member = self._make_member('rev@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        # All these markers live inside the new section.
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-section"',
        )
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-revoke-form"',
        )
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-revoke"',
        )

    def test_highest_tier_state_renders_inside_section(self):
        member = self._make_member('peak@test.com', tier=self.premium)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response, 'data-testid="user-detail-tier-override-highest"',
        )
        self.assertContains(response, 'highest tier')

    def test_form_state_renders_tier_radios_and_durations(self):
        member = self._make_member('formstate@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        self.assertEqual(
            body.count('data-testid="user-detail-tier-override-tier-option"'),
            3,
        )
        self.assertEqual(
            body.count('data-testid="user-detail-tier-override-duration"'),
            5,
        )
        self.assertIn('data-testid="user-detail-tier-override-form"', body)

    def test_history_link_present_in_form_and_active_states(self):
        # The history link should be present whether the user has an
        # active override or not — operators jump back to the audit trail
        # in both flows.
        member_form = self._make_member('hist-form@test.com', tier=self.free)
        member_active = self._make_member(
            'hist-active@test.com', tier=self.free,
        )
        self._make_override(member_active, override_tier=self.main)

        for member in (member_form, member_active):
            response = self.client.get(f'/studio/users/{member.pk}/')
            self.assertContains(
                response,
                'data-testid="user-detail-tier-override-history-link"',
            )
            self.assertContains(
                response,
                f'href="/studio/users/{member.pk}/tier_override/"',
            )


class SlackIdReadOnlyTest(_Base586):
    """Slack ID row is read-only on the detail page."""

    def test_slack_id_row_has_no_input_no_save_no_form(self):
        member = self._make_member(
            'slack-edit@test.com', tier=self.free,
            slack_user_id='U01ADA999',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        # No <input name="slack_user_id">.
        self.assertNotIn('name="slack_user_id"', body)
        # No save submit button testid.
        self.assertNotIn('data-testid="user-detail-slack-id-submit"', body)
        # No form posting to the slack-id-set endpoint.
        slack_id_set_url = reverse(
            'studio_user_slack_id_set', args=[member.pk],
        )
        self.assertNotIn(f'action="{slack_id_set_url}"', body)
        # The form testid is also gone.
        self.assertNotIn('data-testid="user-detail-slack-id-form"', body)
        # Helper copy about the U01ABC123 placeholder is gone.
        self.assertNotIn('U01ABC123', body)

    def test_unlinked_row_renders_admin_edit_link(self):
        member = self._make_member('slack-empty@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response, 'data-testid="user-detail-slack-id-empty"',
        )
        self.assertContains(response, 'Not linked')
        # Admin edit link points at the canonical Django admin change page.
        self.assertContains(
            response, 'data-testid="user-detail-slack-id-admin-link"',
        )
        self.assertContains(response, 'Edit in Django admin')
        self.assertContains(
            response,
            f'href="/admin/accounts/user/{member.pk}/change/"',
        )

    def test_linked_row_does_not_render_admin_edit_link(self):
        member = self._make_member(
            'slack-set@test.com', tier=self.free,
            slack_user_id='U01ABC123',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        # Admin edit link only renders when the row is empty.
        self.assertNotContains(
            response, 'data-testid="user-detail-slack-id-admin-link"',
        )
        # The value still renders.
        self.assertContains(response, 'U01ABC123')


class SlackIdSetEndpointStillCallableTest(_Base586):
    """The studio_user_slack_id_set URL stays defined for non-template
    surfaces (Django admin / scripts)."""

    def test_url_resolves(self):
        member = self._make_member('still@test.com', tier=self.free)
        # A successful reverse means the URL pattern is still registered.
        url = reverse('studio_user_slack_id_set', args=[member.pk])
        self.assertEqual(url, f'/studio/users/{member.pk}/slack-id/')

    def test_post_writes_value_through_endpoint(self):
        # Smoke check: the endpoint still works for a staff POST. The
        # template just stopped surfacing it.
        member = self._make_member('stillpost@test.com', tier=self.free)
        response = self.client.post(
            f'/studio/users/{member.pk}/slack-id/',
            {'slack_user_id': 'U01ABC123'},
        )
        self.assertEqual(response.status_code, 302)
        member.refresh_from_db()
        self.assertEqual(member.slack_user_id, 'U01ABC123')
