import json
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from comments.models import Comment, CommentVote
from plans.models import Plan, Sprint

User = get_user_model()


class ListCommentsAPITest(TestCase):
    """Test GET /api/comments/<content_id>."""

    @classmethod
    def setUpTestData(cls):
        cls.user1 = User.objects.create_user(email='u1@test.com', password='pass')
        cls.user2 = User.objects.create_user(email='u2@test.com', password='pass')
        cls.content_id = uuid.uuid4()

    def test_list_empty(self):
        response = self.client.get(f'/api/comments/{self.content_id}')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['comments'], [])

    def test_list_sorted_by_votes_then_date(self):
        """Questions with more votes appear first."""
        c1 = Comment.objects.create(
            content_id=self.content_id, user=self.user1, body='Old with votes',
        )
        c2 = Comment.objects.create(
            content_id=self.content_id, user=self.user2, body='New no votes',
        )
        # Add votes to c1
        CommentVote.objects.create(comment=c1, user=self.user1)
        CommentVote.objects.create(comment=c1, user=self.user2)

        response = self.client.get(f'/api/comments/{self.content_id}')
        data = response.json()
        self.assertEqual(len(data['comments']), 2)
        self.assertEqual(data['comments'][0]['id'], c1.id)
        self.assertEqual(data['comments'][0]['vote_count'], 2)
        self.assertEqual(data['comments'][1]['id'], c2.id)

    def test_list_includes_replies(self):
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user1, body='Q',
        )
        reply = Comment.objects.create(
            content_id=self.content_id, user=self.user2, parent=top, body='R',
        )
        response = self.client.get(f'/api/comments/{self.content_id}')
        data = response.json()
        self.assertEqual(len(data['comments']), 1)  # only top-level
        self.assertEqual(len(data['comments'][0]['replies']), 1)
        self.assertEqual(data['comments'][0]['replies'][0]['id'], reply.id)

    def test_list_uses_canonical_display_name_for_comments_and_replies(self):
        named = User.objects.create_user(
            email='ada@example.com',
            password='pass',
            first_name='Ada',
            last_name='Lovelace',
        )
        whitespace = User.objects.create_user(
            email='reader@example.com',
            password='pass',
            first_name='  ',
            last_name='  ',
        )
        top = Comment.objects.create(
            content_id=self.content_id, user=named, body='Q',
        )
        Comment.objects.create(
            content_id=self.content_id, user=whitespace, parent=top, body='R',
        )

        response = self.client.get(f'/api/comments/{self.content_id}')

        data = response.json()
        self.assertEqual(data['comments'][0]['user_name'], 'Ada Lovelace')
        self.assertEqual(data['comments'][0]['replies'][0]['user_name'], 'reader')

    def test_user_voted_flag(self):
        """The user_voted flag is True when current user has voted."""
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user1, body='Q',
        )
        CommentVote.objects.create(comment=top, user=self.user2)

        self.client.login(email='u2@test.com', password='pass')
        response = self.client.get(f'/api/comments/{self.content_id}')
        data = response.json()
        self.assertTrue(data['comments'][0]['user_voted'])

    def test_user_voted_false_for_anonymous(self):
        top = Comment.objects.create(
            content_id=self.content_id, user=self.user1, body='Q',
        )
        CommentVote.objects.create(comment=top, user=self.user1)

        response = self.client.get(f'/api/comments/{self.content_id}')
        data = response.json()
        self.assertFalse(data['comments'][0]['user_voted'])


