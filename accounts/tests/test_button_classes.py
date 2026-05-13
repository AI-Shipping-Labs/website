"""Tests for product-surface button class constants (issue #598)."""

import datetime
import re

from django.contrib.auth import get_user_model
from django.template import Context, Template, TemplateSyntaxError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.templatetags.accounts_extras import (
    PRODUCT_BUTTON_CLASSES,
    button_classes,
)
from plans.models import Plan, Sprint
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _tag_class(html, marker):
    marker_idx = html.find(marker)
    if marker_idx == -1:
        raise AssertionError(f"{marker!r} not found in response")
    tag_start = html.rfind("<", 0, marker_idx)
    tag_end = html.find(">", marker_idx)
    tag = html[tag_start:tag_end + 1]
    match = re.search(r'class="([^"]+)"', tag)
    if match is None:
        raise AssertionError(f"No class attribute found for {marker!r}: {tag}")
    return match.group(1)


class ButtonClassesTagTest(TestCase):
    def test_primary_secondary_and_destructive_variants_return_canonical_strings(self):
        self.assertEqual(button_classes('primary'), PRODUCT_BUTTON_CLASSES['primary'])
        self.assertEqual(
            button_classes('secondary'), PRODUCT_BUTTON_CLASSES['secondary'],
        )
        self.assertEqual(
            button_classes('destructive'), PRODUCT_BUTTON_CLASSES['destructive'],
        )

    def test_extra_classes_are_appended(self):
        self.assertEqual(
            button_classes('primary', 'w-full sm:w-auto'),
            f"{PRODUCT_BUTTON_CLASSES['primary']} w-full sm:w-auto",
        )

    def test_unknown_variant_raises_template_syntax_error(self):
        with self.assertRaises(TemplateSyntaxError):
            button_classes('ghost')

    def test_unknown_variant_fails_during_template_render(self):
        template = Template("{% load accounts_extras %}{% button_classes 'ghost' %}")

        with self.assertRaises(TemplateSyntaxError):
            template.render(Context({}))


class ProductButtonRenderedClassTest(TierSetupMixin, TestCase):
    def test_dashboard_welcome_and_plan_ctas_use_canonical_classes(self):
        user = User.objects.create_user(
            email='dashboard-buttons@test.com',
            password='pw',
            tier=self.main_tier,
            email_verified=True,
        )
        sprint = Sprint.objects.create(
            name='Button Sprint',
            slug='button-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=4,
            status='active',
        )
        plan = Plan.objects.create(member=user, sprint=sprint, status='active')
        self.client.force_login(user)

        response = self.client.get('/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'class="{PRODUCT_BUTTON_CLASSES["secondary"]}"', html)
        self.assertIn(f'class="{PRODUCT_BUTTON_CLASSES["primary"]}"', html)
        self.assertEqual(
            _tag_class(html, 'data-testid="account-sprint-plan-open"'),
            PRODUCT_BUTTON_CLASSES['primary'],
        )
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
        )

    @override_settings(STRIPE_CUSTOMER_PORTAL_URL='https://billing.example.test/portal')
    def test_account_page_membership_and_form_ctas_use_canonical_classes(self):
        user = User.objects.create_user(
            email='account-buttons@test.com',
            password='pw',
            tier=self.main_tier,
            subscription_id='sub_test_buttons',
            billing_period_end=timezone.now() + datetime.timedelta(days=30),
            email_verified=True,
        )
        self.client.force_login(user)

        response = self.client.get('/account/')
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _tag_class(html, 'id="profile-save-btn"'),
            PRODUCT_BUTTON_CLASSES['primary'],
        )
        self.assertEqual(
            _tag_class(html, 'id="manage-subscription-btn"'),
            PRODUCT_BUTTON_CLASSES['primary'],
        )
        self.assertNotIn('id="upgrade-btn"', html)
        self.assertNotIn('id="downgrade-btn"', html)
        self.assertNotIn('id="cancel-btn"', html)
        self.assertEqual(
            _tag_class(html, 'id="save-timezone-btn"'),
            PRODUCT_BUTTON_CLASSES['primary'],
        )
        self.assertEqual(
            _tag_class(html, 'id="clear-timezone-btn"'),
            PRODUCT_BUTTON_CLASSES['secondary'],
        )

    def test_account_verification_banner_button_preserves_amber_overrides(self):
        user = User.objects.create_user(
            email='unverified-account-buttons@test.com',
            password='pw',
            email_verified=False,
        )
        self.client.force_login(user)

        response = self.client.get('/account/')
        html = response.content.decode()
        button_class = _tag_class(html, 'id="resend-verification-btn"')

        self.assertEqual(response.status_code, 200)
        self.assertIn(PRODUCT_BUTTON_CLASSES['secondary'], button_class)
        self.assertIn('!border-amber-500/40', button_class)
        self.assertIn('!text-amber-200', button_class)
        self.assertIn('hover:!bg-amber-500/20', button_class)

    def test_my_plan_nav_uses_canonical_secondary_classes(self):
        user = User.objects.create_user(
            email='plan-buttons@test.com',
            password='pw',
            email_verified=True,
        )
        sprint = Sprint.objects.create(
            name='Plan Sprint',
            slug='plan-sprint',
            start_date=datetime.date(2026, 8, 1),
            duration_weeks=4,
            status='active',
        )
        plan = Plan.objects.create(member=user, sprint=sprint, status='active')
        self.client.force_login(user)

        response = self.client.get(
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _tag_class(html, 'data-testid="view-sprint-detail-cta"'),
            PRODUCT_BUTTON_CLASSES['secondary'],
        )
        self.assertEqual(
            _tag_class(html, 'data-testid="view-cohort-board-cta"'),
            PRODUCT_BUTTON_CLASSES['secondary'],
        )

    def test_inline_plan_body_buttons_remain_compact_exceptions(self):
        template = open('templates/plans/_plan_body.html', encoding='utf-8').read()

        self.assertIn('bg-accent px-3 py-1.5', template)
        self.assertIn('min-h-[44px] items-center gap-2 rounded-md bg-accent px-3 py-2', template)
        self.assertNotIn("button_classes 'primary'", template)
