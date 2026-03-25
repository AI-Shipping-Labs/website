import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from comments.models import Comment, CommentVote

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

    def test_empty_body_returns_400(self):
        self.client.login(email='test@test.com', password='pass')
        response = self.client.post(
            f'/api/comments/{self.content_id}',
            data=json.dumps({'body': '  '}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)


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
