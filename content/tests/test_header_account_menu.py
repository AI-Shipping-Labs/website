import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint

User = get_user_model()


class HeaderAccountMenuTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name="May 2026",
            slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
        )

    def _get_header(self, user=None):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_anonymous_header_shows_sign_in_without_account_menu(self):
        html = self._get_header()

        self.assertIn(reverse("account_login"), html)
        self.assertNotIn('data-testid="account-menu"', html)
        self.assertNotIn('id="account-menu-trigger"', html)
        self.assertNotIn('id="notification-bell-btn"', html)

    def test_authenticated_header_uses_compact_account_menu(self):
        user = User.objects.create_user(
            email="member@example.com",
            password="pw",
            first_name="Ada",
            last_name="Lovelace",
        )

        html = self._get_header(user)

        self.assertIn('id="notification-bell-btn"', html)
        self.assertIn('data-testid="account-menu-trigger"', html)
        self.assertIn('aria-haspopup="menu"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn("Ada Lovelace", html)
        self.assertIn("member@example.com", html)
        self.assertIn(f'href="{reverse("account")}"', html)
        self.assertIn('href="/account/#profile"', html)
        self.assertIn(f'href="{reverse("account_logout")}"', html)
        self.assertNotIn(
            'class="text-sm text-muted-foreground transition-colors '
            'hover:text-foreground">member@example.com</a>',
            html,
        )

    def test_account_menu_falls_back_to_email_when_name_is_blank(self):
        user = User.objects.create_user(
            email="long.account.identity@example.com",
            password="pw",
        )

        html = self._get_header(user)

        self.assertIn(
            '<p class="truncate text-sm font-medium text-popover-foreground">'
            "long.account.identity@example.com</p>",
            html,
        )
        self.assertNotIn(
            '<p class="truncate text-xs text-muted-foreground">'
            "long.account.identity@example.com</p>",
            html,
        )

    def test_plan_and_studio_are_conditional_account_menu_items(self):
        member = User.objects.create_user(email="member2@example.com", password="pw")
        staff = User.objects.create_user(
            email="staff@example.com",
            password="pw",
            is_staff=True,
        )

        member_html = self._get_header(member)
        self.assertNotIn('data-testid="header-plan-link"', member_html)
        self.assertNotIn(reverse("studio_dashboard"), member_html)

        self.client.logout()
        plan = Plan.objects.create(
            member=staff,
            sprint=self.sprint,
            visibility="private",
        )
        staff_html = self._get_header(staff)
        plan_href = reverse("my_plan_detail", kwargs={"plan_id": plan.pk})
        self.assertIn(f'href="{plan_href}"', staff_html)
        self.assertIn('data-testid="header-plan-link"', staff_html)
        self.assertIn('data-testid="mobile-header-plan-link"', staff_html)
        self.assertIn(reverse("studio_dashboard"), staff_html)

    def test_mobile_account_section_groups_authenticated_actions(self):
        user = User.objects.create_user(
            email="mobile@example.com",
            password="pw",
            first_name="Mobile",
            last_name="Member",
        )

        html = self._get_header(user)

        self.assertIn('data-testid="mobile-account-section"', html)
        self.assertIn("Mobile Member", html)
        self.assertIn("Notifications", html)
        self.assertIn("mobile-notification-badge", html)
        self.assertIn("mobile-learn-toggle", html)
        self.assertIn("mobile-community-toggle", html)
        section_start = html.index('data-testid="mobile-account-section"')
        logout_start = html.index(reverse("account_logout"), section_start)
        mobile_account_html = html[section_start:logout_start]
        self.assertIn('data-testid="theme-toggle"', mobile_account_html)

    def test_account_menu_interaction_hooks_are_rendered(self):
        user = User.objects.create_user(email="keys@example.com", password="pw")

        html = self._get_header(user)

        self.assertIn("Account menu", html)
        self.assertIn("trigger.setAttribute('aria-expanded', 'true')", html)
        self.assertIn("trigger.setAttribute('aria-expanded', 'false')", html)
        self.assertIn("e.key === 'Escape'", html)
        self.assertIn("!container.contains(e.target)", html)
