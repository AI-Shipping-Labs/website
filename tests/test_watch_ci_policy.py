"""Deploy-readiness policy boundaries for the CI watcher."""

from django.test import SimpleTestCase, tag

from tests.test_watch_ci import (
    FakeGh,
    all_required_success,
    job,
    make_watcher,
    replace_job,
    run_payload,
    watch_ci,
)


def remove_job(jobs, name):
    for index, existing in enumerate(jobs):
        if existing["name"] == name:
            jobs.pop(index)
            return
    raise AssertionError(f"required job {name!r} is not in the job list")


@tag("core")
class SkippedGatingTest(SimpleTestCase):
    def test_incident_cancelled_postgres_and_skipped_deploy_is_no_verdict(self):
        jobs = all_required_success()
        replace_job(
            jobs,
            "PostgreSQL 16 Verification",
            status="completed",
            conclusion="cancelled",
        )
        replace_job(jobs, "Deploy to Dev", status="completed", conclusion="skipped")
        gh = FakeGh(views=[run_payload(status="completed", conclusion="success", jobs=jobs)])
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertEqual(verdict.exit_code, 5)
        self.assertEqual(
            verdict.failing_jobs,
            ["PostgreSQL 16 Verification", "Deploy to Dev"],
        )
        self.assertIn("PostgreSQL 16 Verification (cancelled)", verdict.reason)
        self.assertIn("Deploy to Dev (skipped)", verdict.reason)
        self.assertIn("no verified deployment was observed", verdict.reason)

    def test_failed_postgres_followed_by_skipped_deploy_reports_failure(self):
        jobs = all_required_success()
        replace_job(jobs, "PostgreSQL 16 Verification", conclusion="failure")
        replace_job(jobs, "Deploy to Dev", conclusion="skipped")
        gh = FakeGh(
            views=[run_payload(status="completed", conclusion="failure", jobs=jobs)],
            log_failed="",
        )
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.FAILED)
        self.assertEqual(verdict.exit_code, 1)
        self.assertEqual(verdict.failing_jobs, ["PostgreSQL 16 Verification"])
        self.assertIn("PostgreSQL 16 Verification (failure)", verdict.reason)
        self.assertIn("Deploy to Dev (skipped)", verdict.reason)

    def test_default_deploy_dev_rejects_missing_partial_and_skipped_jobs(self):
        cases = (
            ("missing postgres", "PostgreSQL 16 Verification", None, "missing"),
            ("partial postgres", "PostgreSQL 16 Verification", ("in_progress", ""), "in_progress"),
            ("skipped postgres", "PostgreSQL 16 Verification", ("completed", "skipped"), "skipped"),
            ("skipped deploy", "Deploy to Dev", ("completed", "skipped"), "skipped"),
        )
        for label, blocked_name, state, expected_state in cases:
            with self.subTest(label):
                jobs = all_required_success()
                if state is None:
                    remove_job(jobs, blocked_name)
                else:
                    replace_job(jobs, blocked_name, status=state[0], conclusion=state[1])
                gh = FakeGh(views=[run_payload(status="completed", conclusion="success", jobs=jobs)])
                verdict = make_watcher(gh).watch(run_id="100")

                self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
                self.assertEqual(verdict.failing_jobs, [blocked_name])
                self.assertIn(f"{blocked_name} ({expected_state})", verdict.reason)
                self.assertEqual(
                    "no verified deployment was observed" in verdict.reason,
                    blocked_name == "Deploy to Dev",
                )

    def test_custom_workflow_preserves_explicitly_allowed_skip(self):
        required = ["Validation", "Optional report"]
        jobs = [job("Validation"), job("Optional report", conclusion="skipped")]
        gh = FakeGh(
            views=[
                run_payload(
                    status="completed",
                    conclusion="success",
                    jobs=jobs,
                    workflow="Custom Workflow",
                    branch="custom",
                )
            ]
        )
        watcher = make_watcher(
            gh,
            workflow="Custom Workflow",
            branch="custom",
            required_checks=required,
            allowed_skips=["Optional report"],
        )
        verdict = watcher.watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.GREEN)
