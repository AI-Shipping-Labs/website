"""Tests for the sync_field_guide_job_market converter command (issue #1714)."""

import re
import tempfile
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import yaml
from django.core.management import call_command
from django.test import TestCase

from content.models import Article
from content.services import field_guide_job_market as converter
from content.sync_parsers.families.articles import _sync_article


def _write_posting(month_dir, job_id, *, company=None, ai_type=None,
                   skills=None, locations=None, location=None):
    """Write one synthetic posting YAML into a scrape directory."""
    doc = {
        'company': {'name': company},
        'position': {'title': f'AI Engineer {job_id}'},
        'meta': {'job_id': str(job_id)},
    }
    if ai_type:
        doc['position']['ai_type'] = {'type': ai_type}
    if skills:
        doc['position']['skills'] = skills
    if locations:
        doc['meta']['locations'] = locations
    if location:
        doc['meta']['location'] = location
    (month_dir / f'{job_id}_company.yaml').write_text(
        yaml.safe_dump(doc), encoding='utf-8')


def _make_guide(root):
    """Two-scrape synthetic guide directory with hand-computed aggregates."""
    first = root / 'guide' / 'job-market' / 'data_structured' / '2026-01-05'
    second = root / 'guide' / 'job-market' / 'data_structured' / '2026-02-20'
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    _write_posting(
        first, 1, company='Alpha', ai_type='ai-first',
        skills={'genai': ['LLMs', 'RAG'], 'languages': ['Python'],
                'cloud': ['AWS']},
        location='New York, USA',
    )
    _write_posting(
        first, 2, company='Alpha', ai_type='ai-support',
        skills={'genai': ['LLMs'], 'languages': ['Python', 'JavaScript']},
        locations=['Berlin, DEU', 'London, GBR'],
    )
    _write_posting(
        first, 3, company='Beta', ai_type='ml-first',
        skills={'genai': ['Agents', 'LLMs'], 'languages': ['Python']},
        locations=['New York, USA', 'Berlin, DEU', 'Berlin, DEU'],
    )
    _write_posting(
        second, 4, company='Gamma', ai_type='ai-first',
        skills={'genai': ['LLMs', 'RAG', 'Agents'], 'languages': ['Python'],
                'cloud': ['GCP']},
        locations=['New York, USA'],
    )
    _write_posting(
        second, 5,
        skills={'languages': ['Python']},
        locations=['New York, USA', 'Austin, USA'],
    )
    return root / 'guide'


