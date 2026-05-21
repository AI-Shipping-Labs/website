"""Copy-regression tests for the per-flow verification templates (issue #767).

After the split, the signup template must NOT use newsletter framing,
and the subscribe template must NOT threaten account deletion. These
tests read the four shipped template files directly from disk so a
copywriter who edits the markdown gets a fast, localised failure.

The same tests assert the two legacy files (``email_verification.md``
and ``email_verification_reminder.md``) no longer exist on disk — per
the orchestrator simplification directive, the split is hard, no alias.
"""

from pathlib import Path

from django.test import SimpleTestCase

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "email_templates"
)


def _read(template_name):
    return (TEMPLATES_DIR / f"{template_name}.md").read_text(encoding="utf-8")


class VerificationTemplateCopyTest(SimpleTestCase):
    """The new templates exist with the correct per-flow framing."""

    def test_signup_template_has_no_newsletter_framing(self):
        body = _read("email_verification_signup").lower()
        # Signup users explicitly created an account — must not be told
        # they "subscribed to" anything, and must not mention "newsletter".
        self.assertNotIn("subscribing to", body)
        self.assertNotIn("newsletter", body)

    def test_subscribe_template_has_no_account_deletion_framing(self):
        body = _read("email_verification_subscribe").lower()
        # Subscribe-path users typed their email into a newsletter form;
        # threatening "your account will be deleted" makes them think a
        # phantom account was created behind their back.
        self.assertNotIn("account will be deleted", body)
        self.assertNotIn("account will be removed", body)
        self.assertNotIn("your account", body)

    def test_signup_reminder_template_has_no_newsletter_framing(self):
        body = _read("email_verification_signup_reminder").lower()
        self.assertNotIn("newsletter", body)
        self.assertNotIn("subscription", body)

    def test_subscribe_reminder_template_has_no_account_deletion_framing(self):
        body = _read("email_verification_subscribe_reminder").lower()
        self.assertNotIn("account will be removed", body)
        self.assertNotIn("account will be deleted", body)
        self.assertNotIn("your account", body)

    def test_signup_template_uses_account_framing(self):
        body = _read("email_verification_signup").lower()
        # Positive assertion: at least one signup-framing phrase appears
        # so the test would fail if the template were emptied or
        # replaced by the subscribe copy by mistake.
        self.assertTrue(
            "signing up" in body or "your account" in body,
            "signup template should use account framing",
        )

    def test_subscribe_template_uses_subscription_framing(self):
        body = _read("email_verification_subscribe").lower()
        self.assertIn("confirm", body)
        self.assertTrue(
            "subscription" in body or "subscribe" in body,
            "subscribe template should use subscription framing",
        )


class LegacyVerificationTemplatesDeletedTest(SimpleTestCase):
    """Per the orchestrator simplification directive, the legacy files
    are deleted outright — no alias, no migration window.
    """

    def test_legacy_email_verification_file_is_gone(self):
        self.assertFalse(
            (TEMPLATES_DIR / "email_verification.md").exists(),
            "legacy email_verification.md must be deleted (issue #767)",
        )

    def test_legacy_email_verification_reminder_file_is_gone(self):
        self.assertFalse(
            (TEMPLATES_DIR / "email_verification_reminder.md").exists(),
            "legacy email_verification_reminder.md must be deleted (issue #767)",
        )

    def test_all_four_new_files_exist(self):
        for slug in (
            "email_verification_signup",
            "email_verification_subscribe",
            "email_verification_signup_reminder",
            "email_verification_subscribe_reminder",
        ):
            self.assertTrue(
                (TEMPLATES_DIR / f"{slug}.md").exists(),
                f"{slug}.md must exist on disk (issue #767)",
            )
