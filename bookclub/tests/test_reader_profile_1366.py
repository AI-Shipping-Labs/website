"""Public reading-profile page, visibility toggle, and helpers (issue #1366).

Covers the ``ReaderProfile`` model default, the ``is_reader_public`` /
``public_reader_ids`` helpers and their query budget, the
``/books/<slug>/readers/<user_id>`` page (participation, visibility, and tier
gating), the owner-only visibility toggle, and the ``Your reading profile``
entry point on book detail.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from bookclub.models import (
    READER_VISIBILITY_PRIVATE,
    READER_VISIBILITY_PUBLIC,
    Book,
    Chapter,
    ChapterRead,
    Note,
    ReaderProfile,
)
from bookclub.profiles import is_reader_public, public_reader_ids
from content.access import LEVEL_MAIN
from payments.models import Tier

User = get_user_model()


class ReaderProfileFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')

        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN,
            status='current', start_date=date(2026, 8, 10),
        )
        cls.chapters = [
            Chapter.objects.create(book=cls.book, number=n, title=f'Ch {n}')
            for n in range(5)
        ]

    @classmethod
    def _member(cls, email, visibility=None, tier=None):
        user = User.objects.create_user(email=email, password='pw')
        user.tier = tier or cls.main_tier
        user.save()
        if visibility is not None:
            ReaderProfile.objects.create(user=user, visibility=visibility)
        return user

    def _url(self, user_id):
        return reverse(
            'bookclub_reader_profile',
            kwargs={'slug': self.book.slug, 'user_id': user_id},
        )


@tag('core')
class ReaderProfileModelTest(ReaderProfileFixture):
    def test_visibility_defaults_to_private(self):
        user = User.objects.create_user(email='default@test.com')
        profile = ReaderProfile.objects.create(user=user)
        self.assertEqual(profile.visibility, READER_VISIBILITY_PRIVATE)
        self.assertFalse(profile.is_public)


@tag('core')
class ReaderProfileHelpersTest(ReaderProfileFixture):
    def test_partition_is_correct_and_cheap(self):
        pub1 = self._member('p1@test.com', READER_VISIBILITY_PUBLIC)
        pub2 = self._member('p2@test.com', READER_VISIBILITY_PUBLIC)
        priv = self._member('priv@test.com', READER_VISIBILITY_PRIVATE)
        norow1 = self._member('n1@test.com')
        norow2 = self._member('n2@test.com')

        ids = [pub1.pk, pub2.pk, priv.pk, norow1.pk, norow2.pk]
        with CaptureQueriesContext(connection) as ctx:
            public = public_reader_ids(ids)
        self.assertEqual(public, {pub1.pk, pub2.pk})
        # A single query regardless of the number of ids.
        self.assertEqual(len(ctx), 1)

        self.assertTrue(is_reader_public(pub1))
        self.assertTrue(is_reader_public(pub2))
        self.assertFalse(is_reader_public(priv))
        self.assertFalse(is_reader_public(norow1))

    def test_anonymous_is_never_public(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(is_reader_public(AnonymousUser()))
        self.assertFalse(is_reader_public(None))

    def test_public_reader_ids_query_is_fixed_regardless_of_size(self):
        small = [self._member(f's{i}@test.com', READER_VISIBILITY_PUBLIC).pk
                 for i in range(2)]
        large = [self._member(f'l{i}@test.com', READER_VISIBILITY_PUBLIC).pk
                 for i in range(20)]
        with CaptureQueriesContext(connection) as small_ctx:
            public_reader_ids(small)
        with CaptureQueriesContext(connection) as large_ctx:
            public_reader_ids(large)
        self.assertEqual(len(small_ctx), len(large_ctx))


class ReaderProfilePageTest(ReaderProfileFixture):
    def test_public_profile_renders_stats_strip_and_notes_for_member(self):
        target = self._member('reader-b@test.com', READER_VISIBILITY_PUBLIC)
        for ch in self.chapters[:3]:
            ChapterRead.objects.create(user=target, chapter=ch)
        Note.objects.create(chapter=self.chapters[0], user=target, body='Note A')
        Note.objects.create(chapter=self.chapters[1], user=target, body='Note B')

        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        response = self.client.get(self._url(target.pk))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'bookclub/reader_profile.html')
        # The visibility badge is owner-only; a non-owner viewer never sees it.
        self.assertNotContains(response, 'reader-visibility-badge')
        self.assertContains(response, 'reader-chapters-read')
        self.assertContains(response, 'reader-notes-shared')
        self.assertContains(response, 'reader-progress-strip')
        self.assertContains(response, 'reader-notes-feed')
        self.assertContains(response, 'Note A')
        self.assertContains(response, 'Note B')
        # Stat values: 3 chapters read, 2 notes shared.
        self.assertContains(
            response, 'data-testid="reader-chapters-read">3</p>',
            html=False,
        )
        # No leaked Django comment markers on the member view.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')

    def test_private_profile_is_404_to_other_members(self):
        target = self._member('hidden@test.com', READER_VISIBILITY_PRIVATE)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(self._url(target.pk)).status_code, 404,
        )

    def test_private_profile_is_404_to_anonymous(self):
        target = self._member('hidden@test.com', READER_VISIBILITY_PRIVATE)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        self.assertEqual(
            self.client.get(self._url(target.pk)).status_code, 404,
        )

    def test_owner_sees_own_private_profile_and_toggle(self):
        owner = self._member('owner@test.com', READER_VISIBILITY_PRIVATE)
        ChapterRead.objects.create(user=owner, chapter=self.chapters[0])
        Note.objects.create(chapter=self.chapters[0], user=owner, body='Mine')
        self.client.force_login(owner)
        response = self.client.get(self._url(owner.pk))
        self.assertEqual(response.status_code, 200)
        # Owner sees their own visibility badge + the toggle.
        self.assertContains(response, 'reader-visibility-badge')
        self.assertContains(response, 'Private')
        self.assertContains(response, 'reader-visibility-form')
        self.assertContains(response, 'reader-make-public')
        self.assertContains(response, 'Mine')
        # No leaked Django comment markers on the owner view.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')

    def test_staff_can_view_any_private_profile(self):
        target = self._member('hidden@test.com', READER_VISIBILITY_PRIVATE)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(self._url(target.pk))
        self.assertEqual(response.status_code, 200)

    def test_owner_with_no_participation_still_reaches_own_profile(self):
        owner = self._member('fresh@test.com', READER_VISIBILITY_PRIVATE)
        self.client.force_login(owner)
        response = self.client.get(self._url(owner.pk))
        self.assertEqual(response.status_code, 200)

    def test_non_participant_is_404(self):
        target = self._member('lurker@test.com', READER_VISIBILITY_PUBLIC)
        # No reads and no notes -> no profile.
        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(self._url(target.pk)).status_code, 404,
        )

    def test_note_only_participant_has_a_profile(self):
        target = self._member('note-only@test.com', READER_VISIBILITY_PUBLIC)
        Note.objects.create(chapter=self.chapters[0], user=target, body='N')
        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(self._url(target.pk)).status_code, 200,
        )

    def test_below_tier_viewer_sees_header_and_gate_no_notes(self):
        target = self._member('reader-b@test.com', READER_VISIBILITY_PUBLIC)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        Note.objects.create(chapter=self.chapters[0], user=target, body='Secret note')
        free = self._member('free@test.com', tier=self.free_tier)
        self.client.force_login(free)
        response = self.client.get(self._url(target.pk))
        self.assertEqual(response.status_code, 200)
        # Below-tier non-owner: header name renders, badge is owner-only.
        self.assertContains(response, target.email.split('@')[0])
        self.assertNotContains(response, 'reader-visibility-badge')
        self.assertContains(response, 'data-testid="book-guest-gate"', count=1)
        self.assertNotContains(response, 'reader-notes-feed')
        self.assertNotContains(response, 'Secret note')
        # BLOCKING regression guard: no leaked {# #} on the gated branch.
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')

    def test_anonymous_viewer_of_public_profile_sees_gate_no_notes(self):
        target = self._member('reader-b@test.com', READER_VISIBILITY_PUBLIC)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        Note.objects.create(chapter=self.chapters[0], user=target, body='Secret note')
        response = self.client.get(self._url(target.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="book-guest-gate"', count=1)
        self.assertNotContains(response, 'Secret note')

    def test_draft_book_is_404_for_non_staff(self):
        self.book.status = 'draft'
        self.book.save()
        target = self._member('reader-b@test.com', READER_VISIBILITY_PUBLIC)
        ChapterRead.objects.create(user=target, chapter=self.chapters[0])
        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        self.assertEqual(
            self.client.get(self._url(target.pk)).status_code, 404,
        )

    def test_unknown_slug_is_404(self):
        viewer = self._member('viewer@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(viewer)
        response = self.client.get('/books/no-such-book/readers/1')
        self.assertEqual(response.status_code, 404)


class ReaderVisibilityToggleTest(ReaderProfileFixture):
    def _toggle_url(self, user_id):
        return reverse(
            'bookclub_reader_visibility',
            kwargs={'slug': self.book.slug, 'user_id': user_id},
        )

    def test_owner_flips_private_to_public(self):
        owner = self._member('owner@test.com', READER_VISIBILITY_PRIVATE)
        ChapterRead.objects.create(user=owner, chapter=self.chapters[0])
        self.client.force_login(owner)
        response = self.client.post(
            self._toggle_url(owner.pk), {'visibility': 'public'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], self._url(owner.pk))
        owner.book_reader_profile.refresh_from_db()
        self.assertEqual(
            owner.book_reader_profile.visibility, READER_VISIBILITY_PUBLIC,
        )

    def test_owner_toggle_creates_row_when_absent(self):
        owner = self._member('owner@test.com')  # no profile row yet
        self.client.force_login(owner)
        self.client.post(self._toggle_url(owner.pk), {'visibility': 'public'})
        self.assertTrue(
            ReaderProfile.objects.filter(
                user=owner, visibility=READER_VISIBILITY_PUBLIC,
            ).exists()
        )

    def test_invalid_value_is_400_and_unchanged(self):
        owner = self._member('owner@test.com', READER_VISIBILITY_PRIVATE)
        self.client.force_login(owner)
        response = self.client.post(
            self._toggle_url(owner.pk), {'visibility': 'secret'},
        )
        self.assertEqual(response.status_code, 400)
        owner.book_reader_profile.refresh_from_db()
        self.assertEqual(
            owner.book_reader_profile.visibility, READER_VISIBILITY_PRIVATE,
        )

    def test_non_owner_cannot_flip_another_users_visibility(self):
        target = self._member('target@test.com', READER_VISIBILITY_PUBLIC)
        attacker = self._member('attacker@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(attacker)
        response = self.client.post(
            self._toggle_url(target.pk), {'visibility': 'private'},
        )
        self.assertEqual(response.status_code, 403)
        target.book_reader_profile.refresh_from_db()
        self.assertEqual(
            target.book_reader_profile.visibility, READER_VISIBILITY_PUBLIC,
        )

    def test_anonymous_toggle_redirects_to_login(self):
        owner = self._member('owner@test.com', READER_VISIBILITY_PRIVATE)
        response = self.client.post(
            self._toggle_url(owner.pk), {'visibility': 'public'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response['Location'])

    def test_below_tier_toggle_is_403(self):
        free = self._member('free@test.com', tier=self.free_tier)
        self.client.force_login(free)
        response = self.client.post(
            self._toggle_url(free.pk), {'visibility': 'public'},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_is_not_allowed(self):
        owner = self._member('owner@test.com', READER_VISIBILITY_PRIVATE)
        self.client.force_login(owner)
        response = self.client.get(self._toggle_url(owner.pk))
        self.assertEqual(response.status_code, 405)


class BookDetailReaderProfileLinkTest(ReaderProfileFixture):
    def test_member_sees_your_reading_profile_link(self):
        member = self._member('member@test.com', READER_VISIBILITY_PUBLIC)
        self.client.force_login(member)
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertContains(response, 'book-reader-profile-link')
        self.assertContains(response, self._url(member.pk))

    def test_non_member_does_not_see_link(self):
        free = self._member('free@test.com', tier=self.free_tier)
        self.client.force_login(free)
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertNotContains(response, 'book-reader-profile-link')

    def test_anonymous_does_not_see_link(self):
        response = self.client.get(f'/books/{self.book.slug}')
        self.assertNotContains(response, 'book-reader-profile-link')
