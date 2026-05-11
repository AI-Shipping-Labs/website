"""Studio Slack ID surface on the user detail page (issue #561).

The ``slack_user_id`` field has been on ``User`` for some time, populated
by OAuth signup, bulk Slack import, and the email matcher background job.
This issue surfaces and acts on it in Studio:

- ``/studio/users/<id>/`` renders a ``Slack ID`` row inside the existing
  Membership & community section, with an "Open in Slack" anchor when both
  the user has a Slack ID and the operator has set ``SLACK_TEAM_ID``.
- A new POST endpoint ``/studio/users/<id>/slack-id/`` lets staff set,
  clear, or replace the Slack ID. Validation pins the canonical
  ``^[UW][A-Z0-9]{2,}$`` shape.
- The Slack pill on the users list grows an optional anchor wrapper when
  both halves of the deep-link are configured.

These tests lock the user-facing contract end-to-end at the Django layer
so the matching Playwright scenarios can stay focused on real browser
behavior (link target attributes, flash visibility) instead of
re-asserting on raw HTML.
"""

import os
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting

User = get_user_model()

SETTINGS_KEY = 'SLACK_TEAM_ID'


def _row_html(html, user_pk):
    """Return the inner HTML of the ``<tr data-testid="user-row-<pk>">`` row."""
    pattern = (
        r'<tr[^>]*data-testid="user-row-' + str(user_pk) + r'"[^>]*>'
        r'(.*?)'
        r'</tr>'
    )
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        raise AssertionError(
            f'Could not locate row data-testid="user-row-{user_pk}" in '
            f'rendered HTML.'
        )
    return match.group(0)


