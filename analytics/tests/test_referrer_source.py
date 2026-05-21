"""Unit-test matrix for `analytics.referrer_source.normalize_referrer`.

One test per documented hostname pattern from #772 — failure of any single
case here means the public bucket label changed for that channel, which is
a real product-visible regression.
"""

from django.test import SimpleTestCase

from analytics.referrer_source import ReferrerSource, normalize_referrer


class NormalizeReferrerTest(SimpleTestCase):
    """Pure-function normalizer cases. One assertion per hostname pattern."""

    # --- LinkedIn --------------------------------------------------------

    def test_linkedin_apex(self):
        self.assertEqual(normalize_referrer('linkedin.com'), 'linkedin')

    def test_linkedin_www_subdomain(self):
        self.assertEqual(normalize_referrer('www.linkedin.com'), 'linkedin')

    def test_linkedin_shortlink(self):
        self.assertEqual(normalize_referrer('lnkd.in'), 'linkedin')

    # --- YouTube ---------------------------------------------------------

    def test_youtube_apex(self):
        self.assertEqual(normalize_referrer('youtube.com'), 'youtube')

    def test_youtube_www_subdomain(self):
        self.assertEqual(normalize_referrer('www.youtube.com'), 'youtube')

    def test_youtube_shortlink(self):
        self.assertEqual(normalize_referrer('youtu.be'), 'youtube')

    # --- ChatGPT ---------------------------------------------------------

    def test_chatgpt_new_domain(self):
        self.assertEqual(normalize_referrer('chatgpt.com'), 'chatgpt')

    def test_chatgpt_legacy_subdomain(self):
        self.assertEqual(normalize_referrer('chat.openai.com'), 'chatgpt')

    # --- Perplexity ------------------------------------------------------

    def test_perplexity_apex(self):
        self.assertEqual(normalize_referrer('perplexity.ai'), 'perplexity')

    # --- Claude ----------------------------------------------------------

    def test_claude_apex(self):
        self.assertEqual(normalize_referrer('claude.ai'), 'claude')

    # --- Gemini (must beat Google) --------------------------------------

    def test_gemini_subdomain(self):
        self.assertEqual(normalize_referrer('gemini.google.com'), 'gemini')

    def test_bard_subdomain_maps_to_gemini(self):
        self.assertEqual(normalize_referrer('bard.google.com'), 'gemini')

    # --- Google (com + cc-TLDs) -----------------------------------------

    def test_google_com(self):
        self.assertEqual(normalize_referrer('google.com'), 'google')

    def test_google_www_com(self):
        self.assertEqual(normalize_referrer('www.google.com'), 'google')

    def test_google_de_cctld(self):
        self.assertEqual(normalize_referrer('www.google.de'), 'google')

    def test_google_co_uk_cctld(self):
        self.assertEqual(normalize_referrer('www.google.co.uk'), 'google')

    # --- Search engines --------------------------------------------------

    def test_bing(self):
        self.assertEqual(normalize_referrer('bing.com'), 'bing')

    def test_duckduckgo(self):
        self.assertEqual(normalize_referrer('duckduckgo.com'), 'duckduckgo')

    # --- Twitter / X -----------------------------------------------------

    def test_twitter_com(self):
        self.assertEqual(normalize_referrer('twitter.com'), 'twitter')

    def test_x_com(self):
        self.assertEqual(normalize_referrer('x.com'), 'twitter')

    def test_twitter_shortlink(self):
        self.assertEqual(normalize_referrer('t.co'), 'twitter')

    # --- Facebook --------------------------------------------------------

    def test_facebook_apex(self):
        self.assertEqual(normalize_referrer('facebook.com'), 'facebook')

    def test_facebook_mobile_subdomain(self):
        self.assertEqual(normalize_referrer('m.facebook.com'), 'facebook')

    def test_facebook_shortlink(self):
        self.assertEqual(normalize_referrer('fb.me'), 'facebook')

    # --- Reddit ----------------------------------------------------------

    def test_reddit_apex(self):
        self.assertEqual(normalize_referrer('reddit.com'), 'reddit')

    def test_reddit_old_subdomain(self):
        self.assertEqual(normalize_referrer('old.reddit.com'), 'reddit')

    # --- Hacker News -----------------------------------------------------

    def test_hackernews(self):
        self.assertEqual(normalize_referrer('news.ycombinator.com'), 'hackernews')

    # --- GitHub ----------------------------------------------------------

    def test_github(self):
        self.assertEqual(normalize_referrer('github.com'), 'github')

    # --- Medium (subdomain match) ---------------------------------------

    def test_medium_apex(self):
        self.assertEqual(normalize_referrer('medium.com'), 'medium')

    def test_medium_user_subdomain(self):
        self.assertEqual(normalize_referrer('someone.medium.com'), 'medium')

    # --- Substack (subdomain match) -------------------------------------

    def test_substack_apex(self):
        self.assertEqual(normalize_referrer('substack.com'), 'substack')

    def test_substack_writer_subdomain(self):
        self.assertEqual(normalize_referrer('writer.substack.com'), 'substack')

    # --- Internal -------------------------------------------------------

    def test_internal_apex(self):
        self.assertEqual(normalize_referrer('aishippinglabs.com'), 'internal')

    def test_internal_www_subdomain(self):
        self.assertEqual(normalize_referrer('www.aishippinglabs.com'), 'internal')

    # --- Catch-all / direct ---------------------------------------------

    def test_unknown_host_maps_to_other(self):
        self.assertEqual(normalize_referrer('example.xyz'), 'other')

    def test_empty_host_maps_to_direct(self):
        self.assertEqual(normalize_referrer(''), 'direct')


class ReferrerSourceEnumTest(SimpleTestCase):
    """Smoke-test the TextChoices enum so the admin dropdown stays wired."""

    def test_direct_is_a_valid_choice(self):
        self.assertIn('direct', ReferrerSource.values)

    def test_other_is_a_valid_choice(self):
        self.assertIn('other', ReferrerSource.values)

    def test_linkedin_label_is_human_readable(self):
        self.assertEqual(ReferrerSource.LINKEDIN.label, 'LinkedIn')

    def test_choices_include_every_documented_bucket(self):
        """Guard against a bucket being removed from the enum by accident."""
        expected_values = {
            'linkedin', 'youtube', 'chatgpt', 'perplexity', 'claude',
            'gemini', 'google', 'bing', 'duckduckgo', 'twitter',
            'facebook', 'reddit', 'hackernews', 'github', 'medium',
            'substack', 'internal', 'other', 'direct',
        }
        self.assertEqual(set(ReferrerSource.values), expected_values)
