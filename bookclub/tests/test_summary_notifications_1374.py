"""Book Club summary-publish notifications (issue #1374).

Covers the shared publish-transition trigger fired from both the Studio and
admin API publish surfaces: the in-app bell fan-out (one per book-access
member), the correct title/body/URL per surface, no re-fire on re-save /
notes-pull, re-fire on unpublish -> republish, below-tier / anon exclusion,
Studio self-notify exclusion (API has no acting user), the draft-book silence,
the email opt-out audience rules, and the best-effort no-rollback contract.

Dates derive from ``timezone`` helpers so nothing date-rots.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Token
from bookclub.models import Book, Chapter, Note
from bookclub.summaries import append_notes_digest
from bookclub.summary_notifications import (
    _email_eligible_users,
    is_new_publish,
    notify_summary_published,
)
from content.access import LEVEL_MAIN
from notifications.models import Notification
from payments.models import Tier

User = get_user_model()


def _count(user, *, title_contains=None):
    qs = Notification.objects.filter(
        user=user, notification_type='bookclub_summary',
    )
    if title_contains is not None:
        qs = qs.filter(title__contains=title_contains)
    return qs.count()


@tag("core")
class IsNewPublishPredicateTest(TestCase):
    def test_transition_detection(self):
        ts = timezone.now()
        # null -> set is the publish transition.
        self.assertTrue(is_new_publish(None, ts))
        # A re-save keeps the same timestamp: no transition.
        self.assertFalse(is_new_publish(ts, ts))
        # Unpublish (set -> null) is not a publish.
        self.assertFalse(is_new_publish(ts, None))
        # Still-unpublished: no transition.
        self.assertFalse(is_new_publish(None, None))


class SummaryNotificationsFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')
        cls.premium_tier = Tier.objects.get(slug='premium')

        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
        )
        cls.ch0 = Chapter.objects.create(book=cls.book, number=0, title='Inference')
        cls.ch1 = Chapter.objects.create(book=cls.book, number=1, title='Prereqs')

        # Two book-access members (Main tier clears the Main gate).
        cls.member = cls._member('main@test.com', cls.main_tier)
        cls.member2 = cls._member('main2@test.com', cls.main_tier)
        # A below-tier (Free) member: never in the audience.
        cls.free_user = cls._member('free@test.com', cls.free_tier)

        # Acting staff user is tier-eligible (Premium clears Main) so the
        # self-notify exclusion is actually exercised, not trivially true.
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.staff.tier = cls.premium_tier
        cls.staff.save()
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')

    @classmethod
    def _member(cls, email, tier):
        user = User.objects.create_user(email=email, password='pw')
        user.tier = tier
        user.save()
        return user

    def _api_patch(self, path, payload):
        return self.client.patch(
            path, data=json.dumps(payload), content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )

    def _studio_edit_chapter(self, chapter, *, summary, publish):
        return self.client.post(
            reverse('studio_book_chapter_edit', kwargs={
                'book_id': self.book.pk, 'chapter_id': chapter.pk,
            }),
            data={
                'number': chapter.number,
                'title': chapter.title,
                'summary': summary,
                **({'summary_published': 'on'} if publish else {}),
            },
        )


@tag("core")
class ChapterPublishStudioTest(SummaryNotificationsFixture):
    def test_studio_chapter_publish_fans_out_one_bell_per_member(self):
        self.client.force_login(self.staff)
        resp = self._studio_edit_chapter(
            self.ch0, summary='The KV cache framing clicked.', publish=True,
        )
        self.assertEqual(resp.status_code, 302)

        # Each book-access member gets exactly one, correctly titled + linked.
        for member in (self.member, self.member2):
            self.assertEqual(_count(member), 1)
            note = Notification.objects.get(
                user=member, notification_type='bookclub_summary',
            )
            self.assertEqual(
                note.title,
                'New chapter summary: Inference Engineering (Ch. 0)',
            )
            self.assertEqual(note.body, 'The KV cache framing clicked.')
            self.assertEqual(
                note.url, '/books/inference-engineering/chapters/0#summary',
            )

        # Below-tier and acting staff get nothing.
        self.assertEqual(_count(self.free_user), 0)
        self.assertEqual(_count(self.staff), 0)

    def test_body_fallback_when_excerpt_empty_is_impossible_but_guarded(self):
        # Publishing requires a non-empty body, so the excerpt is always set;
        # this asserts the excerpt (not the fallback) is used on the happy path.
        self.client.force_login(self.staff)
        self._studio_edit_chapter(
            self.ch0, summary='First para.\n\nSecond para.', publish=True,
        )
        note = Notification.objects.get(
            user=self.member, notification_type='bookclub_summary',
        )
        self.assertEqual(note.body, 'First para.')

    def test_resave_still_published_does_not_refire(self):
        self.client.force_login(self.staff)
        self._studio_edit_chapter(
            self.ch0, summary='Original body.', publish=True,
        )
        self.assertEqual(_count(self.member), 1)
        # Re-submit the form with an edited body, still published.
        self._studio_edit_chapter(
            self.ch0, summary='Edited body, still published.', publish=True,
        )
        self.assertEqual(_count(self.member), 1)

    def test_notes_pull_after_publish_does_not_refire(self):
        self.client.force_login(self.staff)
        self._studio_edit_chapter(
            self.ch0, summary='Published body.', publish=True,
        )
        self.assertEqual(_count(self.member), 1)
        # A member note exists; pulling notes edits the draft, never the
        # publish timestamp, so it must not re-notify.
        Note.objects.create(
            chapter=self.ch0, user=self.member, body='A member note.',
        )
        append_notes_digest(self.ch0)
        self.assertEqual(_count(self.member), 1)

    def test_unpublish_then_republish_refires(self):
        self.client.force_login(self.staff)
        self._studio_edit_chapter(
            self.ch0, summary='Body v1.', publish=True,
        )
        self.assertEqual(_count(self.member), 1)
        # Unpublish (no re-notify) then republish (re-notify).
        self._studio_edit_chapter(self.ch0, summary='Body v1.', publish=False)
        self.assertEqual(_count(self.member), 1)
        self._studio_edit_chapter(self.ch0, summary='Body v2.', publish=True)
        self.assertEqual(_count(self.member), 2)

    def test_draft_book_publish_is_silent(self):
        self.book.status = 'draft'
        self.book.save(update_fields=['status'])
        self.client.force_login(self.staff)
        self._studio_edit_chapter(
            self.ch0, summary='Draft-preview body.', publish=True,
        )
        self.assertEqual(_count(self.member), 0)
        self.assertEqual(_count(self.member2), 0)


@tag("core")
class BookPublishApiTest(SummaryNotificationsFixture):
    def test_api_book_publish_fans_out_to_summary_page(self):
        resp = self._api_patch(
            '/api/books/inference-engineering',
            {'summary': 'The group treated the book as a system.',
             'summary_published': True},
        )
        self.assertEqual(resp.status_code, 200)

        for member in (self.member, self.member2):
            self.assertEqual(_count(member), 1)
            note = Notification.objects.get(
                user=member, notification_type='bookclub_summary',
            )
            self.assertEqual(
                note.title, 'Book summary published: Inference Engineering',
            )
            self.assertEqual(note.url, '/books/inference-engineering/summary')

        self.assertEqual(_count(self.free_user), 0)

    def test_api_path_does_not_exclude_acting_staff(self):
        # The admin-token API has no acting user, so the whole tier-eligible
        # audience is notified -- including a tier-eligible staff account.
        self._api_patch(
            '/api/books/inference-engineering',
            {'summary': 'Overall.', 'summary_published': True},
        )
        self.assertEqual(_count(self.staff), 1)

    def test_api_chapter_publish_idempotent_re_patch(self):
        path = '/api/books/inference-engineering/chapters/0'
        payload = {'summary': 'Chapter takeaway.', 'summary_published': True}
        self.assertEqual(self._api_patch(path, payload).status_code, 200)
        self.assertEqual(_count(self.member), 1)
        # Idempotent re-save: no additional notification.
        self.assertEqual(self._api_patch(path, payload).status_code, 200)
        self.assertEqual(_count(self.member), 1)


@tag("core")
class EmailAudienceTest(SummaryNotificationsFixture):
    def test_email_audience_applies_every_exclusion(self):
        # opted_in: verified, subscribed, key absent -> included.
        opted_in = self.member
        opted_in.email_verified = True
        opted_in.save(update_fields=['email_verified'])

        # explicit opt-out via bookclub_emails=False -> excluded.
        opted_out = self.member2
        opted_out.email_verified = True
        opted_out.email_preferences = {'bookclub_emails': False}
        opted_out.save(update_fields=['email_verified', 'email_preferences'])

        # unsubscribed (also represents a permanent SES bounce, #766) -> excluded.
        unsubscribed = self._member('unsub@test.com', self.main_tier)
        unsubscribed.email_verified = True
        unsubscribed.unsubscribed = True
        unsubscribed.save(update_fields=['email_verified', 'unsubscribed'])

        # unverified -> excluded.
        unverified = self._member('unverified@test.com', self.main_tier)
        unverified.email_verified = False
        unverified.save(update_fields=['email_verified'])

        # below-tier verified -> excluded (not in the base audience).
        free_verified = self.free_user
        free_verified.email_verified = True
        free_verified.save(update_fields=['email_verified'])

        eligible = set(
            _email_eligible_users(LEVEL_MAIN).values_list('email', flat=True)
        )
        self.assertIn('main@test.com', eligible)
        self.assertNotIn('main2@test.com', eligible)
        self.assertNotIn('unsub@test.com', eligible)
        self.assertNotIn('unverified@test.com', eligible)
        self.assertNotIn('free@test.com', eligible)

    def test_key_absent_is_opted_in(self):
        self.member.email_verified = True
        self.member.email_preferences = {}
        self.member.save(update_fields=['email_verified', 'email_preferences'])
        eligible = set(
            _email_eligible_users(LEVEL_MAIN).values_list('email', flat=True)
        )
        self.assertIn('main@test.com', eligible)

    def test_publish_sends_email_only_to_opted_in_verified_members(self):
        self.member.email_verified = True
        self.member.save(update_fields=['email_verified'])
        # member2 stays unverified -> no email.
        with patch(
            'email_app.services.email_service.EmailService.send',
        ) as mock_send:
            mock_send.return_value = object()  # non-None -> counted as sent
            notify_summary_published(self.ch0)
        sent_emails = {
            call.args[0].email for call in mock_send.call_args_list
        }
        self.assertEqual(sent_emails, {'main@test.com'})
        # The email used the chapter template.
        templates = {call.args[1] for call in mock_send.call_args_list}
        self.assertEqual(templates, {'bookclub_chapter_summary'})

    def test_studio_publish_excludes_acting_staff_from_email(self):
        # Staff is tier-eligible + verified; the Studio path must not email them.
        self.staff.email_verified = True
        self.staff.save(update_fields=['email_verified'])
        self.member.email_verified = True
        self.member.save(update_fields=['email_verified'])
        self.client.force_login(self.staff)
        with patch(
            'email_app.services.email_service.EmailService.send',
        ) as mock_send:
            mock_send.return_value = object()
            self._studio_edit_chapter(
                self.ch0, summary='Body.', publish=True,
            )
        recipients = {call.args[0].email for call in mock_send.call_args_list}
        self.assertNotIn('staff@test.com', recipients)
        self.assertIn('main@test.com', recipients)


@tag("core")
class BestEffortContractTest(SummaryNotificationsFixture):
    def test_email_failure_does_not_rollback_bell_or_5xx(self):
        # A member that would receive an email so the send is actually attempted.
        self.member.email_verified = True
        self.member.save(update_fields=['email_verified'])

        with patch(
            'email_app.services.email_service.EmailService.send',
            side_effect=RuntimeError('SES down'),
        ):
            resp = self._api_patch(
                '/api/books/inference-engineering/chapters/0',
                {'summary': 'Body text.', 'summary_published': True},
            )
        # PATCH still succeeds and persists the publish.
        self.assertEqual(resp.status_code, 200)
        self.ch0.refresh_from_db()
        self.assertIsNotNone(self.ch0.summary_published_at)
        # Bell rows persisted despite the email failure.
        self.assertEqual(_count(self.member), 1)
        self.assertEqual(_count(self.member2), 1)

    def test_notify_failure_does_not_break_publish(self):
        # If the whole notify path raises, the API PATCH must still return 200.
        with patch(
            'api.views.books.notify_summary_published',
            side_effect=RuntimeError('boom'),
        ):
            resp = self._api_patch(
                '/api/books/inference-engineering',
                {'summary': 'Overall body.', 'summary_published': True},
            )
        self.assertEqual(resp.status_code, 200)
        self.book.refresh_from_db()
        self.assertIsNotNone(self.book.summary_published_at)
