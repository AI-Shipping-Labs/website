"""Tests for mobile responsive fixes on account and auth pages (issue #177)."""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from payments.models import Tier

User = get_user_model()


class AccountPageMobilePaddingTest(TestCase):
    """Account page card sections use p-5 sm:p-8 padding."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="mobile@example.com", password="testpass123"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_membership_card_has_responsive_padding(self):
        """Membership section uses p-5 sm:p-8 instead of just p-8."""
        response = self.client.get("/account/")
        content = response.content.decode()
        # Find the Membership heading with the crown icon
        membership_heading_pos = content.index('<i data-lucide="crown"')
        preceding = content[max(0, membership_heading_pos - 200):membership_heading_pos]
        self.assertIn("p-5 sm:p-8", preceding)

    def test_email_preferences_card_has_responsive_padding(self):
        """Email preferences section uses p-5 sm:p-8."""
        response = self.client.get("/account/")
        content = response.content.decode()
        section_match = re.search(
            r'id="email-preferences-section"[^>]*class="[^"]*"', content
        )
        if not section_match:
            section_match = re.search(
                r'class="[^"]*"[^>]*id="email-preferences-section"', content
            )
        self.assertIsNotNone(section_match)
        self.assertIn("p-5 sm:p-8", section_match.group(0))

    def test_change_password_card_has_responsive_padding(self):
        """Change password section uses p-5 sm:p-8."""
        response = self.client.get("/account/")
        content = response.content.decode()
        section_match = re.search(
            r'id="change-password-section"[^>]*class="[^"]*"', content
        )
        if not section_match:
            section_match = re.search(
                r'class="[^"]*"[^>]*id="change-password-section"', content
            )
        self.assertIsNotNone(section_match)
        self.assertIn("p-5 sm:p-8", section_match.group(0))


class AccountPageActionButtonsStackTest(TestCase):
    """Action buttons stack vertically on narrow screens."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="buttons@example.com", password="testpass123"
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_action_buttons_use_flex_col_sm_flex_row(self):
        """Action buttons container has flex-col sm:flex-row for mobile stacking."""
        response = self.client.get("/account/")
        content = response.content.decode()
        # Find the action buttons container near the upgrade/cancel buttons
        upgrade_pos = content.find('id="upgrade-btn"')
        if upgrade_pos == -1:
            # Free user -- look for the pricing link
            upgrade_pos = content.find('href="/pricing"')
        self.assertNotEqual(upgrade_pos, -1)
        # Look backwards for the flex container
        preceding = content[max(0, upgrade_pos - 400):upgrade_pos]
        self.assertIn("flex-col sm:flex-row", preceding)


class AccountPageModalMobileTest(TestCase):
    """Modals on the account page are usable on 375px screens."""

    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug="main")
        cls.user = User.objects.create_user(
            email="modal@example.com", password="testpass123"
        )
        cls.user.tier = cls.main_tier
        cls.user.subscription_id = "sub_test_modal"
        cls.user.save(update_fields=["tier", "subscription_id"])

    def setUp(self):
        self.client.force_login(self.user)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_modals_have_responsive_padding(self):
        """Modal content areas use p-5 sm:p-8 for better mobile spacing."""
        response = self.client.get("/account/")
        content = response.content.decode()
        # Check upgrade modal
        upgrade_modal_pos = content.index('id="upgrade-modal"')
        modal_section = content[upgrade_modal_pos:upgrade_modal_pos + 500]
        self.assertIn("p-5 sm:p-8", modal_section)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_cancel_modal_has_responsive_padding(self):
        """Cancel modal content area uses p-5 sm:p-8."""
        response = self.client.get("/account/")
        content = response.content.decode()
        cancel_modal_pos = content.index('id="cancel-modal"')
        modal_section = content[cancel_modal_pos:cancel_modal_pos + 500]
        self.assertIn("p-5 sm:p-8", modal_section)


