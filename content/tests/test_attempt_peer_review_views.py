"""CMP-style peer review pages for a dated project attempt."""

import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course, PeerReview, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject
from content.services.peer_pseudonym import peer_pseudonym

CRITERIA = [
    {'id': 'readme', 'title': 'Problem description', 'description': 'README quality.', 'type': 'scale', 'options': [
        {'points': 0, 'text': 'Not described'}, {'points': 2, 'text': 'Clearly described'},
    ]},
    {'id': 'tests', 'title': 'Testing', 'description': '', 'type': 'scale', 'options': [
        {'points': 0, 'text': 'No tests'}, {'points': 1, 'text': 'Unit tests'},
    ]},
    {'id': 'extras', 'title': 'Additional bonus points', 'description': '', 'type': 'checklist', 'options': [
        {'points': 1, 'text': 'There is a UI'}, {'points': 2, 'text': 'Deployed to the cloud'},
    ]},
]
BASE = '/courses/peer-cmp/projects/attempt-1/reviews'


def _checked_values(response, name):
    html = response.content.decode()
    tags = re.findall(r'<input[^>]*name="' + re.escape(name) + r'"[^>]*>', html, flags=re.S)
    return [re.search(r'value="(\d+)"', tag).group(1) for tag in tags if re.search(r'\schecked\b', tag)]


class AttemptPeerReviewViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.learner = users.create_user(email='learner@example.com', password='pw')
        cls.peers = [users.create_user(email=f'peer{n}@example.com', password='pw') for n in range(2)]
        cls.course = Course.objects.create(
            title='CMP course', slug='peer-cmp', status='published', required_level=0,
            peer_review_enabled=True,
        )
        cls.course.project_passing_score = 3
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 1', external_key='1',
            start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
        )
        now = timezone.now()
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, slug='attempt-1', title='Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=1), review_due_at=now + timedelta(days=6),
            evaluation_criteria=CRITERIA,
        )
        cls.own = cls._submission(cls.learner, 'own')
        cls.assigned = []
        for number, peer in enumerate(cls.peers):
            peer_submission = cls._submission(peer, f'peer-{number}')
            cls.assigned.append(PeerReview.objects.create(submission=peer_submission, reviewer=cls.learner))
        cls.received = [
            PeerReview.objects.create(
                submission=cls.own, reviewer=cls.peers[0], is_complete=True,
                criteria_scores={'readme': 1, 'tests': 1, 'extras': [1]}, feedback='Great README, add tests.',
            ),
            PeerReview.objects.create(
                submission=cls.own, reviewer=cls.peers[1], is_complete=True,
                criteria_scores={'readme': 1, 'tests': 0, 'extras': []}, feedback='',
            ),
        ]

    @classmethod
    def _submission(cls, user, name):
        CohortEnrollment.objects.create(cohort=cls.cohort, user=user)
        return ProjectSubmission.objects.create(
            user=user, course=cls.course, cohort=cls.cohort, course_project=cls.project,
            project_url=f'https://github.com/example/{name}', commit_id='9e03d5b1c2d3e4f5',
            status='in_review',
        )

    def setUp(self):
        self.client.force_login(self.learner)

    def _form_url(self, index=0):
        return f'{BASE}/{self.assigned[index].submission_id}'

    def _valid_post(self, **extra):
        return {'criterion-readme': '0', 'criterion-tests': '1', 'criterion-extras': ['1'], **extra}

    def test_list_names_peers_by_pseudonym_with_status(self):
        self.assigned[0].is_complete = True
        self.assigned[0].criteria_scores = {'readme': 1, 'tests': 0}
        self.assigned[0].save(update_fields=['is_complete', 'criteria_scores'])
        response = self.client.get(BASE)
        rows = re.findall(r'data-testid="peer-evaluation-row">(.*?)</a>', response.content.decode(), flags=re.S)
        self.assertEqual(len(rows), 2)
        first_name = peer_pseudonym(self.peers[0].pk, self.course.pk)
        self.assertIn(f"{first_name}'s submission", rows[0])
        self.assertIn('Reviewed', rows[0])
        self.assertIn('To review', rows[1])
        self.assertNotContains(response, 'peer0@example.com')
        self.assertContains(response, 'Complete all 2 reviews to be eligible for your certificate.')
        self.assertContains(response, '1 of 2 complete')
        self.assertContains(response, 'data-testid="peer-evaluations"')
        self.assertContains(response, 'Reviews due')
        self.assertContains(response, 'data-testid="project-title">Peer reviews</h1>')
        self.assertNotContains(response, 'Attempt 1 · Reviews due')
        self.assertNotContains(response, 'data-testid="attempt-phases"')
        self.assertRegex(response.content.decode(), r'href="' + re.escape(self._form_url(1)) + r'"[^>]*data-testid="peer-review-continue"')
        self.assertNotContains(response, 'Peers see you as')

    def test_form_renders_numbered_radios_and_bonus_checkboxes(self):
        response = self.client.get(self._form_url())
        html = response.content.decode()
        self.assertIn('<legend class="text-lg font-semibold text-foreground">1. Problem description</legend>', html)
        self.assertIn('<legend class="text-lg font-semibold text-foreground">3. Additional bonus points</legend>', html)
        self.assertEqual(len(re.findall(r'type="radio" name="criterion-readme"', html)), 2)
        self.assertEqual(len(re.findall(r'type="checkbox" name="criterion-extras"', html)), 2)
        self.assertEqual(len(re.findall(r'<label class="homework-option-label">\s*<input', html)), 6)
        self.assertRegex(html, r'Clearly described <span[^>]*data-testid="review-option-points">\(2 points\)</span>')
        self.assertEqual(html.count('data-testid="review-criterion-bonus"'), 1)
        self.assertNotIn('review-criteria-bonus', html)

    def test_form_keeps_bonus_right_after_its_parent(self):
        criteria = [CRITERIA[0], CRITERIA[2], CRITERIA[1]]
        CourseProject.objects.filter(pk=self.project.pk).update(evaluation_criteria=criteria)
        html = self.client.get(self._form_url()).content.decode()
        legends = re.findall(r'<legend[^>]*>(.*?)</legend>', html)
        self.assertEqual(legends, ['1. Problem description', '2. Additional bonus points', '3. Testing'])
        self.assertRegex(html, r'There is a UI <span[^>]*data-testid="review-option-points">\(1 point\)</span>')
        self.assertRegex(html, r'href="https://github.com/example/peer-0" target="_blank"')
        commit = re.search(r'<a href="([^"]+)" target="_blank" rel="noopener noreferrer"[^>]*data-testid="peer-review-commit">(\w+)</a>', html)
        self.assertEqual(commit.groups(), ('https://github.com/example/peer-0/commit/9e03d5b1c2d3e4f5', '9e03d5b'))
        self.assertIn(f"Evaluate {peer_pseudonym(self.peers[0].pk, self.course.pk)}'s submission", html)
        line = re.search(r'data-testid="peer-review-repo-line">(.*?)</p>', html, flags=re.S).group(1)
        self.assertIn('data-testid="peer-review-github-link"', line)
        self.assertIn('data-testid="peer-review-commit"', line)
        self.assertNotIn('Commit hash', html)

    def test_repo_line_without_commit_shows_only_the_repository(self):
        ProjectSubmission.objects.filter(pk=self.assigned[0].submission_id).update(commit_id='')
        html = self.client.get(self._form_url()).content.decode()
        line = re.search(r'data-testid="peer-review-repo-line">(.*?)</p>', html, flags=re.S).group(1)
        self.assertIn('href="https://github.com/example/peer-0"', line)
        self.assertNotIn('data-testid="peer-review-commit"', line)

    def test_missing_scale_criterion_returns_400_and_keeps_selections(self):
        response = self.client.post(self._form_url(), {
            'criterion-readme': '1', 'criterion-extras': ['0', '1'], 'feedback': 'Nice work',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Missing: Testing.', status_code=400)
        self.assertContains(response, 'data-testid="review-criterion-error"', count=1, status_code=400)
        self.assertEqual(_checked_values(response, 'criterion-readme'), ['1'])
        self.assertEqual(_checked_values(response, 'criterion-extras'), ['0', '1'])
        self.assertContains(response, 'Nice work</textarea>', status_code=400)
        self.assertFalse(PeerReview.objects.get(pk=self.assigned[0].pk).is_complete)

    def test_submit_with_missing_criteria_keeps_draft_and_lists_missing(self):
        response = self.client.post(self._form_url(), {
            'criterion-readme': '1', 'feedback': 'x',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Missing: Testing.', status_code=400)
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertFalse(review.is_complete)
        self.assertIsNone(review.completed_at)
        self.assertEqual(review.criteria_scores, {'readme': 1, 'extras': []})

    def _draft(self, data, index=0):
        return self.client.post(self._form_url(index), {'action': 'draft', **data})

    def test_draft_autosave_keeps_partial_answers_preselected_and_not_done(self):
        response = self._draft({'criterion-readme': '1', 'feedback': 'wip'})
        self.assertEqual(response.json(), {'saved': True, 'submitted': False})
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertFalse(review.is_complete)
        self.assertIsNone(review.completed_at)
        page = self.client.get(self._form_url())
        self.assertEqual(_checked_values(page, 'criterion-readme'), ['1'])
        self.assertContains(page, 'wip</textarea>')
        self.assertNotContains(page, 'data-testid="review-criterion-error"')
        self.assertNotContains(page, 'data-testid="peer-review-saved-status"')
        reviews_page = self.client.get(BASE)
        self.assertContains(reviews_page, '0 of 2 complete')
        self.assertNotContains(reviews_page, 'Reviewed')

    def test_draft_autosave_never_completes_even_with_all_criteria(self):
        self._draft(self._valid_post())
        self.assertFalse(PeerReview.objects.get(pk=self.assigned[0].pk).is_complete)

    def test_draft_autosave_does_not_touch_a_submitted_review(self):
        self.client.post(self._form_url(), self._valid_post(feedback='final'))
        response = self._draft({'criterion-readme': '1'})
        self.assertEqual(response.json(), {'saved': False, 'submitted': True})
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertTrue(review.is_complete)
        self.assertEqual(review.criteria_scores, {'readme': 0, 'tests': 1, 'extras': [1]})
        self.assertEqual(review.feedback, 'final')

    def test_draft_autosave_requires_assignment(self):
        outsider = get_user_model().objects.create_user(email='out@example.com', password='pw')
        CohortEnrollment.objects.create(cohort=self.cohort, user=outsider)
        self.client.force_login(outsider)
        self.assertEqual(self._draft({'criterion-readme': '1'}).status_code, 403)
        self.assertEqual(PeerReview.objects.get(pk=self.assigned[0].pk).criteria_scores, {})

    def test_draft_autosave_rejected_once_project_is_completed(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        self.assertEqual(self._draft({'criterion-readme': '1'}).status_code, 403)
        self.assertEqual(PeerReview.objects.get(pk=self.assigned[0].pk).criteria_scores, {})

    def test_submit_with_missing_criteria_leaves_a_submitted_review_unchanged(self):
        self.client.post(self._form_url(), self._valid_post())
        response = self.client.post(self._form_url(), {'criterion-readme': '1'})
        self.assertEqual(response.status_code, 400)
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertTrue(review.is_complete)
        self.assertEqual(review.criteria_scores['readme'], 0)

    def test_submit_with_all_criteria_completes_and_continues(self):
        response = self.client.post(self._form_url(), self._valid_post())
        self.assertRedirects(response, self._form_url(1), fetch_redirect_response=False)
        self.assertTrue(PeerReview.objects.get(pk=self.assigned[0].pk).is_complete)

    def test_stale_complete_row_with_missing_criteria_is_not_shown_as_done(self):
        PeerReview.objects.filter(pk=self.assigned[0].pk).update(
            is_complete=True, criteria_scores={'readme': 1},
        )
        response = self.client.get(self._form_url())
        self.assertNotContains(response, 'peer-review-saved-status')
        reviews_page = self.client.get(BASE)
        self.assertContains(reviews_page, '0 of 2 complete')
        self.assertNotContains(reviews_page, 'Reviewed')
        self.assertTrue(PeerReview.objects.get(pk=self.assigned[0].pk).is_complete)

    def test_complete_row_with_all_scale_criteria_is_shown_as_done(self):
        PeerReview.objects.filter(pk=self.assigned[0].pk).update(
            is_complete=True, criteria_scores={'readme': 1, 'tests': 0},
        )
        reviews_page = self.client.get(BASE)
        self.assertContains(reviews_page, '1 of 2 complete')

    def test_valid_post_with_empty_comment_completes_review_and_moves_to_next_pending(self):
        response = self.client.post(self._form_url(), self._valid_post(feedback=''))
        self.assertRedirects(response, self._form_url(1), fetch_redirect_response=False)
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertTrue(review.is_complete)
        self.assertEqual(review.criteria_scores, {'readme': 0, 'tests': 1, 'extras': [1]})
        self.assertIsNone(review.score)

    def test_saving_the_last_pending_review_returns_to_the_list(self):
        self.assigned[0].is_complete = True
        self.assigned[0].criteria_scores = {'readme': 1, 'tests': 0}
        self.assigned[0].save(update_fields=['is_complete', 'criteria_scores'])
        page = self.client.get(self._form_url(1))
        self.assertContains(page, 'data-testid="peer-review-submit">Submit</button>')
        self.assertContains(page, f'href="{self._form_url(0)}"')
        response = self.client.post(self._form_url(1), self._valid_post())
        self.assertRedirects(response, BASE, fetch_redirect_response=False)

    def test_first_review_offers_save_and_submit_without_previous(self):
        page = self.client.get(self._form_url(0))
        self.assertNotContains(page, 'data-testid="peer-review-save"')
        self.assertContains(page, 'data-testid="peer-review-submit">Submit</button>')
        self.assertNotContains(page, 'data-testid="peer-review-previous"')

    def test_saved_review_can_be_updated_while_window_is_open(self):
        self.client.post(self._form_url(), self._valid_post(feedback='First note'))
        page = self.client.get(self._form_url())
        self.assertContains(page, 'data-testid="peer-review-saved-status"')
        self.assertEqual(_checked_values(page, 'criterion-tests'), ['1'])
        self.client.post(self._form_url(), self._valid_post(**{'criterion-readme': '1', 'feedback': 'Second note'}))
        review = PeerReview.objects.get(pk=self.assigned[0].pk)
        self.assertTrue(review.is_complete)
        self.assertEqual((review.criteria_scores['readme'], review.feedback), (1, 'Second note'))

    def test_closed_notice_replaces_certificate_banner_on_review_page(self):
        page = self.client.get(self._form_url())
        self.assertContains(page, 'data-testid="peer-review-certificate-requirement"')
        self.assertNotContains(page, 'data-testid="peer-review-form-closed"')
        self.client.post(self._form_url(), self._valid_post())
        self.assertNotContains(self.client.get(self._form_url()), 'peer-review-form-closed')
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        for index in (0, 1):
            html = self.client.get(self._form_url(index)).content.decode()
            self.assertEqual(html.count('The peer review form is closed.'), 1)
            self.assertIn('data-testid="peer-review-form-closed"', html)
            self.assertNotIn('peer-review-certificate-requirement', html)
            self.assertNotIn('Complete all', html)
            self.assertNotIn('Your review (submitted)', html)

    def test_unassigned_submission_is_forbidden(self):
        stranger = get_user_model().objects.create_user(email='stranger@example.com', password='pw')
        self.client.force_login(stranger)
        CohortEnrollment.objects.create(cohort=self.cohort, user=stranger)
        response = self.client.post(self._form_url(), self._valid_post())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PeerReview.objects.filter(reviewer=stranger).exists())

    def test_all_reviews_done_but_not_completed_shows_no_results(self):
        for review in self.assigned:
            review.is_complete = True
            review.criteria_scores = {'readme': 1, 'tests': 0}
            review.save(update_fields=['is_complete', 'criteria_scores'])
        CourseProject.objects.filter(pk=self.project.pk).update(
            review_due_at=timezone.now() - timedelta(days=2),
        )
        html = self.client.get(BASE).content.decode()
        self.assertIn('data-testid="peer-review-completed-notice"', html)
        self.assertNotIn('data-testid="peer-results"', html)
        self.assertNotIn('peer-project-score', html)
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        self.assertIn('data-testid="peer-results"', self.client.get(BASE).content.decode())
        CourseProject.objects.filter(pk=self.project.pk).update(state='PR')
        self.assertNotIn('data-testid="peer-results"', self.client.get(BASE).content.decode())

    def test_feedback_phase_shows_score_outcome_and_anonymous_comments(self):
        for review in self.assigned:
            review.is_complete = True
            review.criteria_scores = {'readme': 1, 'tests': 0}
            review.save(update_fields=['is_complete', 'criteria_scores'])
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        response = self.client.get(BASE)
        html = response.content.decode()
        self.assertIn('data-testid="peer-project-score">4</span>', html)
        self.assertNotIn('peer-review-certificate-requirement', html)
        self.assertNotIn('data-testid="peer-evaluations"', html)
        self.assertNotIn('data-testid="peer-evaluation-row"', html)
        self.assertIn('Your results', html)
        self.assertNotIn('data-testid="peer-your-submission"', html)
        self.assertLess(html.index('Peer feedback'), html.index('Criteria breakdown'))
        self.assertIn('data-testid="project-title">Feedback</h1>', html)
        self.assertIn('<title>Feedback:', html)
        self.assertNotIn('data-testid="peer-review-meta"', html)
        self.assertIn('data-testid="peer-project-outcome">Passed</span>', html)
        feedback = re.search(r'data-testid="peer-feedback-list">(.*?)</ul>', html, flags=re.S).group(1)
        self.assertIn('Reviewer 1', feedback)
        self.assertIn('Great README, add tests.', feedback)
        self.assertNotIn('Reviewer 2', feedback)
        self.assertNotIn('peer0@example.com', html)
        self.assertNotIn(peer_pseudonym(self.peers[0].pk, self.course.pk), feedback)
        self.assertNotIn('Each criterion scores the median', html)
        self.assertNotIn('peer-results-default', html)
        self.assertEqual(re.findall(r'data-criterion="([a-z-]+)">', html), ['readme', 'tests', 'extras'])
        self.assertNotIn('data-testid="peer-result-bonus"', html)
        self.assertNotIn('of up to', html)
        readme = re.search(r'data-criterion="readme">(.*?)</li>\s*</ul>\s*</details>', html, flags=re.S).group(1)
        self.assertIn('data-testid="peer-result-criterion-choice">Clearly described</p>', readme)
        self.assertIn('<summary>2 votes</summary>', readme)
        self.assertNotIn('<details class="peer-result-votes" data-testid="peer-result-votes" open', html)

    def test_completed_project_with_missed_reviews_explains_failure(self):
        self.assigned[0].is_complete = True
        self.assigned[0].criteria_scores = {'readme': 1, 'tests': 0}
        self.assigned[0].save(update_fields=['is_complete', 'criteria_scores'])
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(BASE).content.decode()
        self.assertIn('data-testid="peer-project-outcome">Failed</span>', html)
        self.assertNotIn('Failed (4)', html.split('data-testid="peer-results"', 1)[1])
        card = re.search(r'data-testid="peer-score-card">(.*?)</ul>', html, flags=re.S).group(1)
        self.assertIn(
            'You completed 1 of 2 assigned reviews. '
            'To pass, you need at least 3 points and all assigned reviews completed.', card,
        )
        self.assertNotIn('Passing score:', card)
        for testid in ('peer-review-certificate-requirement', 'peer-evaluations',
                       'peer-evaluation-row', 'peer-review-form-closed', 'peer-review-continue',
                       'peer-review-meta'):
            self.assertNotIn(f'data-testid="{testid}"', html)
        self.assertNotIn('Peer review was due', html)
        self.assertNotIn('data-timezone-notice-text', html)

    def test_points_only_failure_names_the_passing_score(self):
        for review in self.assigned:
            review.is_complete = True
            review.criteria_scores = {'readme': 0, 'tests': 0}
            review.save(update_fields=['is_complete', 'criteria_scores'])
        PeerReview.objects.filter(submission=self.own).update(criteria_scores={'readme': 0, 'tests': 0, 'extras': []})
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(BASE).content.decode()
        self.assertIn('data-testid="peer-results-fail-reason">To pass, you need at least 3 points.</p>', html)

    def test_open_review_keeps_pending_labels_and_banner(self):
        html = self.client.get(BASE).content.decode()
        self.assertIn('peer-review-certificate-requirement', html)
        self.assertIn('To review', html)
        self.assertIn('data-testid="peer-evaluations"', html)
        self.assertIn('data-testid="peer-review-meta"', html)
        self.assertIn('data-timezone-notice-text', html)

    def test_results_without_received_reviews_show_defaults_and_no_votes(self):
        PeerReview.objects.filter(submission=self.own).delete()
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(BASE).content.decode()
        self.assertIn('data-testid="peer-project-score">4</span>', html)
        self.assertIn('No peer reviews received yet, scores shown are defaults.', html)
        self.assertNotIn('peer-result-votes', html)
        self.assertNotIn(' vote', html.split('data-testid="peer-results"', 1)[1])
        self.assertNotIn('peer-result-criterion-choice', html)

    def test_pending_list_has_one_progress_statement_and_plain_row_titles(self):
        html = self.client.get(BASE).content.decode()
        self.assertEqual(html.count('data-testid="peer-review-progress"'), 1)
        self.assertNotIn('evaluations done', html)
        self.assertNotIn('Evaluate:', html)

    def test_open_form_is_titled_evaluate_with_instructions(self):
        html = self.client.get(self._form_url()).content.decode()
        author = peer_pseudonym(self.peers[0].pk, self.course.pk)
        self.assertIn(f"Evaluate {author}'s submission</h1>", html)
        self.assertIn('Evaluate the submission on each criterion.', html)
        self.assertIn('Reviews due', html)

    def test_closed_form_is_a_read_only_review_without_deadline(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(self._form_url()).content.decode()
        author = peer_pseudonym(self.peers[0].pk, self.course.pk)
        self.assertIn(f"Review of {author}'s submission</h1>", html)
        self.assertNotIn('Evaluate', html.split('data-testid="project-title"', 1)[1].split('</main>', 1)[0])
        self.assertNotIn('Reviews due', html)
        self.assertIn('You did not complete this review.', html)
        self.assertIn('data-testid="peer-review-next"', html)

    def test_closed_form_for_completed_review_has_no_missed_note(self):
        self.client.post(self._form_url(), self._valid_post())
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(self._form_url()).content.decode()
        self.assertIn('data-testid="peer-review-form-closed"', html)
        self.assertNotIn('You did not complete this review.', html)

    def test_completed_reviews_page_says_peer_review_closed_without_date(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='CO')
        html = self.client.get(BASE).content.decode()
        self.assertNotIn('Reviews due', html)
        self.assertNotIn('data-timezone-notice-text', html)
        self.assertNotIn('Peer review was due', html)

    def test_bonus_description_replaces_generic_bonus_line(self):
        criteria = [dict(CRITERIA[0]), dict(CRITERIA[1]), {**CRITERIA[2], 'description': 'Extra effort.'}]
        CourseProject.objects.filter(pk=self.project.pk).update(evaluation_criteria=criteria)
        html = self.client.get(self._form_url()).content.decode()
        self.assertIn('Extra effort.', html)
        self.assertNotIn('Tick all that apply.', html)
        self.assertEqual(html.count('data-testid="review-criterion-bonus"'), 1)


class AttemptWithoutCriteriaTest(TestCase):
    def test_comment_only_review_records_no_score(self):
        users = get_user_model().objects
        learner = users.create_user(email='learner@example.com', password='pw')
        author = users.create_user(email='author@example.com', password='pw')
        course = Course.objects.create(
            title='Plain', slug='plain', status='published', required_level=0, peer_review_enabled=True,
        )
        now = timezone.now()
        project = CourseProject.objects.create(
            course=course, slug='attempt-1', title='Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=1), review_due_at=now + timedelta(days=6),
        )
        submission = ProjectSubmission.objects.create(
            user=author, course=course, course_project=project, project_url='https://github.com/a/b',
        )
        review = PeerReview.objects.create(submission=submission, reviewer=learner)
        self.client.force_login(learner)
        url = f'/courses/plain/projects/attempt-1/reviews/{submission.pk}'
        page = self.client.get(url)
        self.assertNotContains(page, 'name="criterion-')
        self.assertContains(page, 'name="feedback"')
        response = self.client.post(url, {'feedback': 'Looks solid'})
        self.assertRedirects(response, '/courses/plain/projects/attempt-1/reviews', fetch_redirect_response=False)
        review.refresh_from_db()
        self.assertEqual((review.is_complete, review.score, review.feedback), (True, None, 'Looks solid'))