class _SlackTeamIdSettingMixin:
    """Reset ``SLACK_TEAM_ID`` state between tests.

    Identical pattern to ``test_user_list_stripe_indicator`` — keep the
    env var / IntegrationSetting / in-process cache from leaking across
    tests so we can assert on both the "configured" and "blank" paths.
    """

    def _reset_team_id(self):
        IntegrationSetting.objects.filter(key=SETTINGS_KEY).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self._saved_env = os.environ.pop(SETTINGS_KEY, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved_env is not None:
            os.environ[SETTINGS_KEY] = self._saved_env
        else:
            os.environ.pop(SETTINGS_KEY, None)

    def _set_team_id(self, value):
        IntegrationSetting.objects.update_or_create(
            key=SETTINGS_KEY,
            defaults={
                'value': value,
                'is_secret': False,
                'group': 'slack',
                'description': '',
            },
        )
        clear_config_cache()


class StudioUserDetailSlackIdRowTest(_SlackTeamIdSettingMixin, TestCase):
    """The Slack ID row + edit form on /studio/users/<id>/."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.linked = User.objects.create_user(
            email='ada@example.com', password='pw',
            slack_user_id='U01ADA123',
        )
        cls.unlinked = User.objects.create_user(
            email='partner@example.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        self._reset_team_id()

    def test_detail_renders_slack_id_row_for_linked_user(self):
        response = self.client.get(f'/studio/users/{self.linked.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-detail-slack-id-row"')
        self.assertContains(response, 'data-testid="user-detail-slack-id-value"')
        self.assertContains(response, 'U01ADA123')

    def test_detail_renders_not_linked_for_unlinked_user(self):
        response = self.client.get(f'/studio/users/{self.unlinked.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="user-detail-slack-id-empty"')
        self.assertContains(response, 'Not linked')

    def test_open_in_slack_link_visible_when_team_id_configured(self):
        self._set_team_id('T01TEAM123')
        # Sanity: confirm the helper actually plumbed the value into config.
        self.assertEqual(get_config(SETTINGS_KEY), 'T01TEAM123')

        response = self.client.get(f'/studio/users/{self.linked.pk}/')
        self.assertContains(
            response, 'data-testid="user-detail-slack-profile-link"',
        )
        self.assertContains(
            response,
            'href="https://app.slack.com/client/T01TEAM123/U01ADA123"',
        )
        # New-tab + noopener: locked together because separate assertions
        # would pass if some other anchor on the page had them.
        anchor_match = re.search(
            r'<a([^>]*data-testid="user-detail-slack-profile-link"[^>]*)>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match)
        attrs = anchor_match.group(1)
        self.assertIn('target="_blank"', attrs)
        self.assertIn('rel="noopener"', attrs)

    def test_open_in_slack_link_missing_when_team_id_blank(self):
        # No team ID configured → no anchor, but the ID stays visible.
        self.assertEqual(get_config(SETTINGS_KEY), '')
        response = self.client.get(f'/studio/users/{self.linked.pk}/')
        self.assertNotContains(
            response, 'data-testid="user-detail-slack-profile-link"',
        )
        # Tooltip explains why the link is missing.
        self.assertContains(
            response,
            'title="Configure SLACK_TEAM_ID to enable the link"',
        )
        # ID itself still visible so the operator can copy it manually.
        self.assertContains(response, 'U01ADA123')

    def test_open_in_slack_link_missing_when_user_has_no_slack_id(self):
        # Even with the team ID configured, an unlinked user has no anchor.
        self._set_team_id('T01TEAM123')
        response = self.client.get(f'/studio/users/{self.unlinked.pk}/')
        self.assertNotContains(
            response, 'data-testid="user-detail-slack-profile-link"',
        )
        self.assertContains(response, 'Not linked')

    def test_inline_edit_form_always_rendered(self):
        # Form is reachable in both states (linked + unlinked) — that's
        # the only way to clear or replace a wrongly-set ID.
        linked_html = self.client.get(
            f'/studio/users/{self.linked.pk}/'
        ).content.decode()
        unlinked_html = self.client.get(
            f'/studio/users/{self.unlinked.pk}/'
        ).content.decode()
        for html in (linked_html, unlinked_html):
            self.assertIn('data-testid="user-detail-slack-id-form"', html)
            self.assertIn('data-testid="user-detail-slack-id-input"', html)
            self.assertIn('data-testid="user-detail-slack-id-submit"', html)
        # Form action posts to the new endpoint on each user.
        self.assertIn(
            f'action="/studio/users/{self.linked.pk}/slack-id/"', linked_html,
        )
        self.assertIn(
            f'action="/studio/users/{self.unlinked.pk}/slack-id/"',
            unlinked_html,
        )

    def test_form_label_branches_on_current_state(self):
        # The label flips between "Edit Slack ID" and "Set Slack ID" so
        # operators see the right call-to-action.
        linked_html = self.client.get(
            f'/studio/users/{self.linked.pk}/'
        ).content.decode()
        self.assertIn('Edit Slack ID', linked_html)

        unlinked_html = self.client.get(
            f'/studio/users/{self.unlinked.pk}/'
        ).content.decode()
        self.assertIn('Set Slack ID', unlinked_html)


class StudioUserSlackIdSetEndpointTest(TestCase):
    """POST ``/studio/users/<id>/slack-id/`` set / clear / validate matrix."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.target = User.objects.create_user(
            email='partner@example.com', password='pw',
        )
        cls.target_with_id = User.objects.create_user(
            email='ghost@example.com', password='pw',
            slack_user_id='U_OLDONE',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')

    def _post(self, user, value):
        return self.client.post(
            f'/studio/users/{user.pk}/slack-id/',
            {'slack_user_id': value},
        )

    def test_set_valid_id_persists_and_redirects(self):
        response = self._post(self.target, 'U09PARTNER')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'/studio/users/{self.target.pk}/')
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, 'U09PARTNER')

    def test_set_bumps_slack_checked_at(self):
        # Pre-condition: never checked.
        self.assertIsNone(self.target.slack_checked_at)
        self._post(self.target, 'U09PARTNER')
        self.target.refresh_from_db()
        # Must be non-null after a successful set — uses timezone.now().
        self.assertIsNotNone(self.target.slack_checked_at)

    def test_set_uppercases_lowercase_input(self):
        # Operators occasionally paste a lowercase value. Slack IDs are
        # conventionally uppercase; we normalize before validation.
        response = self._post(self.target, 'u01abc123')
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, 'U01ABC123')

    def test_set_trims_whitespace(self):
        self._post(self.target, '  U01TRIM00  ')
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, 'U01TRIM00')

    def test_w_prefix_is_valid(self):
        # Enterprise Grid org-wide IDs start with W; both prefixes accepted.
        self._post(self.target, 'W01ENT123')
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, 'W01ENT123')

    def test_invalid_format_rejected_and_existing_value_unchanged(self):
        # Starting state: target_with_id already has a Slack ID. A bad
        # submission must NOT overwrite it.
        response = self._post(self.target_with_id, 'not-a-slack-id')
        self.assertEqual(response.status_code, 302)
        self.target_with_id.refresh_from_db()
        self.assertEqual(self.target_with_id.slack_user_id, 'U_OLDONE')

    def test_invalid_format_flashes_error(self):
        response = self._post(self.target, 'lowercase-only')
        # follow=True so the messages framework attaches the flash to the
        # final response context.
        response = self.client.post(
            f'/studio/users/{self.target.pk}/slack-id/',
            {'slack_user_id': 'lowercase-only'},
            follow=True,
        )
        flashes = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('Invalid Slack ID' in m for m in flashes),
            f'Expected an "Invalid Slack ID" flash, got: {flashes}',
        )

    def test_empty_value_clears_existing_id(self):
        response = self._post(self.target_with_id, '')
        self.assertEqual(response.status_code, 302)
        self.target_with_id.refresh_from_db()
        self.assertEqual(self.target_with_id.slack_user_id, '')

    def test_empty_value_clears_and_bumps_slack_checked_at(self):
        # The bump fires on the clear path too so the badge state ("Never
        # checked" → "Not in Slack") tracks operator intent.
        self.assertIsNone(self.target_with_id.slack_checked_at)
        self._post(self.target_with_id, '')
        self.target_with_id.refresh_from_db()
        self.assertIsNotNone(self.target_with_id.slack_checked_at)

    def test_short_id_rejected(self):
        # Pattern requires at least 3 chars total (^[UW][A-Z0-9]{2,}$).
        response = self._post(self.target, 'U1')
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, '')

    def test_id_with_special_chars_rejected(self):
        response = self._post(self.target, 'U01-ABC')
        self.assertEqual(response.status_code, 302)
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, '')

    def test_get_method_not_allowed(self):
        # GET-only access should bounce — endpoint is POST-only.
        response = self.client.get(f'/studio/users/{self.target.pk}/slack-id/')
        self.assertEqual(response.status_code, 405)

    def test_anonymous_user_cannot_post_and_no_side_effect(self):
        # Side-effect guard from testing-guidelines Rule 12.
        self.client.logout()
        before = self.target.slack_user_id
        response = self.client.post(
            f'/studio/users/{self.target.pk}/slack-id/',
            {'slack_user_id': 'U99HIJACK'},
        )
        # @staff_required redirects unauthenticated users to login.
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, before)

    def test_non_staff_user_cannot_post_and_no_side_effect(self):
        self.client.logout()
        self.client.login(email='member@test.com', password='pw')
        before = self.target.slack_user_id
        response = self.client.post(
            f'/studio/users/{self.target.pk}/slack-id/',
            {'slack_user_id': 'U99HIJACK'},
        )
        # @staff_required returns 403 for authenticated non-staff users.
        self.assertEqual(response.status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.slack_user_id, before)

    def test_target_user_not_found(self):
        # Operator clicks a stale URL after the user was deleted.
        response = self.client.post(
            '/studio/users/999999/slack-id/',
            {'slack_user_id': 'U01ABC123'},
        )
        self.assertEqual(response.status_code, 404)


