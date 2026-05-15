"""Tests for email kind classification (issue #655)."""

from django.test import SimpleTestCase

from email_app.services.email_classification import (
    EMAIL_KIND_PROMOTIONAL,
    PROMOTIONAL_EMAIL_TYPES,
    classify_email_type,
)


class WorkshopAnnouncementClassificationTest(SimpleTestCase):
    """``workshop_announcement`` must classify as promotional so SES uses
    the promotional sender, ``EmailService`` honours the global newsletter
    opt-out, and the unsubscribe footer is rendered (issue #655)."""

    def test_workshop_announcement_classified_as_promotional(self):
        self.assertEqual(
            classify_email_type('workshop_announcement'),
            EMAIL_KIND_PROMOTIONAL,
        )

    def test_workshop_announcement_in_promotional_set(self):
        self.assertIn('workshop_announcement', PROMOTIONAL_EMAIL_TYPES)
