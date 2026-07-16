"""R1 keeps the production-image onboarding notification vocabulary."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, tag

from questionnaires.services_onboarding_ai import _enqueue_staff_notification


@tag("core")
class R1OnboardingProducerGateTest(SimpleTestCase):
    @patch("crm.services.onboarding_notify.notify_staff_onboarding_submitted")
    @patch("questionnaires.models.OnboardingTurnAttempt.objects.select_related")
    def test_r1_uses_synchronous_legacy_notification(self, select_related, notify):
        respondent = object()
        select_related.return_value.get.return_value = SimpleNamespace(
            conversation=SimpleNamespace(
                response=SimpleNamespace(respondent=respondent),
            ),
        )

        _enqueue_staff_notification(42)

        select_related.assert_called_once_with(
            'conversation__response__respondent',
        )
        notify.assert_called_once_with(respondent)
