"""Issue #1591: ``sprint_partner_intro`` greets on ``member_name``.

The greeting must degrade to ``Hi there,`` for a member with no name, while
the partner rows inside the body keep their identifying ``display_name``
label -- a card-style label, where the email handle is the right fallback.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.services.email_service import EmailService
from plans.models import Plan, Sprint, SprintEnrollment
from plans.services import assign_accountability_partners
from plans.services.partner_intro_emails import (
    _audience_data,
    _build_rows,
    _email_context,
)

User = get_user_model()


class PartnerIntroGreetingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        # The member has no name at all; the partner has none either, so the
        # partner label has to fall back to the handle to stay identifying.
        cls.member = cls._enrolled('nameless.member@test.com')
        cls.partner = cls._enrolled('p.artner@test.com')
        assign_accountability_partners(
            sprint=cls.sprint,
            member=cls.member,
            partner=cls.partner,
            assigned_by=cls.staff,
        )

    @classmethod
    def _enrolled(cls, email, **fields):
        user = User.objects.create_user(email=email, password='pw', **fields)
        SprintEnrollment.objects.create(
            sprint=cls.sprint, user=user, enrolled_by=cls.staff,
        )
        Plan.objects.create(member=user, sprint=cls.sprint)
        return user

    def _context_for(self, member):
        data = _audience_data(self.sprint)
        rows = _build_rows(self.sprint, data)
        row = next(r for r in rows if r['member_id'] == member.pk)
        return row, _email_context(sprint=self.sprint, member=member, row=row)

    def test_nameless_member_greeting_is_hi_there(self):
        _row, context = self._context_for(self.member)
        self.assertEqual(context['member_name'], 'there')

    def test_named_member_greeting_keeps_their_name(self):
        self.member.first_name = 'Ada'
        self.member.last_name = 'Lovelace'
        self.member.save(update_fields=['first_name', 'last_name'])

        _row, context = self._context_for(self.member)

        self.assertEqual(context['member_name'], 'Ada Lovelace')

    def test_operator_row_and_partner_label_keep_the_handle_fallback(self):
        """``_row_identity`` and ``_partner_identity`` are deliberately not
        converted: they are labels, not greetings."""
        row, context = self._context_for(self.member)

        self.assertEqual(row['member_name'], 'nameless.member')
        self.assertEqual(
            [partner['name'] for partner in context['partners']],
            ['p.artner'],
        )

    def test_rendered_body_greets_hi_there_and_still_lists_the_partner(self):
        _row, context = self._context_for(self.member)

        _subject, body = EmailService()._render_template(
            'sprint_partner_intro', self.member, context,
        )

        self.assertIn('Hi there,', body)
        self.assertNotIn('Hi nameless.member,', body)
        self.assertNotIn('Hi ,', body)
        self.assertIn('p.artner', body)
        self.assertIn('p.artner@test.com', body)
