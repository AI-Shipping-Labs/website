"""Tests for the Studio user-detail status-clarity changes (issue #924).

The "Membership & community" card on ``/studio/users/<id>/`` gains:

- an ``Email verified`` row (Verified / Not verified pills) sourced from
  ``User.email_verified``;
- inline ``title=`` tooltips + ``help-circle`` info icons on every cryptic
  label (Email verified, Tier, Status, Source, Activated, Newsletter,
  Slack, Slack ID) plus the tier-source badge variants and the bounce
  ``State`` label;
- a ``What do these mean?`` docs link in the card header.

These tests lock the server-rendered HTML contract. The hover legibility
of the tooltips over the dark card is a ``[HUMAN]`` screenshot criterion
and is verified manually, not here.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class _Base924(TestCase):
    """Shared fixtures: tiers + staff session."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff-924@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-924@test.com', password='pw')

    def _make_member(self, email, tier=None, **extras):
        user = User.objects.create_user(email=email, password='pw')
        if tier is not None:
            user.tier = tier
        for key, value in extras.items():
            setattr(user, key, value)
        user.save()
        return user

    def _make_override(self, user, override_tier=None):
        return TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=override_tier or self.main,
            expires_at=timezone.now() + timedelta(days=14),
            granted_by=self.staff,
            is_active=True,
        )


