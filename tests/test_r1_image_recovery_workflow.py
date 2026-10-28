from pathlib import Path

from django.test import SimpleTestCase, tag

from jobs.management.commands.setup_schedules import R2_ONLY_SCHEDULE_NAMES
from website.release_phase import (
    R1_EXPAND_COMPATIBILITY,
    R2_BACKGROUND_WORK_ENABLED,
    background_work_enabled,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-dev.yml"


@tag("core")
class R1ImageRecoveryWorkflowContractTest(SimpleTestCase):
    def test_candidate_remains_compile_time_r1(self):
        self.assertIs(R1_EXPAND_COMPATIBILITY, True)
        self.assertIs(R2_BACKGROUND_WORK_ENABLED, False)
        self.assertIs(background_work_enabled(), False)
        self.assertEqual(
            R2_ONLY_SCHEDULE_NAMES,
            (
                "cleanup-calendly-webhook-logs",
                "retry-calendly-webhooks",
                "resume-webhook-deliveries",
                "redact-maven-enrollment-pii",
                "retry-maven-enrollment-steps",
                "retry-stuck-recording-uploads",
                "purge-plan-sprints-raw-text",
                "onboarding-staff-notification-recovery",
                "booking-call-capture",
            ),
        )

    def test_deploy_dev_retires_obsolete_ecr_rehearsal(self):
        workflow = WORKFLOW.read_text()

        self.assertNotIn("Rehearse exact R1 image rollback", workflow)
        self.assertNotIn("R1_PRODUCTION_TAG", workflow)
        self.assertNotIn("verify_r1_image_rollback_recovery.sh", workflow)
