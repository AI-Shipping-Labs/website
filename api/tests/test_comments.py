"""Operator comments API contracts for issue #1592."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http import HTTPStatus
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import MemberAPIKey, Token
from api.views.comments import COMMENTS_LIST_SCHEMA
from bookclub.models import Book, Chapter, Note
from comments.models import ApiReplyOperation, Comment
from content.models import Course, Module, Unit, Workshop, WorkshopPage
from plans.models import Plan, Sprint

User = get_user_model()


class OperatorCommentsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='comments-staff@test.com',
            password='pw',
            is_staff=True,
            first_name='Staff',
        )
        cls.member = User.objects.create_user(
            email='comments-member@test.com',
            password='pw',
            first_name='Learner',
        )
        cls.token, cls.plaintext = Token.create_for_user(
            user=cls.staff, name='comments automation',
        )

        cls.course = Course.objects.create(
            content_id=uuid.uuid4(), title='AI Hero', slug='aihero',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Day 1', slug='day-1', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            content_id=uuid.uuid4(), module=cls.module,
            title='Frontmatter', slug='frontmatter', sort_order=1,
        )
        cls.workshop = Workshop.objects.create(
            content_id=uuid.uuid4(), title='Agents', slug='agents',
            date=date(2026, 9, 8), status='published',
            landing_required_level=0, pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            content_id=uuid.uuid4(), workshop=cls.workshop,
            title='Setup', slug='setup', sort_order=1,
        )
        cls.sprint = Sprint.objects.create(
            name='September Sprint', slug='september-sprint',
            start_date=date(2026, 9, 1), status='active',
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
            title='Ship the assistant', visibility='private',
        )
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Jane Builder', required_level=0, status='current',
        )
        cls.chapter = Chapter.objects.create(
            book=cls.book, number=1, title='Batching',
        )
        cls.note = Note.objects.create(
            chapter=cls.chapter, user=cls.member, body='My notes',
        )

        cls.top_comments = [
            Comment.objects.create(
                content_id=cls.unit.content_id, user=cls.member,
                body='Course question',
            ),
            Comment.objects.create(
                content_id=cls.page.content_id, user=cls.member,
                body='Workshop question',
            ),
            Comment.objects.create(
                content_id=cls.plan.comment_content_id, user=cls.member,
                body='Plan question',
            ),
            Comment.objects.create(
                content_id=cls.note.comment_content_id, user=cls.member,
                body='Book question',
            ),
            Comment.objects.create(
                content_id=uuid.uuid4(), user=cls.member,
                body='Orphan question',
            ),
        ]
        cls.reply = Comment.objects.create(
            content_id=cls.unit.content_id,
            user=cls.staff,
            parent=cls.top_comments[0],
            body='Existing answer',
        )

    def auth(self, token=None):
        return {'HTTP_AUTHORIZATION': f'Token {token or self.plaintext}'}

    def post_reply(self, comment_id, body='Answer', key='run-1', **extra):
        headers = self.auth()
        headers['HTTP_IDEMPOTENCY_KEY'] = key
        headers.update(extra)
        return self.client.post(
            f'/api/comments/{comment_id}/replies',
            data=json.dumps({'body': body}),
            content_type='application/json',
            **headers,
        )

    def test_list_resolves_all_supported_types_unknown_rows_and_flat_replies(self):
        response = self.client.get('/api/comments', **self.auth())
        payload = response.json()
        self.assertEqual(payload['count'], 6)
        self.assertEqual(payload['limit'], 50)
        self.assertEqual(payload['offset'], 0)
        self.assertEqual(
            {row['content_type'] for row in payload['comments']},
            {
                'course_unit', 'workshop_page', 'sprint_plan',
                'book_club_note', 'unknown',
            },
        )
        self.assertEqual(
            [row['id'] for row in payload['comments']],
            sorted((row['id'] for row in payload['comments']), reverse=True),
        )
        by_type = {row['content_type']: row for row in payload['comments']}
        self.assertIsNone(by_type['unknown']['context'])
        self.assertEqual(by_type['course_unit']['context']['unit_slug'], 'frontmatter')
        self.assertEqual(by_type['workshop_page']['context']['workshop_key'], 'agents')
        self.assertEqual(by_type['sprint_plan']['context']['plan_id'], self.plan.pk)
        self.assertEqual(by_type['book_club_note']['context']['note_id'], self.note.pk)
        reply = next(row for row in payload['comments'] if row['id'] == self.reply.pk)
        self.assertEqual(reply['kind'], 'reply')
        self.assertEqual(reply['thread_root_id'], self.top_comments[0].pk)
        course_top = next(
            row for row in payload['comments'] if row['id'] == self.top_comments[0].pk
        )
        self.assertEqual(course_top['reply_count'], 1)

    def test_list_query_bound_does_not_grow_with_page_rows(self):
        with CaptureQueriesContext(connection) as first_queries:
            first = self.client.get('/api/comments?limit=1', **self.auth())
        with CaptureQueriesContext(connection) as many_queries:
            many = self.client.get('/api/comments?limit=50', **self.auth())
        self.assertEqual(first.json()['limit'], 1)
        self.assertEqual(many.json()['limit'], 50)
        self.assertEqual(len(first_queries), len(many_queries))

    def test_tied_timestamps_keep_offset_pages_deterministic(self):
        content_id = uuid.uuid4()
        comments = [
            Comment.objects.create(
                content_id=content_id,
                user=self.member,
                body=f'Tied comment {index}',
            )
            for index in range(3)
        ]
        tied_at = timezone.now()
        Comment.objects.filter(pk__in=[comment.pk for comment in comments]).update(
            created_at=tied_at,
        )

        first_page = self.client.get(
            f'/api/comments?content_id={content_id}&limit=2&offset=0',
            **self.auth(),
        ).json()
        second_page = self.client.get(
            f'/api/comments?content_id={content_id}&limit=2&offset=2',
            **self.auth(),
        ).json()

        expected_ids = [comment.pk for comment in reversed(comments)]
        self.assertEqual(first_page['count'], 3)
        self.assertEqual(first_page['offset'], 0)
        self.assertEqual(second_page['offset'], 2)
        self.assertEqual(
            [row['id'] for row in first_page['comments']],
            expected_ids[:2],
        )
        self.assertEqual(
            [row['id'] for row in second_page['comments']],
            expected_ids[2:],
        )

    def test_course_and_unanswered_filters_combine_with_and(self):
        Comment.objects.create(
            content_id=self.unit.content_id, user=self.member, body='Unanswered',
        )
        response = self.client.get(
            '/api/comments',
            {
                'course_slug': 'aihero',
                'module_slug': 'day-1',
                'unit_slug': 'frontmatter',
                'unanswered': 'true',
            },
            **self.auth(),
        )
        rows = response.json()['comments']
        self.assertEqual([row['body'] for row in rows], ['Unanswered'])
        self.assertTrue(all(row['kind'] == 'top_level' for row in rows))

    def test_filters_reject_invalid_and_contradictory_values(self):
        cases = [
            ('limit=0', 'limit'),
            ('offset=-1', 'offset'),
            ('content_id=nope', 'content_id'),
            ('content_type=external', 'content_type'),
            ('since=2026-09-08T10:00:00', 'since'),
            ('since=2026-09-09T00:00:00Z&until=2026-09-08T00:00:00Z', 'since'),
            ('unanswered=yes', 'unanswered'),
            ('unanswered=true&kind=reply', 'unanswered'),
            ('module_slug=day-1', 'module_slug'),
            ('course_slug=aihero&workshop_key=agents', 'content_type'),
            ('content_type=sprint_plan&course_slug=aihero', 'content_type'),
        ]
        for query, field in cases:
            with self.subTest(query=query):
                response = self.client.get(f'/api/comments?{query}', **self.auth())
                self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
                self.assertEqual(response.json()['code'], 'validation_error')
                self.assertEqual(response.json()['details']['field'], field)

    def test_empty_or_unknown_valid_filters_do_not_expose_other_rows(self):
        for query in ('course_slug=', 'author_email=', 'plan_id=999999'):
            with self.subTest(query=query):
                response = self.client.get(f'/api/comments?{query}', **self.auth())
                self.assertEqual(response.json()['comments'], [])

    def test_authentication_matrix_rejects_non_operator_callers(self):
        member_key, member_plaintext = MemberAPIKey.create_for_user(
            user=self.member, name='member comments',
        )
        self.assertIsNotNone(member_key.pk)
        requests = [
            {},
            self.auth('invalid'),
            self.auth(member_plaintext),
        ]
        self.client.force_login(self.staff)
        requests.append({})
        for headers in requests:
            with self.subTest(headers=bool(headers)):
                response = self.client.get('/api/comments', **headers)
                self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)
        self.client.logout()

    @patch('comments.services.mark_activated')
    @patch('comments.services._notify_comment_recipients')
    def test_reply_uses_shared_service_and_replay_has_no_side_effects(
        self, notify, mark_activated,
    ):
        parent = self.top_comments[0]
        body = '  <img src=x onerror=alert(1)> **answer**  '
        created = self.post_reply(parent.pk, body=body, key='safe-run-1')
        replayed = self.post_reply(parent.pk, body=body, key='safe-run-1')

        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        self.assertFalse(created.json()['idempotent_replay'])
        self.assertTrue(replayed.json()['idempotent_replay'])
        self.assertEqual(created.json()['id'], replayed.json()['id'])
        self.assertEqual(created.json()['body'], '<img src=x onerror=alert(1)> **answer**')
        self.assertEqual(
            Comment.objects.filter(parent=parent, body__contains='**answer**').count(),
            1,
        )
        self.assertEqual(ApiReplyOperation.objects.count(), 1)
        self.assertEqual(notify.call_count, 1)
        mark_activated.assert_called_once_with(self.staff)

    def test_idempotency_key_conflict_and_different_tokens_are_independent(self):
        parent = self.top_comments[0]
        self.post_reply(parent.pk, body='First answer', key='shared-key')
        conflict = self.post_reply(parent.pk, body='Changed answer', key='shared-key')
        self.assertEqual(conflict.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(conflict.json()['code'], 'idempotency_key_reused')

        other_staff = User.objects.create_user(
            email='other-comments-staff@test.com', is_staff=True,
        )
        _token, plaintext = Token.create_for_user(user=other_staff, name='other')
        response = self.client.post(
            f'/api/comments/{parent.pk}/replies',
            data=json.dumps({'body': 'Other operator'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {plaintext}',
            HTTP_IDEMPOTENCY_KEY='shared-key',
        )
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertEqual(ApiReplyOperation.objects.count(), 2)

    def test_reply_validation_and_thread_gates_have_no_side_effects(self):
        initial_comments = Comment.objects.count()
        cases = [
            (self.reply.pk, {'body': 'nested'}, 'nested', HTTPStatus.UNPROCESSABLE_ENTITY, 'invalid_parent'),
            (999999, {'body': 'missing'}, 'missing', HTTPStatus.NOT_FOUND, 'comment_not_found'),
            (self.top_comments[4].pk, {'body': 'orphan'}, 'orphan', HTTPStatus.NOT_FOUND, 'thread_not_found'),
            (self.top_comments[0].pk, {}, 'missing-body', HTTPStatus.UNPROCESSABLE_ENTITY, 'validation_error'),
            (self.top_comments[0].pk, {'body': 12}, 'type', HTTPStatus.UNPROCESSABLE_ENTITY, 'validation_error'),
            (self.top_comments[0].pk, {'body': '   '}, 'blank', HTTPStatus.UNPROCESSABLE_ENTITY, 'validation_error'),
            (self.top_comments[0].pk, {'body': 'x' * 10001}, 'long', HTTPStatus.UNPROCESSABLE_ENTITY, 'validation_error'),
            (self.top_comments[0].pk, {'body': 'ok', 'user_id': self.member.pk}, 'override', HTTPStatus.UNPROCESSABLE_ENTITY, 'validation_error'),
        ]
        for comment_id, payload, key, status, code in cases:
            with self.subTest(key=key):
                response = self.client.post(
                    f'/api/comments/{comment_id}/replies',
                    data=json.dumps(payload),
                    content_type='application/json',
                    HTTP_AUTHORIZATION=f'Token {self.plaintext}',
                    HTTP_IDEMPOTENCY_KEY=key,
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['code'], code)
        missing_key = self.client.post(
            f'/api/comments/{self.top_comments[0].pk}/replies',
            data=json.dumps({'body': 'ok'}),
            content_type='application/json',
            **self.auth(),
        )
        self.assertEqual(missing_key.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)
        self.assertEqual(missing_key.json()['code'], 'idempotency_key_required')
        self.assertEqual(Comment.objects.count(), initial_comments)
        self.assertEqual(ApiReplyOperation.objects.count(), 0)

    @patch('api.views.comments._can_write_thread', return_value=False)
    def test_forbidden_thread_creates_no_side_effects(self, _can_write):
        response = self.post_reply(self.top_comments[0].pk, key='forbidden')
        self.assertEqual(response.status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(response.json()['code'], 'comment_reply_forbidden')
        self.assertFalse(ApiReplyOperation.objects.exists())

    def test_operation_is_immutable_non_secret_and_survives_token_lifecycle(self):
        created = self.post_reply(self.top_comments[0].pk, key='audit-key')
        self.assertEqual(created.status_code, HTTPStatus.CREATED)
        operation = ApiReplyOperation.objects.get()
        self.assertEqual(operation.token_identity, self.token.pk)
        self.assertEqual(operation.token_name_snapshot, 'comments automation')
        self.assertEqual(operation.actor_id_snapshot, self.staff.pk)
        self.assertNotIn(self.plaintext, operation.token_prefix_snapshot)
        self.assertNotIn(self.plaintext, operation.request_digest)

        operation.token_name_snapshot = 'changed'
        with self.assertRaises(ValidationError):
            operation.save()
        with self.assertRaises(ValidationError):
            operation.delete()

        self.token.rotate_key()
        self.token.delete()
        operation.refresh_from_db()
        self.assertEqual(operation.actor_id, self.staff.pk)
        self.assertEqual(operation.resulting_comment_id, created.json()['id'])

    def test_generated_openapi_documents_collection_reply_and_required_header(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        spec = build_spec(urlpatterns)
        collection = spec['paths']['/api/comments']['get']
        reply = spec['paths']['/api/comments/{comment_id}/replies']['post']
        self.assertEqual(
            collection['responses']['200']['content']['application/json']['schema'],
            COMMENTS_LIST_SCHEMA,
        )
        key = next(
            item for item in reply['parameters']
            if item['name'] == 'Idempotency-Key'
        )
        self.assertEqual(key['in'], 'header')
        self.assertTrue(key['required'])
        self.assertIn('body', reply['requestBody']['content']['application/json']['schema']['required'])
        for status in ('200', '201'):
            schema = reply['responses'][status]['content']['application/json']['schema']
            self.assertIn('idempotent_replay', schema['required'])


@tag('core', 'postgresql')
class OperatorCommentReplyConcurrencyTest(TransactionTestCase):
    """Production row locks converge identical operator reply requests."""

    def test_concurrent_identical_requests_create_side_effects_once(self):
        if connection.vendor != 'postgresql':
            self.skipTest('operator reply concurrency requires PostgreSQL row locking')

        staff = User.objects.create_user(
            email='concurrent-comments-staff@test.com',
            is_staff=True,
        )
        member = User.objects.create_user(email='concurrent-comments-member@test.com')
        token, plaintext = Token.create_for_user(
            user=staff,
            name='concurrent comments',
        )
        course = Course.objects.create(
            content_id=uuid.uuid4(),
            title='Concurrent Course',
            slug='concurrent-course',
            status='published',
            required_level=0,
        )
        module = Module.objects.create(
            course=course,
            title='Concurrency',
            slug='concurrency',
            sort_order=1,
        )
        unit = Unit.objects.create(
            content_id=uuid.uuid4(),
            module=module,
            title='Locks',
            slug='locks',
            sort_order=1,
        )
        parent = Comment.objects.create(
            content_id=unit.content_id,
            user=member,
            body='Can two operators race?',
        )
        barrier = threading.Barrier(2)

        def reply():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                response = Client().post(
                    f'/api/comments/{parent.pk}/replies',
                    data=json.dumps({'body': 'One durable answer'}),
                    content_type='application/json',
                    HTTP_AUTHORIZATION=f'Token {plaintext}',
                    HTTP_IDEMPOTENCY_KEY='concurrent-run',
                )
                return response.status_code, response.json()
            finally:
                connection.close()

        with (
            patch('comments.services.mark_activated') as mark_activated,
            patch('comments.services._notify_comment_recipients') as notify,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(pool.map(lambda _: reply(), range(2)))

        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(
            sorted(body['idempotent_replay'] for _, body in results),
            [False, True],
        )
        self.assertEqual(len({body['id'] for _, body in results}), 1)
        self.assertEqual(
            Comment.objects.filter(parent=parent, body='One durable answer').count(),
            1,
        )
        self.assertEqual(ApiReplyOperation.objects.count(), 1)
        self.assertEqual(mark_activated.call_count, 1)
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(ApiReplyOperation.objects.get().token_identity, token.pk)
