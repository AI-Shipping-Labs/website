"""Maven welcome email content tests (issue #960)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.services.email_service import EmailService
from integrations.services.maven import _welcome_context

User = get_user_model()


class MavenWelcomeEmailContentTest(TestCase):
    def test_welcome_email_is_course_framed_with_consent_and_opt_out(self):
        user = User.objects.create_user(
            email="enrollee@test.com", password="x", first_name="Sam",
        )
        context = _welcome_context(user, "LLM Zoomcamp")
        subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        # Course-framed: names the course.
        self.assertIn("LLM Zoomcamp", subject)
        self.assertIn("LLM Zoomcamp", body_html)
        # Sign-in / set-password CTA.
        self.assertIn(context["sign_in_url"], body_html)
        self.assertIn(context["password_reset_url"], body_html)
        # Transparent notice that they were added for course communication.
        self.assertIn("community access", body_html.lower())
        self.assertIn("did not add you to any marketing", body_html.lower())
        # Opt-out link + reply-to-remove line.
        self.assertIn(context["opt_out_url"], body_html)
        self.assertIn("reply", body_html.lower())

    def test_opt_out_url_uses_unsubscribe_token_endpoint(self):
        user = User.objects.create_user(email="o@test.com", password="x")
        context = _welcome_context(user, "Course")
        self.assertIn("/api/unsubscribe?token=", context["opt_out_url"])
