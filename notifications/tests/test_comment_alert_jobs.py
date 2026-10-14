"""Transaction and worker contracts for queued staff comment alerts."""

import json
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, tag

from comments.models import Comment
from comments.services import create_comment
from notifications.services.staff_comment_alerts import (
    COMMENT_ALERT_TASK_PATH,
    deliver_staff_comment_alert,
    enqueue_staff_comment_alert,
)

User = get_user_model()


@tag("core")
class CommentAlertDispatchTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="queued-comment@test.com",
            password="pw",
        )

    def _create_comment(self, body="Queued comment"):
        return create_comment(
            content_id=uuid.uuid4(),
            user=self.member,
            body=body,
        )

    def test_alert_waits_for_commit_before_enqueue(self):
        with (
            mock.patch(
                "notifications.services.staff_comment_alerts.enqueue_staff_comment_alert",
            ) as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            with transaction.atomic():
                comment = self._create_comment()
                enqueue.assert_not_called()

        enqueue.assert_called_once_with(comment.pk)

    def test_rolled_back_comment_is_not_enqueued(self):
        class RollBackComment(Exception):
            pass

        with (
            mock.patch(
                "notifications.services.staff_comment_alerts.enqueue_staff_comment_alert",
            ) as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            with self.assertRaises(RollBackComment), transaction.atomic():
                comment = self._create_comment()
                enqueue.assert_not_called()
                raise RollBackComment

        enqueue.assert_not_called()
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_queue_failure_does_not_undo_saved_comment(self):
        with (
            mock.patch(
                "notifications.services.staff_comment_alerts.enqueue_staff_comment_alert",
                side_effect=RuntimeError("queue unavailable"),
            ),
            self.assertLogs("comments.services", level="ERROR") as logs,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                comment = self._create_comment(body="Saved despite queue")

        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())
        self.assertIn(
            f"staff comment Slack alert enqueue failed for comment {comment.pk}",
            "\n".join(logs.output),
        )

    def test_web_request_only_enqueues_and_never_calls_slack(self):
        content_id = uuid.uuid4()
        self.client.force_login(self.member)

        with (
            mock.patch(
                "notifications.services.staff_comment_alerts.enqueue_staff_comment_alert",
            ) as enqueue,
            mock.patch(
                "notifications.services.staff_slack.requests.post",
            ) as slack_post,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/api/comments/{content_id}",
                    data=json.dumps({"body": "Return without Slack"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 201)
                enqueue.assert_not_called()
                slack_post.assert_not_called()

            enqueue.assert_called_once_with(response.json()["id"])
            slack_post.assert_not_called()


@tag("core")
class CommentAlertJobTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent_author = User.objects.create_user(
            email="parent-comment@test.com",
            password="pw",
        )
        cls.reply_author = User.objects.create_user(
            email="reply-comment@test.com",
            password="pw",
        )
        cls.parent = Comment.objects.create(
            content_id=uuid.uuid4(),
            user=cls.parent_author,
            body="Parent",
        )
        cls.reply = Comment.objects.create(
            content_id=cls.parent.content_id,
            user=cls.reply_author,
            parent=cls.parent,
            body="Reply",
        )

    @mock.patch("notifications.services.staff_comment_alerts.async_task")
    def test_enqueue_passes_only_scalar_id_and_descriptive_name(self, async_task):
        async_task.return_value = "task-123"

        result = enqueue_staff_comment_alert(self.reply.pk)

        self.assertEqual(result, "task-123")
        self.assertEqual(
            async_task.call_args.args,
            (COMMENT_ALERT_TASK_PATH, self.reply.pk),
        )
        self.assertEqual(
            async_task.call_args.kwargs,
            {
                "task_name": (f"Staff comment alert: comment {self.reply.pk} from comment creation"),
            },
        )

    @mock.patch(
        "notifications.services.staff_comment_alerts.notify_staff_of_comment",
    )
    def test_worker_reloads_authors_and_delegates(self, notify):
        notify.return_value = True

        result = deliver_staff_comment_alert(self.reply.pk)

        self.assertIs(result, True)
        delivered = notify.call_args.args[0]
        self.assertEqual(delivered.pk, self.reply.pk)
        self.assertIn("user", delivered._state.fields_cache)
        self.assertIn("parent", delivered._state.fields_cache)
        self.assertIn("user", delivered.parent._state.fields_cache)

    @mock.patch(
        "notifications.services.staff_comment_alerts.notify_staff_of_comment",
    )
    def test_worker_safely_skips_missing_comment(self, notify):
        missing_id = self.reply.pk + 1000

        with self.assertLogs(
            "notifications.services.staff_comment_alerts",
            level="INFO",
        ) as logs:
            result = deliver_staff_comment_alert(missing_id)

        self.assertIs(result, False)
        notify.assert_not_called()
        self.assertIn(
            f"comment {missing_id} no longer exists",
            "\n".join(logs.output),
        )
