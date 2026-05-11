"""Template wiring tests for issue #568.

The Override badge on ``/studio/users/<id>/`` (added in #562) is wrapped
in an ``<a>`` whose ``href`` points at the existing per-user surface
``/studio/users/tier-override/?email=<urlencoded email>``. No new route,
view, or template — only the template anchor wrapper changes.

These tests cover the Django-side contract:

- the badge anchor is rendered with the correct URL + urlencoded email
- emails with ``+`` are encoded as ``%2B`` (not as ``+`` or a space)
- the non-override badges stay plain ``<span>`` (no anchor wrapper)
- the existing ``user-detail-tier-badge`` testid + ``data-tier-source``
  attribute remain on the inner ``<span>`` so #562's selectors survive

Playwright in ``test_studio_user_detail_override_badge_link_568.py``
covers the click-through and the destination page render.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier

User = get_user_model()


class _BadgeLinkTestBase(TestCase):
    """Shared fixtures: tiers and a staff session."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug='free')
        cls.basic = Tier.objects.get(slug='basic')
        cls.main = Tier.objects.get(slug='main')
        cls.premium = Tier.objects.get(slug='premium')
        cls.staff = User.objects.create_user(
            email='staff-568@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='staff-568@test.com', password='pw')

    def _make_member(self, email, tier=None, stripe_customer_id=''):
        user = User.objects.create_user(email=email, password='pw')
        if tier is not None:
            user.tier = tier
        user.stripe_customer_id = stripe_customer_id
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


class OverrideBadgeAnchorRenderedTest(_BadgeLinkTestBase):
    """The Override badge is wrapped in <a> with the right URL + testid."""

    def test_anchor_wrapper_present_for_override_tier_source(self):
        member = self._make_member('ov-anchor@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertEqual(response.status_code, 200)
        # The badge-link wrapper is rendered exactly once for the
        # override variant of the tier badge.
        body = response.content.decode()
        self.assertEqual(
            body.count('data-testid="user-detail-tier-badge-link"'), 1,
        )
        # The inner span keeps its #562 testid + data-tier-source.
        self.assertContains(response, 'data-testid="user-detail-tier-badge"')
        self.assertContains(response, 'data-tier-source="override"')

    def test_anchor_href_matches_reverse_plus_querystring(self):
        member = self._make_member('href@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        expected = (
            reverse('studio_tier_override')
            + '?email=href%40test.com'
        )
        # The anchor's href must match the reverse + urlencoded email.
        self.assertContains(
            response,
            f'href="{expected}"',
        )

    def test_anchor_has_title_tooltip(self):
        member = self._make_member('tip@test.com', tier=self.free)
        self._make_override(member, override_tier=self.main)
        response = self.client.get(f'/studio/users/{member.pk}/')
        # Hover affordance per AC. The literal lives in the template as
        # raw text inside a double-quoted attribute, so the apostrophe is
        # NOT escaped — it round-trips as a plain `'`.
        self.assertContains(
            response, "title=\"View this user's tier overrides\"",
        )


class OverrideBadgeEmailUrlEncodedTest(_BadgeLinkTestBase):
    """Special characters in emails must round-trip via |urlencode."""

    def test_plus_becomes_percent_2b_in_href(self):
        member = self._make_member(
            'alex+test@example.com', tier=self.free,
        )
        self._make_override(member, override_tier=self.basic)
        response = self.client.get(f'/studio/users/{member.pk}/')
        body = response.content.decode()
        # Build the exact substring we expect to find on the badge anchor.
        expected_href_attr = (
            'href="'
            + reverse('studio_tier_override')
            + '?email=alex%2Btest%40example.com"'
        )
        self.assertIn(expected_href_attr, body)

        # Locate the badge anchor specifically (the "View full override
        # history" link on the same page is intentionally NOT urlencoded
        # so #562's existing assertion ``alex+test@example.com in href``
        # still holds — this assertion focuses on the badge anchor).
        anchor_marker = 'data-testid="user-detail-tier-badge-link"'
        idx = body.index(anchor_marker)
        # Inspect the snippet around the badge anchor opening tag.
        # The href attribute lives BEFORE this testid on the same <a>
        # element. We grab a slice that comfortably spans the anchor
        # opening tag and assert on its contents.
        snippet = body[max(0, idx - 400):idx + len(anchor_marker) + 50]
        self.assertIn(
            'href="'
            + reverse('studio_tier_override')
            + '?email=alex%2Btest%40example.com"',
            snippet,
        )
        # The badge anchor must NOT carry the raw email — only the
        # encoded form.  If somebody removes |urlencode in the future
        # this assertion catches it without depending on the history
        # link's behaviour.
        self.assertNotIn(
            'href="'
            + reverse('studio_tier_override')
            + '?email=alex+test@example.com"',
            snippet,
        )


class NonOverrideBadgesStayPlainSpansTest(_BadgeLinkTestBase):
    """Stripe / Default badges must NOT be wrapped in an anchor."""

    def test_default_badge_has_no_anchor_wrapper(self):
        member = self._make_member('plain-default@test.com', tier=self.free)
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="default"')
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-badge-link"',
        )

    def test_stripe_badge_has_no_anchor_wrapper(self):
        member = self._make_member(
            'plain-stripe@test.com',
            tier=self.basic,
            stripe_customer_id='cus_568_plain',
        )
        response = self.client.get(f'/studio/users/{member.pk}/')
        self.assertContains(response, 'data-tier-source="stripe"')
        self.assertNotContains(
            response, 'data-testid="user-detail-tier-badge-link"',
        )


class NoNewUrlOrViewIntroducedTest(_BadgeLinkTestBase):
    """Issue #568 is wiring-only: no new route or view is added."""

    def test_no_user_overrides_route_exists(self):
        # The out-of-scope route from the original (rescoped) spec must
        # NOT have been introduced.  We hit the path directly; 404
        # confirms it is not registered.  If somebody adds the route
        # later this test will start failing and the spec must be
        # revisited.
        member = self._make_member('noroute@test.com', tier=self.free)
        response = self.client.get(
            f'/studio/users/{member.pk}/overrides/',
        )
        self.assertEqual(response.status_code, 404)
