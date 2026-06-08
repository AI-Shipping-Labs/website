"""Unit tests for the SES explanation helper module (issue #849).

Covers severity classification (incl. the unknown-event fallback), the
bounce_type/subtype glossary (hits + misses), and the diagnostic-code decoder
(hits, misses, multi-code extraction + de-dup). These are pure-Python lookups,
so no DB is touched.
"""

from django.test import SimpleTestCase, tag

from accounts.utils.bounce import SOFT_BOUNCE_THRESHOLD
from email_app import ses_explain
from email_app.models.ses_event import SesEvent


@tag('core')
class SeverityClassificationTests(SimpleTestCase):
    def test_permanent_bounce_and_complaint_are_high(self):
        for event_type in (
            SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            SesEvent.EVENT_TYPE_COMPLAINT,
        ):
            self.assertEqual(
                ses_explain.severity_for_event_type(event_type),
                ses_explain.SEVERITY_HIGH,
            )

    def test_transient_and_other_bounce_are_medium(self):
        for event_type in (
            SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
            SesEvent.EVENT_TYPE_BOUNCE_OTHER,
        ):
            self.assertEqual(
                ses_explain.severity_for_event_type(event_type),
                ses_explain.SEVERITY_MEDIUM,
            )

    def test_non_bounce_events_are_info(self):
        for event_type in (
            SesEvent.EVENT_TYPE_DELIVERY,
            SesEvent.EVENT_TYPE_OPEN,
            SesEvent.EVENT_TYPE_CLICK,
            SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION,
            SesEvent.EVENT_TYPE_UNSUBSCRIBE_CONFIRMATION,
            SesEvent.EVENT_TYPE_OTHER,
        ):
            self.assertEqual(
                ses_explain.severity_for_event_type(event_type),
                ses_explain.SEVERITY_INFO,
            )

    def test_unknown_event_type_falls_back_to_info_without_raising(self):
        self.assertEqual(
            ses_explain.severity_for_event_type('some_future_type'),
            ses_explain.SEVERITY_INFO,
        )
        self.assertEqual(
            ses_explain.severity_for_event_type(''),
            ses_explain.SEVERITY_INFO,
        )
        self.assertEqual(
            ses_explain.severity_for_event_type(None),
            ses_explain.SEVERITY_INFO,
        )

    def test_labels(self):
        self.assertEqual(
            ses_explain.severity_label(SesEvent.EVENT_TYPE_BOUNCE_PERMANENT),
            'Serious',
        )
        self.assertEqual(
            ses_explain.severity_label(SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT),
            'Temporary',
        )
        self.assertEqual(
            ses_explain.severity_label(SesEvent.EVENT_TYPE_DELIVERY),
            'Informational',
        )

    def test_pill_classes_reuse_exact_existing_palette(self):
        # Must match EVENT_TYPE_PILL_CLASSES in studio/views/ses_events.py.
        self.assertEqual(
            ses_explain.severity_classes(SesEvent.EVENT_TYPE_BOUNCE_PERMANENT),
            'bg-red-500/20 text-red-400',
        )
        self.assertEqual(
            ses_explain.severity_classes(SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT),
            'bg-amber-500/20 text-amber-300',
        )
        self.assertEqual(
            ses_explain.severity_classes(SesEvent.EVENT_TYPE_DELIVERY),
            'bg-secondary text-muted-foreground',
        )

    def test_high_consequence_mentions_unsubscribe(self):
        text = ses_explain.severity_consequence(
            SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
        )
        self.assertIn('unsubscribed', text.lower())

    def test_medium_consequence_mentions_retry_and_threshold(self):
        text = ses_explain.severity_consequence(
            SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
        )
        self.assertIn('retries', text.lower())
        self.assertIn('threshold', text.lower())

    def test_info_consequence_has_no_problem(self):
        text = ses_explain.severity_consequence(SesEvent.EVENT_TYPE_DELIVERY)
        self.assertIn('no deliverability problem', text.lower())