class CancelModalTapTargetsTest(TestCase):
    """Cancel modal checkbox and input have adequate tap targets."""

    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug="main")
        cls.user = User.objects.create_user(
            email="cancel-tap@example.com", password="testpass123"
        )
        cls.user.tier = cls.main_tier
        cls.user.subscription_id = "sub_cancel_tap"
        cls.user.save(update_fields=["tier", "subscription_id"])

    def setUp(self):
        self.client.force_login(self.user)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_cancel_checkbox_has_larger_size(self):
        """Cancel confirmation checkbox uses h-5 w-5 for better tappability."""
        response = self.client.get("/account/")
        content = response.content.decode()
        checkbox_match = re.search(
            r'id="cancel-confirm-checkbox"[^>]*class="[^"]*"', content
        )
        if not checkbox_match:
            checkbox_match = re.search(
                r'class="[^"]*"[^>]*id="cancel-confirm-checkbox"', content
            )
        self.assertIsNotNone(checkbox_match)
        self.assertIn("h-5 w-5", checkbox_match.group(0))

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_cancel_checkbox_label_has_min_height(self):
        """Cancel checkbox label has min-h-[44px] for adequate tap target."""
        response = self.client.get("/account/")
        content = response.content.decode()
        checkbox_pos = content.index('id="cancel-confirm-checkbox"')
        # The label is the parent element before the checkbox
        preceding = content[max(0, checkbox_pos - 300):checkbox_pos]
        self.assertIn("min-h-[44px]", preceding)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_cancel_confirm_input_uses_text_base(self):
        """Cancel confirm text input uses text-base (16px) to prevent iOS zoom."""
        response = self.client.get("/account/")
        content = response.content.decode()
        input_match = re.search(
            r'id="cancel-confirm-text"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="cancel-confirm-text"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_cancel_modal_buttons_stack_on_mobile(self):
        """Cancel modal action buttons use flex-col-reverse sm:flex-row for mobile stacking."""
        response = self.client.get("/account/")
        content = response.content.decode()
        cancel_btn_pos = content.index('id="confirm-cancel-btn"')
        preceding = content[max(0, cancel_btn_pos - 300):cancel_btn_pos]
        self.assertIn("flex-col-reverse sm:flex-row", preceding)


class FormInputTextBaseTest(TestCase):
    """All form inputs use text-base (16px) to prevent iOS zoom on focus."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="textbase@example.com", password="testpass123"
        )

    def test_login_email_input_uses_text_base(self):
        """Login email input uses text-base."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        input_match = re.search(
            r'id="login-email"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="login-email"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_login_password_input_uses_text_base(self):
        """Login password input uses text-base."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        input_match = re.search(
            r'id="login-password"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="login-password"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_register_email_input_uses_text_base(self):
        """Register email input uses text-base."""
        response = self.client.get("/accounts/register/")
        content = response.content.decode()
        input_match = re.search(
            r'id="register-email"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="register-email"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_register_password_input_uses_text_base(self):
        """Register password input uses text-base."""
        response = self.client.get("/accounts/register/")
        content = response.content.decode()
        input_match = re.search(
            r'id="register-password"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="register-password"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_register_password_confirm_input_uses_text_base(self):
        """Register password confirm input uses text-base."""
        response = self.client.get("/accounts/register/")
        content = response.content.decode()
        input_match = re.search(
            r'id="register-password-confirm"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="register-password-confirm"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_password_reset_request_email_input_uses_text_base(self):
        """Password reset request email input uses text-base."""
        response = self.client.get("/accounts/password-reset-request")
        content = response.content.decode()
        input_match = re.search(
            r'id="password-reset-email"[^>]*class="[^"]*"', content
        )
        if not input_match:
            input_match = re.search(
                r'class="[^"]*"[^>]*id="password-reset-email"', content
            )
        self.assertIsNotNone(input_match)
        self.assertIn("text-base", input_match.group(0))

    def test_account_password_inputs_use_text_base(self):
        """Account page change password inputs use text-base."""
        self.client.force_login(self.user)
        response = self.client.get("/account/")
        content = response.content.decode()
        for input_id in ["current-password", "new-password", "confirm-new-password"]:
            input_match = re.search(
                rf'id="{input_id}"[^>]*class="[^"]*"', content
            )
            if not input_match:
                input_match = re.search(
                    rf'class="[^"]*"[^>]*id="{input_id}"', content
                )
            self.assertIsNotNone(input_match, f"Input {input_id} not found")
            self.assertIn(
                "text-base",
                input_match.group(0),
                f"Input {input_id} should use text-base",
            )


class LoginPageLinksWrapTest(TestCase):
    """Login page links wrap gracefully on narrow screens."""

    def test_login_links_container_has_flex_wrap(self):
        """Forgot password and Create account links container has flex-wrap for graceful wrapping."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        # Find the container with both links
        forgot_pos = content.index('id="forgot-password-link"')
        preceding = content[max(0, forgot_pos - 300):forgot_pos]
        self.assertIn("flex-wrap", preceding)
        self.assertIn("gap-2", preceding)


class LoginPageMobilePaddingTest(TestCase):
    """Login page card uses responsive padding."""

    def test_login_card_has_responsive_padding(self):
        """Login card uses p-5 sm:p-8."""
        response = self.client.get("/accounts/login/")
        content = response.content.decode()
        # Find the card div containing the sign-in heading
        signin_pos = content.index("Sign in</h1>")
        preceding = content[max(0, signin_pos - 400):signin_pos]
        self.assertIn("p-5 sm:p-8", preceding)


class RegisterPageMobilePaddingTest(TestCase):
    """Register page card uses responsive padding."""

    def test_register_card_has_responsive_padding(self):
        """Register card uses p-5 sm:p-8."""
        response = self.client.get("/accounts/register/")
        content = response.content.decode()
        heading_pos = content.index("Create Account</h1>")
        preceding = content[max(0, heading_pos - 400):heading_pos]
        self.assertIn("p-5 sm:p-8", preceding)


class PasswordResetRequestMobilePaddingTest(TestCase):
    """Password reset request page card uses responsive padding."""

    def test_password_reset_request_card_has_responsive_padding(self):
        """Password reset request card uses p-5 sm:p-8."""
        response = self.client.get("/accounts/password-reset-request")
        content = response.content.decode()
        heading_pos = content.index("Reset your password</h1>")
        preceding = content[max(0, heading_pos - 400):heading_pos]
        self.assertIn("p-5 sm:p-8", preceding)
