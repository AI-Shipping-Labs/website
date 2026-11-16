"""Phase model for one learner's project attempt (content.services.project_phase)."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from content.models import Cohort, CourseProject
from content.services.project_phase import attempt_phase
from content.services.project_results import ProjectResult

SUBMISSION_DUE = datetime(2026, 3, 1, 12, 0, tzinfo=dt_timezone.utc)  # date-rot-ok: fixed clock
REVIEW_DUE = SUBMISSION_DUE + timedelta(days=7)
BEFORE = SUBMISSION_DUE - timedelta(hours=1)
REVIEWING = SUBMISSION_DUE + timedelta(days=1)
AFTER = REVIEW_DUE + timedelta(days=1)


def dated_project(state='CS'):
    return CourseProject(
        slug='attempt-1', title='Attempt 1', state=state,
        submission_due_at=SUBMISSION_DUE, review_due_at=REVIEW_DUE,
    )


def self_paced_project():
    project = CourseProject(slug='project', title='Project')
    project.cohort = Cohort(pk=1, mode='self_paced')
    return project


def submission(status='submitted'):
    return SimpleNamespace(status=status)


def reviews(*complete):
    return [SimpleNamespace(is_complete=flag) for flag in complete]


def result(total, passed):
    return ProjectResult(total=total, criteria=(), review_count=3, passing_score=12, passed=passed)


class DatedPhaseTest(SimpleTestCase):
    """Dated attempts follow the staff-set state; the clock never moves them."""

    def test_no_submission_while_collecting_is_submit_with_open_badge(self):
        phase = attempt_phase(dated_project(), None, [], BEFORE)
        self.assertEqual((phase.key, phase.badge_label), ('submit', 'Open'))
        self.assertEqual((phase.deadline, phase.deadline_kind), (SUBMISSION_DUE, 'submission'))
        self.assertEqual((phase.action_url_name, phase.action_label), ('course_project_submit', 'Submit project'))

    def test_submitted_while_collecting_shows_submitted_and_update_action(self):
        phase = attempt_phase(dated_project(), submission(), [], BEFORE)
        self.assertEqual((phase.key, phase.badge_label, phase.action_label), ('submit', 'Submitted', 'Update submission'))

    def test_past_submission_deadline_alone_does_not_change_the_phase(self):
        phase = attempt_phase(dated_project(), None, [], AFTER)
        self.assertEqual((phase.key, phase.badge_label), ('submit', 'Open'))
        self.assertTrue(phase.deadline_overdue)

    def test_no_submission_once_peer_reviewing_is_missed(self):
        phase = attempt_phase(dated_project('PR'), None, [], REVIEWING)
        self.assertEqual((phase.key, phase.label, phase.badge_label), ('missed', 'Missed', 'Not submitted'))
        self.assertIsNone(phase.deadline)

    def test_submitted_without_assignment_while_peer_reviewing_waits(self):
        phase = attempt_phase(dated_project('PR'), submission(), [], REVIEWING)
        self.assertEqual(phase.key, 'waiting')
        self.assertEqual(phase.action_url_name, '')
        self.assertEqual(phase.deadline, REVIEW_DUE)

    def test_pending_review_is_review_phase_with_progress(self):
        phase = attempt_phase(dated_project('PR'), submission('in_review'), reviews(True, False, False), REVIEWING)
        self.assertEqual((phase.key, phase.badge_label), ('review', 'Review 1/3'))
        self.assertEqual((phase.reviews_done, phase.reviews_assigned), (1, 3))
        self.assertTrue(phase.certificate_blocked)
        self.assertEqual(phase.action_label, 'Continue reviews')

    def test_all_reviews_done_stays_in_review_with_review_completed_badge(self):
        phase = attempt_phase(
            dated_project('PR'), submission('in_review'), reviews(True, True, True),
            REVIEWING, result(20, True),
        )
        self.assertEqual((phase.key, phase.badge_label), ('review', 'Review completed'))
        self.assertFalse(phase.results_final)
        self.assertFalse(phase.certificate_blocked)

    def test_review_deadline_passing_does_not_end_peer_review(self):
        phase = attempt_phase(
            dated_project('PR'), submission('in_review'), reviews(False), AFTER, result(20, True),
        )
        self.assertEqual((phase.key, phase.badge_label), ('review', 'Review 0/1'))
        self.assertFalse(phase.results_final)
        self.assertTrue(phase.deadline_overdue)

    def test_completed_is_feedback_with_final_badges(self):
        cases = ((result(14, True), 'Passed (14)'), (result(9, False), 'Failed (9)'))
        for project_result, label in cases:
            with self.subTest(label=label):
                phase = attempt_phase(
                    dated_project('CO'), submission('review_complete'), reviews(True), BEFORE, project_result,
                )
                self.assertEqual((phase.key, phase.badge_label), ('feedback', label))
                self.assertTrue(phase.results_final)

    def test_completed_with_pending_reviews_is_final_and_blocks_certificate(self):
        phase = attempt_phase(dated_project('CO'), submission('in_review'), reviews(False), BEFORE)
        self.assertEqual(phase.key, 'feedback')
        self.assertTrue(phase.results_final)
        self.assertTrue(phase.certificate_blocked)

    def test_completed_badge_without_passing_score_is_score_only(self):
        unset = ProjectResult(total=7, criteria=(), review_count=1, passing_score=0, passed=None)
        phase = attempt_phase(dated_project('CO'), submission(), reviews(True), AFTER, unset)
        self.assertEqual((phase.badge_key, phase.badge_label), ('scored', '7 points'))

    def test_completed_without_submission_is_missed(self):
        phase = attempt_phase(dated_project('CO'), None, [], AFTER)
        self.assertEqual((phase.key, phase.badge_label), ('missed', 'Not submitted'))

    def test_closed_reads_closed_with_or_without_submission(self):
        for sub, key in ((None, 'missed'), (submission(), 'waiting')):
            phase = attempt_phase(dated_project('CL'), sub, [], BEFORE)
            self.assertEqual((phase.key, phase.badge_label), (key, 'Closed'))

    def test_review_deadline_only_while_peer_reviewing(self):
        for state, expected in (('PR', REVIEW_DUE), ('CO', None), ('CL', None)):
            with self.subTest(state=state):
                phase = attempt_phase(dated_project(state), submission(), reviews(False), REVIEWING)
                self.assertEqual(phase.deadline, expected)

    def test_steps_link_only_to_reachable_pages(self):
        submit = attempt_phase(dated_project(), None, [], BEFORE)
        self.assertEqual([s.url_name for s in submit.steps], ['course_project_submit', '', ''])
        feedback = attempt_phase(dated_project('CO'), submission(), reviews(True), AFTER)
        self.assertEqual([s.state for s in feedback.steps], ['done', 'done', 'current'])
        self.assertEqual(feedback.steps[2].anchor, 'peer-feedback')


class ReviewActionLabelTest(SimpleTestCase):
    def test_action_label_follows_review_progress_and_state(self):
        cases = (
            ('PR', reviews(False, False), 'Start reviews'),
            ('PR', reviews(True, False), 'Continue reviews'),
            ('PR', reviews(True, True), 'View reviews'),
            ('CO', reviews(True, True), 'See feedback'),
        )
        for state, assigned, label in cases:
            with self.subTest(state=state, label=label):
                phase = attempt_phase(dated_project(state), submission('in_review'), assigned, REVIEWING)
                self.assertEqual(phase.action_label, label)


class SelfPacedPhaseTest(SimpleTestCase):
    def test_no_submission_is_submit_without_deadline(self):
        phase = attempt_phase(self_paced_project(), None, [], AFTER)
        self.assertEqual(phase.key, 'submit')
        self.assertIsNone(phase.deadline)

    def test_submitted_without_reviews_waits_and_stays_editable(self):
        phase = attempt_phase(self_paced_project(), submission(), [], AFTER)
        self.assertEqual(phase.key, 'waiting')
        self.assertEqual(phase.action_label, 'Update submission')

    def test_pending_review_is_review_without_deadline(self):
        phase = attempt_phase(self_paced_project(), submission('in_review'), reviews(False, True), AFTER)
        self.assertEqual(phase.key, 'review')
        self.assertIsNone(phase.deadline)

    def test_results_final_only_when_received_reviews_are_complete(self):
        pending = attempt_phase(self_paced_project(), submission('in_review'), reviews(True), AFTER, result(14, True))
        final = attempt_phase(self_paced_project(), submission('review_complete'), reviews(True), AFTER, result(14, True))
        self.assertEqual((pending.key, pending.badge_label), ('feedback', 'Review completed'))
        self.assertEqual(final.badge_label, 'Passed (14)')