@tag('core')
class ConsequenceNoteTests(SimpleTestCase):
    def test_threshold_sourced_from_constant_not_hardcoded(self):
        # The note must name the same number the webhook enforces.
        for event_type in (
            SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
            SesEvent.EVENT_TYPE_DELIVERY,
        ):
            self.assertIn(
                str(SOFT_BOUNCE_THRESHOLD),
                ses_explain.consequence_note(event_type),
            )

    def test_high_note_explains_immediate_unsubscribe(self):
        note = ses_explain.consequence_note(SesEvent.EVENT_TYPE_COMPLAINT)
        self.assertIn('immediately', note.lower())
        self.assertIn('unsubscribe', note.lower())

    def test_medium_note_explains_temporary_and_threshold(self):
        note = ses_explain.consequence_note(
            SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT,
        )
        self.assertIn('temporary', note.lower())
        self.assertIn(str(SOFT_BOUNCE_THRESHOLD), note)


@tag('core')
class TermGlossaryTests(SimpleTestCase):
    def test_bounce_type_terms(self):
        for term in ('Permanent', 'Transient', 'Undetermined'):
            self.assertTrue(
                ses_explain.explain_term(term),
                f'expected non-empty explanation for {term}',
            )

    def test_bounce_subtype_terms(self):
        for term in (
            'General',
            'NoEmail',
            'Suppressed',
            'MailboxFull',
            'MessageTooLarge',
            'ContentRejected',
            'OnAccountSuppressionList',
            'abuse',
        ):
            self.assertTrue(
                ses_explain.explain_term(term),
                f'expected non-empty explanation for {term}',
            )

    def test_noemail_says_address_does_not_exist(self):
        self.assertIn(
            'does not exist',
            ses_explain.explain_term('NoEmail').lower(),
        )

    def test_abuse_mentions_spam(self):
        self.assertIn('spam', ses_explain.explain_term('abuse').lower())

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(
            ses_explain.explain_term('permanent'),
            ses_explain.explain_term('Permanent'),
        )
        self.assertTrue(ses_explain.explain_term('noemail'))

    def test_unknown_and_blank_terms_return_empty(self):
        self.assertEqual(ses_explain.explain_term('TotallyUnknown'), '')
        self.assertEqual(ses_explain.explain_term(''), '')
        self.assertEqual(ses_explain.explain_term(None), '')
        self.assertEqual(ses_explain.explain_term('   '), '')


@tag('core')
class DiagnosticDecoderTests(SimpleTestCase):
    def test_known_codes_map_to_sentences(self):
        for code in ('4.4.7', '4.4.1', '5.1.1', '5.2.2', '5.7.1', '5.3.4', '4.2.2'):
            pairs = ses_explain.decode_diagnostic(f'smtp; 550 {code} something')
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0][0], code)
            self.assertTrue(pairs[0][1])

    def test_blank_and_none_return_empty(self):
        self.assertEqual(ses_explain.decode_diagnostic(''), [])
        self.assertEqual(ses_explain.decode_diagnostic(None), [])

    def test_unrecognized_code_returns_empty(self):
        self.assertEqual(
            ses_explain.decode_diagnostic('smtp; 599 9.9.9 totally unknown failure'),
            [],
        )

    def test_reporter_multi_code_string_extracts_both_in_order(self):
        diagnostic = (
            'smtp; 550 4.4.7 Message expired: unable to deliver in 840 '
            'minutes.<421 4.4.1 Failed to establish connection>'
        )
        pairs = ses_explain.decode_diagnostic(diagnostic)
        codes = [code for code, _ in pairs]
        self.assertEqual(codes, ['4.4.7', '4.4.1'])
        self.assertIn('expired', pairs[0][1].lower())
        self.assertIn('connect', pairs[1][1].lower())

    def test_duplicate_codes_are_deduplicated_preserving_order(self):
        diagnostic = 'smtp; 5.1.1 no such user; retried 5.1.1 again; 4.4.7 expired'
        pairs = ses_explain.decode_diagnostic(diagnostic)
        codes = [code for code, _ in pairs]
        self.assertEqual(codes, ['5.1.1', '4.4.7'])

    def test_mixed_known_and_unknown_keeps_only_known(self):
        pairs = ses_explain.decode_diagnostic('9.9.9 unknown then 5.2.2 mailbox full')
        codes = [code for code, _ in pairs]
        self.assertEqual(codes, ['5.2.2'])
