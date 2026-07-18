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
REHEARSAL = ROOT / "deploy" / "verify_r1_image_rollback_recovery.sh"


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
                "purge-plan-sprints-raw-text",
                "onboarding-staff-notification-recovery",
            ),
        )

    def test_deploy_dev_gates_ecs_rollout_on_exact_r1_rehearsal(self):
        workflow = WORKFLOW.read_text()
        rehearsal_step = workflow.index(
            "Rehearse exact R1 image rollback and forward recovery",
        )
        deploy_step = workflow.index("- name: Deploy to Dev")
        self.assertLess(rehearsal_step, deploy_step)
        self.assertIn("R1_PRODUCTION_TAG: 20260716-162837-dc07564", workflow)
        self.assertIn("verify_r1_image_rollback_recovery.sh", workflow)
        self.assertIn("PREDEPLOY_MIGRATE_CHECK_ENABLED", workflow)

    def test_rehearsal_is_ephemeral_image_only_and_idempotent(self):
        script = REHEARSAL.read_text()
        self.assertIn("postgres:16", script)
        self.assertIn("docker pull \"${R1_IMAGE}\"", script)
        self.assertIn("docker image inspect \"${R1_IMAGE}\"", script)
        self.assertIn("candidate is no longer in R1", script)
        self.assertLess(
            script.index("candidate is no longer in R1"),
            script.index("docker pull \"${R1_IMAGE}\""),
        )
        self.assertIn("manage \"${R1_IMAGE}\" migrate --noinput", script)
        self.assertIn("manage \"${CANDIDATE_IMAGE}\" migrate --noinput", script)
        self.assertGreaterEqual(script.count("reconcile_r1_expand"), 4)
        self.assertNotIn("migrate content 0054", script)
        self.assertNotIn("migrate email_app 0019", script)
        self.assertNotIn("aws ", script)
        self.assertNotIn("aishippinglabs.com", script)
        self.assertIn("candidate-era-workshop", script)
        self.assertIn("0056_reconcile_workshop_preview_tokens", script)
        self.assertIn("EmailLog.objects.count() == 4", script)