class CreateCommentAPITest(TestCase):
    """Test POST /api/comments/<content_id>."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='test@test.com', password='pass')
        cls.content_id = uuid.uuid4()

    def test_anonymous_returns_401(self):
        count_before = Comment.objects.count()
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'question'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Comment.objects.count(), count_before)

    def test_create_comment_success(self):
        self.client.login(email='test@test.com', password='pass')
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'How do I install?'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['body'], 'How do I install?')
        self.assertEqual(data['vote_count'], 0)
        self.assertEqual(data['replies'], [])
        # Verify in DB
        self.assertEqual(Comment.objects.filter(content_id=self.content_id).count(), 1)

    def test_create_comment_uses_canonical_display_name(self):
        User.objects.create_user(
            email='ada@example.com',
            password='pass',
            first_name='Ada',
            last_name='Lovelace',
        )
        self.client.login(email='ada@example.com', password='pass')

        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': 'Hello'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['user_name'], 'Ada Lovelace')

    def test_empty_body_returns_400(self):
        self.client.login(email='test@test.com', password='pass')
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': '  '}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


@tag('core')
class CreateCommentActivationAPITest(TestCase):
    def setUp(self):
        self.content_id = uuid.uuid4()
        self.user = User.objects.create_user(
            email='comment-activation@test.com',
            password='pass',
            account_activated=False,
        )

    def test_success_activates_user_and_preserves_payload_shape(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_endpoint', kwargs={'content_id': self.content_id}),
            data=json.dumps({'body': '  How do I ship this?  '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(
            set(data),
            {
                'id',
                'body',
                'user_name',
                'created_at',
                'vote_count',
                'user_voted',
                'replies',
            },
        )
        self.assertEqual(data['body'], 'How do I ship this?')
        self.assertEqual(data['vote_count'], 0)
        self.assertFalse(data['user_voted'])
        self.assertEqual(data['replies'], [])
        self.assertTrue(data['created_at'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)

    def test_invalid_json_does_not_activate_user(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_endpoint', kwargs={'content_id': self.content_id}),
            data='not json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid JSON'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)

    def test_blank_body_does_not_activate_user(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_endpoint', kwargs={'content_id': self.content_id}),
            data=json.dumps({'body': '   '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Body is required'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)

    def test_plan_permission_failure_does_not_activate_user(self):
        owner = User.objects.create_user(
            email='comment-plan-owner@test.com',
            password='pass',
        )
        sprint = Sprint.objects.create(
            name='Comment Activation Sprint',
            slug='comment-activation-sprint',
            start_date=date(2026, 6, 1),
        )
        plan = Plan.objects.create(
            member=owner,
            sprint=sprint,
            visibility='cohort',
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                'comments_endpoint',
                kwargs={'content_id': plan.comment_content_id},
            ),
            data=json.dumps({'body': 'Forbidden'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {'error': 'Not allowed'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)


class ReplyAPITest(TestCase):
    """Test POST /api/comments/<comment_id>/reply."""

    @classmethod
    def setUpTestData(cls):
        cls.user1 = User.objects.create_user(email='u1@test.com', password='pass')
        cls.user2 = User.objects.create_user(email='u2@test.com', password='pass')
        cls.content_id = uuid.uuid4()
        cls.top_comment = Comment.objects.create(
            content_id=cls.content_id, user=cls.user1, body='Question',
        )

    def test_anonymous_returns_401(self):
        count_before = Comment.objects.count()
        response = self.client.post(
            f'/api/comments/{self.top_comment.id}/reply',
            data=json.dumps({'body': 'reply'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Comment.objects.count(), count_before)

    def test_reply_success(self):
        self.client.login(email='u2@test.com', password='pass')
        response = self.client.post(
            f'/api/comments/{self.top_comment.id}/reply',
            data=json.dumps({'body': 'pip install -r requirements.txt'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['body'], 'pip install -r requirements.txt')

    def test_reply_uses_email_local_part_display_name_fallback(self):
        User.objects.create_user(email='reader@example.com', password='pass')
        self.client.login(email='reader@example.com', password='pass')

        response = self.client.post(
            f'/api/comments/{self.top_comment.id}/reply',
            data=json.dumps({'body': 'fallback'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['user_name'], 'reader')

    def test_reply_to_reply_returns_400(self):
        reply = Comment.objects.create(
            content_id=self.content_id, user=self.user2,
            parent=self.top_comment, body='A reply',
        )
        self.client.login(email='u1@test.com', password='pass')
        count_before = Comment.objects.count()
        response = self.client.post(
            f'/api/comments/{reply.id}/reply',
            data=json.dumps({'body': 'nested reply attempt'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.objects.count(), count_before)

    def test_reply_to_nonexistent_returns_404(self):
        self.client.login(email='u1@test.com', password='pass')
        response = self.client.post(
            '/api/comments/99999/reply',
            data=json.dumps({'body': 'nope'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)


@tag('core')
class ReplyActivationAPITest(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            email='reply-parent@test.com',
            password='pass',
        )
        self.user = User.objects.create_user(
            email='reply-activation@test.com',
            password='pass',
            account_activated=False,
        )
        self.parent = Comment.objects.create(
            content_id=uuid.uuid4(),
            user=self.author,
            body='Parent comment',
        )

    def test_success_activates_user_and_preserves_payload_shape(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': self.parent.pk}),
            data=json.dumps({'body': '  Reply body  '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(set(data), {'id', 'body', 'user_name', 'created_at'})
        self.assertEqual(data['body'], 'Reply body')
        self.assertTrue(data['created_at'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.account_activated)

    def test_missing_parent_does_not_activate_user(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': 99999}),
            data=json.dumps({'body': 'No parent'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {'error': 'Comment not found'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)

    def test_nested_reply_does_not_activate_user(self):
        reply = Comment.objects.create(
            content_id=self.parent.content_id,
            user=self.author,
            parent=self.parent,
            body='Existing reply',
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': reply.pk}),
            data=json.dumps({'body': 'Nested reply'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Cannot reply to a reply'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)

    def test_invalid_json_does_not_activate_user(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': self.parent.pk}),
            data='not json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid JSON'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)

    def test_blank_body_does_not_activate_user(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('comments_reply', kwargs={'comment_id': self.parent.pk}),
            data=json.dumps({'body': '   '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Body is required'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.account_activated)


class VoteAPITest(TestCase):
    """Test POST /api/comments/<comment_id>/vote."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='voter@test.com', password='pass')
        cls.content_id = uuid.uuid4()
        cls.top_comment = Comment.objects.create(
            content_id=cls.content_id, user=cls.user, body='Question',
        )

    def test_anonymous_returns_401(self):
        vote_count_before = CommentVote.objects.count()
        response = self.client.post(f'/api/comments/{self.top_comment.id}/vote')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(CommentVote.objects.count(), vote_count_before)

    def test_vote_toggle_on(self):
        self.client.login(email='voter@test.com', password='pass')
        response = self.client.post(f'/api/comments/{self.top_comment.id}/vote')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['voted'])
        self.assertEqual(data['vote_count'], 1)

    def test_vote_toggle_off(self):
        self.client.login(email='voter@test.com', password='pass')
        # Vote on
        self.client.post(f'/api/comments/{self.top_comment.id}/vote')
        # Vote off
        response = self.client.post(f'/api/comments/{self.top_comment.id}/vote')
        data = response.json()
        self.assertFalse(data['voted'])
        self.assertEqual(data['vote_count'], 0)

    def test_vote_on_reply_returns_400(self):
        reply = Comment.objects.create(
            content_id=self.content_id, user=self.user,
            parent=self.top_comment, body='A reply',
        )
        self.client.login(email='voter@test.com', password='pass')
        vote_count_before = CommentVote.objects.count()
        response = self.client.post(f'/api/comments/{reply.id}/vote')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CommentVote.objects.count(), vote_count_before)

    def test_vote_on_nonexistent_returns_404(self):
        self.client.login(email='voter@test.com', password='pass')
        response = self.client.post('/api/comments/99999/vote')
        self.assertEqual(response.status_code, 404)