class JobMarketAggregateMathTest(TestCase):
    """Hand-computed aggregate math on the synthetic two-scrape fixture."""

    @classmethod
    def setUpTestData(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.guide = _make_guide(Path(tmp.name))
        cls.months = converter.scrape_months(cls.guide)
        cls.postings = converter.load_postings(cls.guide, cls.months)
        cls.agg = converter.compute_aggregates(cls.postings, cls.months)

    def test_months_sorted_and_provenance_counts(self):
        provenance = self.agg['provenance']
        self.assertEqual(self.months, ['2026-01-05', '2026-02-20'])
        self.assertEqual(provenance['scrape_count'], 2)
        self.assertEqual(provenance['total_postings'], 5)
        self.assertEqual(
            [(s['date'], s['postings']) for s in provenance['scrapes']],
            [('2026-01-05', 3), ('2026-02-20', 2)],
        )
        self.assertEqual(provenance['latest_scrape'], '2026-02-20')
        self.assertEqual(provenance['latest_postings'], 2)
        self.assertEqual(
            provenance['window'], 'Jan 5, 2026 to Feb 20, 2026')
        self.assertEqual(provenance['data_through'], 'February 2026')

    def test_skill_demand_counts_shares_and_descending_order(self):
        demand = {
            entry['category']: entry for entry in self.agg['skill_demand']}
        # Latest scrape has 2 postings: each genai skill is 50.0%, Python
        # is 100.0%. Equal counts tie-break alphabetically.
        genai = demand['genai']
        self.assertEqual(
            [(s['name'], s['count'], s['pct']) for s in genai['skills']],
            [('Agents', 1, 50.0), ('LLMs', 1, 50.0), ('RAG', 1, 50.0)],
        )
        languages = demand['languages']
        self.assertEqual(
            [(s['name'], s['count'], s['pct']) for s in languages['skills']],
            [('Python', 2, 100.0)],
        )
        self.assertEqual(
            [s['name'] for s in demand['cloud']['skills']], ['GCP'])
        # Categories without skills in the latest scrape are omitted.
        self.assertNotIn('ops', demand)

    def test_skill_trends_cover_all_months_with_within_month_shares(self):
        trends = self.agg['skill_trends']
        self.assertEqual(trends['months'], ['2026-01-05', '2026-02-20'])
        self.assertEqual(trends['month_labels'], ['Jan 5', 'Feb 20'])
        genai = trends['groups'][0]
        self.assertEqual(genai['category'], 'genai')
        # Ranking is by total mentions across scrapes (LLMs 4, Agents 2,
        # RAG 2); ties break alphabetically.
        self.assertEqual(
            [s['name'] for s in genai['skills']], ['LLMs', 'Agents', 'RAG'])
        llms = genai['skills'][0]
        # 3 of 3 postings in January, 1 of 2 in February.
        self.assertEqual(llms['shares'], [100.0, 50.0])
        agents = genai['skills'][1]
        self.assertEqual(agents['shares'], [33.3, 50.0])
        languages = trends['groups'][1]
        self.assertEqual(
            [(s['name'], s['shares']) for s in languages['skills']],
            [('Python', [100.0, 100.0]), ('JavaScript', [33.3, 0.0])],
        )

    def test_role_mix_shares_per_month_with_unknown_fallback(self):
        role_mix = self.agg['role_mix']
        self.assertEqual(role_mix['totals'], [3, 2])
        by_type = {t['type']: t['shares'] for t in role_mix['types']}
        self.assertEqual(by_type['ai-first'], [33.3, 50.0])
        self.assertEqual(by_type['ai-support'], [33.3, 0.0])
        self.assertEqual(by_type['ml-first'], [33.3, 0.0])
        # Posting 5 has no ai_type.type and falls back to unknown.
        self.assertEqual(by_type['unknown'], [0.0, 50.0])
        self.assertEqual(
            [t['label'] for t in role_mix['types']],
            ['AI-first', 'AI-support', 'ML-first', 'Unclassified'],
        )

    def test_top_companies_come_from_the_latest_scrape(self):
        # Alpha posted twice but only in January; Gamma leads February.
        self.assertEqual(
            [(c['name'], c['count'], c['pct'])
             for c in self.agg['companies']],
            [('Gamma', 1, 50.0)],
        )

    def test_locations_are_city_level_and_deduplicated(self):
        locations = self.agg['locations']
        # February: New York twice (once from the list, Alpha's January
        # singular location does not count), Austin once.
        self.assertEqual(
            [(loc['name'], loc['count'], loc['pct'])
             for loc in locations],
            [('New York', 2, 100.0), ('Austin', 1, 50.0)],
        )

    def test_singular_location_fallback_counts_in_its_own_month(self):
        # Month-level check: January has 3 postings and New York is
        # mentioned by postings 1 and 3.
        agg = converter.compute_aggregates(
            self.postings, ['2026-01-05'])
        self.assertEqual(
            [(loc['name'], loc['count'], loc['pct'])
             for loc in agg['locations']],
            # Equal counts tie-break alphabetically (Berlin before
            # New York).
            [('Berlin', 2, 66.7), ('New York', 2, 66.7),
             ('London', 1, 33.3)],
        )

    def test_top_n_truncation_for_demand_and_trends(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        guide_root = Path(tmp.name)
        month_dir = (
            guide_root / 'job-market' / 'data_structured' / '2026-03-10')
        month_dir.mkdir(parents=True)
        _write_posting(
            month_dir, 1, company='TruncCo', ai_type='ai-first',
            skills={
                'genai': [f'S{i:02d}' for i in range(1, 13)],
                'languages': ['Python'],
            },
        )
        _write_posting(
            month_dir, 2, company='TruncCo', ai_type='ai-first',
            skills={'genai': ['S01', 'S02']},
        )
        months = converter.scrape_months(guide_root)
        agg = converter.compute_aggregates(
            converter.load_postings(guide_root, months), months)
        demand = {
            entry['category']: entry for entry in agg['skill_demand']}
        genai_names = [s['name'] for s in demand['genai']['skills']]
        self.assertEqual(len(genai_names), converter.SKILL_DEMAND_TOP)
        self.assertEqual(
            genai_names,
            ['S01', 'S02'] + [f'S{i:02d}' for i in range(3, 11)],
        )
        trends = agg['skill_trends']['groups'][0]
        self.assertEqual(
            [s['name'] for s in trends['skills']],
            ['S01', 'S02'] + [f'S{i:02d}' for i in range(3, 9)],
        )
        self.assertEqual(len(trends['skills']), converter.TREND_TOP['genai'])


class MarkdownTableCellEscapingTest(TestCase):
    """A pipe in a data value must not split its markdown table row.

    The dataset contains employer names like ``Holiday Channel | My
    Holiday World``; an unescaped pipe would corrupt the companies
    table on the rendered page.
    """

    def test_pipe_in_cell_is_escaped_and_row_stays_two_columns(self):
        table = converter._markdown_table(
            ['Company', 'Postings'],
            [('Holiday Channel | My Holiday World', 3)],
        )
        self.assertIn('Holiday Channel \\| My Holiday World', table)
        row = table.splitlines()[-1]
        cells = re.split(r'(?<!\\)\|', row)
        # Splitting on unescaped pipes leaves exactly the two columns.
        self.assertEqual(
            [cell.strip() for cell in cells[1:-1]],
            ['Holiday Channel \\| My Holiday World', '3'],
        )


class SyncFieldGuideJobMarketCommandTest(TestCase):
    """Dry-run versus write behavior of the management command."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.guide = _make_guide(Path(tmp.name))
        self.repo = Path(tmp.name) / 'content-repo'
        self.repo.mkdir()

    def _run(self, write=False):
        out = StringIO()
        args = [
            'sync_field_guide_job_market',
            '--from-disk', str(self.guide),
            '--content-repo', str(self.repo),
        ]
        if write:
            args.append('--write')
        call_command(*args, stdout=out)
        return out.getvalue()

    def _article_path(self):
        return self.repo / converter.ARTICLE_TARGET_DIR / 'index.md'

    def test_dry_run_writes_nothing_and_prints_summary(self):
        output = self._run(write=False)
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertIn('Scrape 2026-01-05: 3 postings', output)
        self.assertIn('Scrape 2026-02-20: 2 postings', output)
        self.assertIn(
            'Total: 5 postings across 2 scrapes; data through '
            'February 2026.', output)
        self.assertIn(f'{converter.ARTICLE_TARGET_DIR}/index.md', output)
        self.assertIn(converter.SKILLS_WIDGET_TARGET, output)
        self.assertIn(converter.TRENDS_WIDGET_TARGET, output)
        self.assertIn('Dry run', output)

    def test_write_creates_article_and_widgets(self):
        self._run(write=True)
        article = frontmatter.loads(
            self._article_path().read_text(encoding='utf-8'))
        uuid.UUID(article.metadata['content_id'])
        self.assertEqual(
            article.metadata['slug'], 'ai-engineering-job-market')
        self.assertEqual(article.metadata['status'], 'published')
        self.assertEqual(article.metadata['required_level'], 0)
        self.assertEqual(article.metadata['page_type'], 'blog')
        self.assertEqual(article.metadata['date'], '2026-02-20')
        self.assertIn('field-guide', article.metadata['tags'])
        data = article.metadata['data']
        self.assertEqual(data['provenance']['total_postings'], 5)
        self.assertEqual(len(data['skill_demand']), 3)
        self.assertEqual(len(data['companies']), 1)
        self.assertEqual(len(data['locations']), 2)
        self.assertEqual(len(data['role_mix']['month_labels']), 2)
        body = article.content
        self.assertIn('<!-- include:widgets/job_market_skills.html -->', body)
        self.assertIn('<!-- include:widgets/job_market_trends.html -->', body)
        self.assertIn('Data through February 2026', body)
        self.assertIn(converter.GUIDE_JOB_MARKET_URL, body)
        self.assertIn('/blog/ai-engineering-field-guide', body)
        self.assertIn('/membership', body)
        self.assertTrue(
            (self.repo / converter.SKILLS_WIDGET_TARGET).exists())
        self.assertTrue(
            (self.repo / converter.TRENDS_WIDGET_TARGET).exists())

    def test_second_write_is_byte_identical_and_keeps_content_id(self):
        self._run(write=True)
        first_files = {
            str(path.relative_to(self.repo)): path.read_bytes()
            for path in sorted(self.repo.rglob('*')) if path.is_file()
        }
        output = self._run(write=True)
        second_files = {
            str(path.relative_to(self.repo)): path.read_bytes()
            for path in sorted(self.repo.rglob('*')) if path.is_file()
        }
        self.assertEqual(first_files, second_files)
        self.assertIn('kept content_id', output)

    def test_existing_article_keeps_its_content_id(self):
        self._run(write=True)
        article = frontmatter.loads(
            self._article_path().read_text(encoding='utf-8'))
        self.assertEqual(
            article.metadata['content_id'],
            frontmatter.loads(
                self._article_path().read_text(encoding='utf-8'),
            ).metadata['content_id'],
        )
        # A fresh converter run over a hand-planted target reuses its id.
        planted = str(uuid.uuid4())
        self._article_path().write_text(
            f'---\ncontent_id: {planted}\ntitle: Old\n---\n\nold body\n',
            encoding='utf-8',
        )
        self._run(write=True)
        article = frontmatter.loads(
            self._article_path().read_text(encoding='utf-8'))
        self.assertEqual(article.metadata['content_id'], planted)
        self.assertEqual(article.metadata['status'], 'published')

    def test_help_documents_flags_and_refresh_loop(self):
        out = StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(out):
                call_command('sync_field_guide_job_market', '--help')
        help_text = out.getvalue()
        self.assertIn('--from-disk', help_text)
        self.assertIn('--content-repo', help_text)
        self.assertIn('--write', help_text)
        self.assertIn('Review the diff in the content repo', help_text)

    def test_missing_guide_checkout_raises_command_error(self):
        with self.assertRaises(Exception) as ctx:
            call_command(
                'sync_field_guide_job_market',
                '--from-disk', str(self.repo),
                '--content-repo', str(self.repo),
            )
        self.assertIn('job-market/data_structured/', str(ctx.exception))

    def test_missing_content_repo_raises_command_error(self):
        with self.assertRaises(Exception) as ctx:
            call_command(
                'sync_field_guide_job_market',
                '--from-disk', str(self.guide),
                '--content-repo', str(self.repo / 'absent'),
            )
        self.assertIn('Content repo checkout not found', str(ctx.exception))


class JobMarketArticleSyncViewTest(TestCase):
    """Generated article syncs through the articles parser and renders."""

    @classmethod
    def setUpTestData(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        guide = _make_guide(Path(tmp.name))
        cls.repo = Path(tmp.name) / 'content-repo'
        cls.repo.mkdir()
        call_command(
            'sync_field_guide_job_market',
            '--from-disk', str(guide),
            '--content-repo', str(cls.repo),
            '--write',
            stdout=StringIO(),
        )
        article_path = (
            cls.repo / converter.ARTICLE_TARGET_DIR / 'index.md')
        post = frontmatter.loads(article_path.read_text(encoding='utf-8'))
        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0,
            'deleted': 0, 'errors': [], 'items_detail': [],
        }
        _sync_article(
            SimpleNamespace(repo_name='AI-Shipping-Labs/content'),
            str(cls.repo),
            f'{converter.ARTICLE_TARGET_DIR}/index.md',
            dict(post.metadata),
            post.content,
            'abc123',
            stats,
            None,
            set(),
            set(),
        )
        cls.stats = stats
        cls.article = Article.objects.get(slug='ai-engineering-job-market')

    def test_parser_round_trip_persists_public_published_article(self):
        self.assertEqual(self.stats['errors'], [])
        self.assertTrue(self.article.published)
        self.assertEqual(self.article.required_level, 0)
        self.assertEqual(self.article.page_type, 'blog')
        self.assertEqual(
            self.article.data_json['provenance']['total_postings'], 5)
        self.assertEqual(
            self.article.data_json['role_mix']['month_labels'],
            ['Jan 5', 'Feb 20'],
        )

    def test_includes_expanded_to_widget_markup(self):
        html = self.article.content_html
        # Skill bars carry CSS widths from the data payload.
        self.assertIn('style="width: 50.0%"', html)
        self.assertIn('style="width: 100.0%"', html)
        self.assertIn('Agents', html)
        # Trends widget renders one column per scrape month.
        self.assertIn('Skill trends by scrape', html)
        self.assertIn('Role mix by scrape', html)
        self.assertIn('Feb 20', html)

    def test_anonymous_page_renders_provenance_and_links(self):
        response = self.client.get('/blog/ai-engineering-job-market')
        # assertContains requires a 200 response, covering the status contract.
        self.assertContains(response, 'Data through February 2026')
        content = response.content.decode()
        self.assertIn('Gamma', content)
        self.assertIn(
            'https://github.com/alexeygrigorev/'
            'ai-engineering-field-guide/tree/main/job-market',
            content,
        )
        self.assertIn('/blog/ai-engineering-field-guide', content)
        self.assertIn('/membership', content)

    def test_anonymous_page_is_not_gated(self):
        response = self.client.get('/blog/ai-engineering-job-market')
        # assertContains requires a 200 response, covering the status contract.
        self.assertContains(response, 'Skill trends by scrape')
        self.assertNotContains(
            response, 'gated-create-free-account-link')

    def test_article_appears_in_sitemap(self):
        response = self.client.get('/sitemap.xml')
        # assertContains requires a 200 response, covering the status contract.
        self.assertContains(response, '/blog/ai-engineering-job-market')