class EmailVerifiedRowTest(_Base924):
    """The Email-verified row reflects ``User.email_verified``."""

    def test_verified_user_shows_verified_pill(self):
        member = self._make_member(
            'verified-924@test.com', tier=self.free, email_verified=True,
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<span class="inline-flex items-center text-xs px-2 py-1 '
            'rounded-full bg-green-500/20 text-green-400" '
            'data-testid="user-detail-email-verified" '
            'data-email-verified="yes">Verified</span>',
            html=True,
        )

    def test_unverified_user_shows_not_verified_pill(self):
        member = self._make_member(
            'unverified-924@test.com', tier=self.free, email_verified=False,
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="user-detail-email-verified"', body)
        self.assertIn('data-email-verified="no"', body)
        self.assertNotIn('data-email-verified="yes"', body)
        self.assertContains(response, '>Not verified</span>')


class CrypticLabelTooltipsTest(_Base924):
    """Each cryptic label carries the exact help title + a help icon."""

    def setUp(self):
        super().setUp()
        member = self._make_member('tooltips-924@test.com', tier=self.free)
        self.response = self.client.get(f'/studio/users/{member.pk}/')
        self.body = self.response.content.decode()

    def test_email_verified_tooltip_text(self):
        self.assertIn(
            'title="Whether the user confirmed their email via the '
            'verification link. OAuth signups are auto-verified. The exact '
            'date is not tracked."',
            self.body,
        )

    def test_tier_tooltip_text(self):
        self.assertIn(
            "title=\"The user's effective membership tier (Free, Basic, "
            'Main, Premium). Includes any active temporary upgrade."',
            self.body,
        )

    def test_status_tooltip_text(self):
        self.assertIn(
            'title="The login account state, not the subscription. '
            'Active = can log in; Staff = staff account; Inactive = login '
            'disabled."',
            self.body,
        )

    def test_source_tooltip_text(self):
        self.assertIn(
            'title="How this user row was first created (signup '
            'attribution). &quot;Unknown (pre-existing row)&quot; means the '
            'row predates signup tracking."',
            self.body,
        )

    def test_activated_tooltip_text(self):
        self.assertIn(
            'title="Yes once the user has taken any platform action '
            '(verified email, paid, registered for an event, completed a '
            'course unit, or linked Slack). No = newsletter-only / never '
            'engaged."',
            self.body,
        )

    def test_newsletter_tooltip_text(self):
        self.assertIn(
            'title="Whether the user receives marketing emails. '
            'Unsubscribed users still keep their account."',
            self.body,
        )

    def test_slack_tooltip_text(self):
        self.assertIn(
            'title="Result of the last Slack-workspace membership check. '
            '&quot;Never checked&quot; means we have not probed this user '
            'yet."',
            self.body,
        )

    def test_slack_id_tooltip_text(self):
        self.assertIn(
            "title=\"The user's linked Slack workspace ID. Set during Slack "
            'OAuth; &quot;Not linked&quot; means no Slack account is '
            'connected."',
            self.body,
        )

    def test_help_icons_render_on_cryptic_labels(self):
        # One help-circle icon per cryptic label inside the membership
        # card (Tier, Status, Email verified, Source, Activated,
        # Newsletter, Slack, Slack ID) = 8, plus the card-header
        # "What do these mean?" link icon = 9. The bounce card is not
        # rendered for this healthy member.
        self.assertEqual(
            self.body.count('data-lucide="help-circle"'), 9,
        )


class TierSourceBadgeTooltipsTest(_Base924):
    """Each tier-source badge variant carries its own tooltip."""

    def test_default_badge_tooltip(self):
        member = self._make_member('default-924@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="default"')
        self.assertContains(
            response,
            "title=\"No Stripe subscription and no override — this is the "
            "user's stored tier (manual, seed, or newsletter-only row).\"",
        )

    def test_stripe_badge_tooltip(self):
        member = self._make_member(
            'stripe-924@test.com', tier=self.basic,
            stripe_customer_id='cus_924',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="stripe"')
        self.assertContains(
            response,
            'title="Tier comes from a paid Stripe subscription."',
        )

    def test_override_badge_tooltip(self):
        member = self._make_member('override-924@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="override"')
        self.assertContains(
            response,
            'title="Tier comes from an active temporary upgrade granted in '
            'Studio. Click to view override history."',
        )
        # The existing override-page link is preserved.
        self.assertContains(
            response, 'data-testid="user-detail-tier-badge-link"',
        )


class BounceStateTooltipTest(_Base924):
    """The bounce State label carries the bounce help tooltip."""

    def test_bounce_state_tooltip_present_for_bounced_user(self):
        member = self._make_member(
            'bounced-924@test.com', tier=self.free,
            bounce_state=User.BounceState.PERMANENT,
            bounce_recorded_at=timezone.now(),
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-testid="user-detail-bounce-state"')
        self.assertContains(
            response,
            'title="SES delivery status. Permanent = hard bounce (address '
            'dead); Soft = transient bounce. Permanent bounces are '
            'auto-unsubscribed."',
        )

    def test_bounce_tooltip_absent_for_healthy_user(self):
        member = self._make_member('healthy-924@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertNotContains(
            response, 'data-testid="user-detail-bounce-state"',
        )
        self.assertNotContains(response, 'SES delivery status.')


class StatusDocsLinkTest(_Base924):
    """The card header carries the GitHub-hosted status-docs link."""

    def test_docs_link_present_and_opens_new_tab(self):
        member = self._make_member('docs-924@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(
            response,
            'href="https://github.com/AI-Shipping-Labs/website/blob/main/'
            '_docs/studio-user-statuses.md"',
        )
        self.assertContains(
            response, 'data-testid="user-detail-status-docs-link"',
        )
        # Locate the docs anchor and confirm it opens in a new tab.
        body = response.content.decode()
        marker = 'data-testid="user-detail-status-docs-link"'
        idx = body.index(marker)
        snippet = body[max(0, idx - 300):idx + 200]
        self.assertIn('target="_blank"', snippet)
        self.assertIn('rel="noopener"', snippet)


class ExistingTestidsSurviveTest(_Base924):
    """The added tooltips must not break existing detail selectors."""

    def test_existing_membership_testids_present(self):
        member = self._make_member('survive-924@test.com', tier=self.basic)
        response = self.client.get(f'/studio/users/{member.pk}/')
        for testid in (
            'user-detail-tier-pill',
            'user-detail-tier-badge',
            'user-detail-status-pill',
            'user-detail-signup-source',
            'user-detail-account-activated',
            'user-detail-slack-id-row',
        ):
            self.assertContains(response, f'data-testid="{testid}"')