class StudioUserListSlackPillAnchorTest(_SlackTeamIdSettingMixin, TestCase):
    """The list view's Slack pill becomes an anchor when both halves of the
    deep-link are configured."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.linked = User.objects.create_user(
            email='ada@example.com', password='pw',
            slack_user_id='U01ADA123',
        )
        cls.unlinked = User.objects.create_user(
            email='alan@example.com', password='pw',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        self._reset_team_id()

    def test_pill_text_unchanged_in_both_modes(self):
        # The pill text ("Slack" / "No Slack" / "Slack unchecked") is the
        # contract older tests rely on; wrapping it in an anchor must not
        # change the visible label.
        response = self.client.get('/studio/users/?q=ada@example.com')
        row_html = _row_html(response.content.decode(), self.linked.pk)
        self.assertIn('data-testid="slack-status"', row_html)

    def test_pill_is_anchor_when_user_has_id_and_team_id_set(self):
        self._set_team_id('T01TEAM123')
        response = self.client.get('/studio/users/?q=ada@example.com')
        row_html = _row_html(response.content.decode(), self.linked.pk)
        # Anchor wrapper with a stable test-id and aria-label.
        self.assertIn('data-testid="slack-profile-link"', row_html)
        self.assertIn(
            'href="https://app.slack.com/client/T01TEAM123/U01ADA123"',
            row_html,
        )
        self.assertIn(
            f'aria-label="Open {self.linked.email} in Slack"', row_html,
        )
        # New-tab + noopener attributes on the wrapping anchor specifically.
        anchor_match = re.search(
            r'<a([^>]*data-testid="slack-profile-link"[^>]*)>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match)
        attrs = anchor_match.group(1)
        self.assertIn('target="_blank"', attrs)
        self.assertIn('rel="noopener"', attrs)

    def test_pill_is_not_anchor_when_team_id_blank(self):
        # Linked user, no team ID — anchor wrapper must be absent.
        self.assertEqual(get_config(SETTINGS_KEY), '')
        response = self.client.get('/studio/users/?q=ada@example.com')
        row_html = _row_html(response.content.decode(), self.linked.pk)
        self.assertNotIn('data-testid="slack-profile-link"', row_html)
        # Pill itself still renders.
        self.assertIn('data-testid="slack-status"', row_html)

    def test_pill_is_not_anchor_when_user_has_no_slack_id(self):
        # Team ID configured but user has no slack_user_id → no anchor.
        self._set_team_id('T01TEAM123')
        response = self.client.get('/studio/users/?q=alan@example.com')
        row_html = _row_html(response.content.decode(), self.unlinked.pk)
        self.assertNotIn('data-testid="slack-profile-link"', row_html)


class StudioSlackTeamIdSettingsSaveTest(TestCase):
    """The new ``SLACK_TEAM_ID`` registry key is editable from /studio/settings/."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        IntegrationSetting.objects.filter(key=SETTINGS_KEY).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_save_round_trip_for_team_id(self):
        # Mirrors the Stripe save round-trip — exercise the settings save
        # endpoint and confirm the row is persisted + cache cleared.
        # The save view iterates every key in the ``slack`` group, so the
        # POST has to include all of them (empty values are fine; the view
        # treats them as "delete").
        post_data = {
            'SLACK_ENABLED': 'false',
            'SLACK_ENVIRONMENT': '',
            'SLACK_BOT_TOKEN': '',
            'SLACK_COMMUNITY_CHANNEL_IDS': '',
            'SLACK_ANNOUNCEMENTS_CHANNEL_ID': '',
            'SLACK_DEV_COMMUNITY_CHANNEL_IDS': '',
            'SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID': '',
            'SLACK_TEST_COMMUNITY_CHANNEL_IDS': '',
            'SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID': '',
            'SLACK_ANNOUNCEMENTS_CHANNEL_NAME': '',
            'SLACK_INVITE_URL': '',
            'SLACK_TEAM_ID': 'T01NEWTEAM',
        }
        response = self.client.post('/studio/settings/slack/save/', post_data)
        self.assertEqual(response.status_code, 302)

        row = IntegrationSetting.objects.get(key=SETTINGS_KEY)
        self.assertEqual(row.value, 'T01NEWTEAM')
        self.assertFalse(row.is_secret)
        self.assertEqual(row.group, 'slack')
        self.assertEqual(get_config(SETTINGS_KEY), 'T01NEWTEAM')

    def test_registry_includes_slack_team_id_in_slack_group(self):
        # The settings dashboard pulls fields from INTEGRATION_GROUPS, so
        # the key being registered there is what makes it render.
        from integrations.settings_registry import get_group_by_name

        slack_group = get_group_by_name('slack')
        self.assertIsNotNone(slack_group)
        keys = [k['key'] for k in slack_group['keys']]
        self.assertIn('SLACK_TEAM_ID', keys)
        team_id_entry = next(
            k for k in slack_group['keys'] if k['key'] == 'SLACK_TEAM_ID'
        )
        # Non-secret so the value renders inline (not masked).
        self.assertFalse(team_id_entry.get('is_secret', False))
        # Descriptive copy is in place.
        self.assertTrue(
            team_id_entry.get('description', '').strip(),
            'SLACK_TEAM_ID must carry a non-empty description.',
        )
