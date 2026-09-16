"""Tests for the sync_field_guide_interview converter command (issue #1705)."""

import tempfile
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import frontmatter
from django.core.management import call_command
from django.test import TestCase

from content.models import InterviewCategory
from content.services import field_guide_interview as converter
from content.sync_parsers.families.interview_questions import (
    _sync_interview_question,
)

GUIDE_THEORY = """# Theory Questions

Based on candidate reports about [AI engineering](https://example.com/role) interviews.

This section covers knowledge-based questions.

## Format

Typically 45-60 minutes, conversational. [^fmt]

## Interview Questions

Actual questions reported by candidates.

### LLM Practice

Understanding how LLMs work.

- How do LLMs work? [^a]
- What is temperature? [^b] [^a]
- Your app gets 1M queries/day - how do you optimize cost? [^broken [^a]

### RAG Systems

Connecting LLMs to knowledge.

- What's RAG? [^a]

Examples:

- What is semantic caching? [^b]

### ML Fundamentals

Classical ML questions live in an external repo.

## Sources

[^a]: [Reddit thread](https://reddit.com/r/example)
[^b]: [HN thread](https://news.ycombinator.com/item?id=1)
[^fmt]: [Guide](https://example.com)
"""

GUIDE_SYSTEM_DESIGN = """# AI System Design Interview

Design orchestration around pre-trained models.

## Format

45-60 minutes. [^d]

## Questions

Frequently asked topics:

- Scale a chat feature to 1M daily users. [^d]
- Optimize cost for 1M queries/day. [^e]

### Typical AI System Design Questions

- Design a chatbot. [^d]
- Design a RAG system. [^e]

## Sources

[^d]: [X post](https://x.com/1)
[^e]: [Y blog](https://y.com/2)
"""

GUIDE_VS_CHILDREN = """# Coding Interview

Coding round content.

## AI System Design vs System Design

The fundamental shift: orchestration over training. [^v]

- Traditional ML focuses on training pipelines
- AI system design focuses on orchestration

### ML system design

Focus on training pipelines:

- Design a recommendation system [^v]

## Sources

[^v]: [Blog](https://example.com/v)
"""

EXISTING_THEORY = """---
content_id: 11111111-1111-1111-1111-111111111111
description: old description
sections: []
status: coming-soon
title: Theory Interview Questions
---
"""

EXISTING_PUBLISHED = """---
content_id: 22222222-2222-2222-2222-222222222222
description: old description
sections: []
status: published
title: Theory Interview Questions
---
"""


class SyncFieldGuideInterviewTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.guide = self._write_guide(root)
        self.repo = root / 'content-repo'
        (self.repo / 'interview-questions').mkdir(parents=True)

    def _write_guide(self, root):
        questions = root / 'guide' / 'interview' / 'questions'
        questions.mkdir(parents=True)
        sources = {
            '01-theory.md': GUIDE_THEORY,
            '04-ai-system-design.md': GUIDE_SYSTEM_DESIGN,
            '05-behavioral.md': GUIDE_VS_CHILDREN,
            'questions.md': '# All questions\n\n- aggregate dump\n',
            'notes.md': '# Notes\n\n- not a question bank\n',
        }
        for name, text in sources.items():
            (questions / name).write_text(text, encoding='utf-8')
        return root / 'guide'

    def _target(self, name):
        return self.repo / 'interview-questions' / name

    def _run(self, write=False):
        out = StringIO()
        args = [
            'sync_field_guide_interview',
            '--from-disk', str(self.guide),
            '--content-repo', str(self.repo),
        ]
        if write:
            args.append('--write')
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_dry_run_default_writes_no_files(self):
        target_dir = self.repo / 'interview-questions'
        before = sorted(p.name for p in target_dir.iterdir())
        output = self._run(write=False)
        after = sorted(p.name for p in target_dir.iterdir())
        self.assertEqual(before, after)
        self.assertEqual(after, [])
        self.assertIn('01-theory.md -> interview-questions/theory.md', output)
        self.assertIn('2 sections, 5 questions', output)
        self.assertIn(
            '04-ai-system-design.md -> interview-questions/system-design.md',
            output,
        )
        self.assertIn(
            '05-behavioral.md -> interview-questions/behavioral.md', output,
        )
        self.assertIn('Dry run', output)

    def test_write_maps_slugs_and_skips_unmapped_files(self):
        output = self._run(write=True)
        self.assertTrue(self._target('theory.md').exists())
        self.assertTrue(self._target('system-design.md').exists())
        self.assertTrue(self._target('behavioral.md').exists())
        self.assertFalse(self._target('01-theory.md').exists())
        self.assertFalse(self._target('ai-system-design.md').exists())
        self.assertFalse(self._target('questions.md').exists())
        self.assertFalse(self._target('notes.md').exists())
        self.assertIn(
            'questions.md (aggregate file, not a category bank)', output,
        )
        self.assertIn(
            'notes.md (not a known guide question file)', output,
        )

    def test_emitted_sections_match_source_structure(self):
        self._run(write=True)
        post = frontmatter.loads(
            self._target('theory.md').read_text(encoding='utf-8'))
        sections = post.metadata['sections']
        self.assertEqual(
            [s['id'] for s in sections], ['llm-practice', 'rag-systems'],
        )
        self.assertEqual(sections[0]['title'], 'LLM Practice')
        self.assertEqual(sections[1]['intro'], 'Connecting LLMs to knowledge.')
        self.assertEqual(
            [q['question'] for q in sections[0]['qa']],
            [
                'How do LLMs work?',
                'What is temperature?',
                'Your app gets 1M queries/day - how do you optimize cost?',
            ],
        )
        # H2-direct bullets in the source become their own section.
        system_design = frontmatter.loads(
            self._target('system-design.md').read_text(encoding='utf-8'))
        self.assertEqual(
            [s['id'] for s in system_design.metadata['sections']],
            ['questions', 'typical-ai-system-design-questions'],
        )

    def test_question_only_h3_with_no_bullets_stays_body_prose(self):
        self._run(write=True)
        text = self._target('theory.md').read_text(encoding='utf-8')
        section_titles = [
            s['title']
            for s in frontmatter.loads(text).metadata['sections']
        ]
        # Assert on the title: 'ml-fundamentals' reads as a Tailwind
        # margin token to the layout-assertion ratchet.
        self.assertNotIn('ML Fundamentals', section_titles)
        before_marker = text.split('<!-- after-questions -->', 1)[0]
        self.assertIn('### ML Fundamentals', before_marker)
        self.assertIn(
            'Classical ML questions live in an external repo.', before_marker,
        )

    def test_narrative_h2_with_question_h3_children_keeps_both(self):
        self._run(write=True)
        text = self._target('behavioral.md').read_text(encoding='utf-8')
        post = frontmatter.loads(text)
        self.assertEqual(
            [s['id'] for s in post.metadata['sections']],
            ['ml-system-design'],
        )
        before_marker = text.split('<!-- after-questions -->', 1)[0]
        self.assertIn('## AI System Design vs System Design', before_marker)
        self.assertIn(
            '- Traditional ML focuses on training pipelines', before_marker,
        )

    def test_footnotes_stripped_and_provenance_line_present(self):
        self._run(write=True)
        for name in ('theory.md', 'system-design.md', 'behavioral.md'):
            text = self._target(name).read_text(encoding='utf-8')
            self.assertNotIn('[^', text)
            marker = '<!-- after-questions -->'
            self.assertIn(marker, text)
            after = text.split(marker, 1)[1]
            self.assertIn(
                'https://github.com/alexeygrigorev/'
                'ai-engineering-field-guide',
                after,
            )
        # Format prose is kept as body above the marker.
        theory = self._target('theory.md').read_text(encoding='utf-8')
        self.assertIn('## Format', theory.split('<!-- after-questions -->')[0])

    def test_description_is_first_intro_paragraph(self):
        self._run(write=True)
        post = frontmatter.loads(
            self._target('theory.md').read_text(encoding='utf-8'))
        self.assertEqual(
            post.metadata['description'],
            'Based on candidate reports about AI engineering interviews.',
        )

    def test_status_preserved_and_new_files_default_coming_soon(self):
        self._target('theory.md').write_text(EXISTING_THEORY, encoding='utf-8')
        self._run(write=True)
        post = frontmatter.loads(
            self._target('theory.md').read_text(encoding='utf-8'))
        self.assertEqual(post.metadata['status'], 'coming-soon')
        self.assertEqual(
            post.metadata['content_id'],
            '11111111-1111-1111-1111-111111111111',
        )
        new_post = frontmatter.loads(
            self._target('system-design.md').read_text(encoding='utf-8'))
        self.assertEqual(new_post.metadata['status'], 'coming-soon')
        uuid.UUID(new_post.metadata['content_id'])

    def test_published_status_not_flipped(self):
        self._target('theory.md').write_text(
            EXISTING_PUBLISHED, encoding='utf-8')
        self._run(write=True)
        post = frontmatter.loads(
            self._target('theory.md').read_text(encoding='utf-8'))
        self.assertEqual(post.metadata['status'], 'published')
        self.assertEqual(
            post.metadata['content_id'],
            '22222222-2222-2222-2222-222222222222',
        )

    def test_idempotent_second_run_byte_identical(self):
        self._run(write=True)
        target_dir = self.repo / 'interview-questions'
        first = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self._run(write=True)
        second = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self.assertEqual(first, second)
        self.assertEqual(
            set(first), {'theory.md', 'system-design.md', 'behavioral.md'},
        )

    def test_help_documents_flags_and_refresh_loop(self):
        out = StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(out):
                call_command('sync_field_guide_interview', '--help')
        help_text = out.getvalue()
        self.assertIn('--from-disk', help_text)
        self.assertIn('--content-repo', help_text)
        self.assertIn('--write', help_text)
        self.assertIn('Review the diff in the content repo', help_text)

    def test_missing_guide_checkout_raises_command_error(self):
        with self.assertRaises(Exception) as ctx:
            call_command(
                'sync_field_guide_interview',
                '--from-disk', str(self.repo),
                '--content-repo', str(self.repo),
            )
        self.assertIn('interview/questions/', str(ctx.exception))

    def test_parser_round_trip_upserts_category(self):
        self._run(write=True)
        post = frontmatter.loads(
            self._target('theory.md').read_text(encoding='utf-8'))
        stats = {
            'created': 0, 'updated': 0, 'unchanged': 0,
            'deleted': 0, 'errors': [], 'items_detail': [],
        }
        source = SimpleNamespace(repo_name='AI-Shipping-Labs/content')
        _sync_interview_question(
            source, 'interview-questions/theory.md',
            dict(post.metadata), post.content, 'abc123', stats, set(),
        )
        category = InterviewCategory.objects.get(slug='theory')
        self.assertEqual(len(category.sections_json), 2)
        self.assertEqual(
            category.sections_json[0]['qa'][0]['question'],
            'How do LLMs work?',
        )
        self.assertEqual(category.status, 'coming-soon')
        self.assertEqual(category.source_repo, 'AI-Shipping-Labs/content')


class FieldGuideConverterServiceTest(TestCase):
    def test_source_files_strip_prefix_and_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            questions = Path(tmp) / 'interview' / 'questions'
            questions.mkdir(parents=True)
            for name in (
                '01-theory.md',
                '02-coding.md',
                '03-project-deep-dive.md',
                '04-ai-system-design.md',
                '05-behavioral.md',
                '06-home-assignments.md',
                'questions.md',
                '07-notes.md',
            ):
                (questions / name).write_text('# x\n', encoding='utf-8')
            mapping = converter.source_files(tmp)
            self.assertEqual(
                mapping,
                [
                    ('interview/questions/01-theory.md', 'theory'),
                    ('interview/questions/02-coding.md', 'coding'),
                    (
                        'interview/questions/03-project-deep-dive.md',
                        'project-deep-dive',
                    ),
                    (
                        'interview/questions/04-ai-system-design.md',
                        'system-design',
                    ),
                    ('interview/questions/05-behavioral.md', 'behavioral'),
                    (
                        'interview/questions/06-home-assignments.md',
                        'home-assignments',
                    ),
                    ('interview/questions/07-notes.md', 'notes'),
                ],
            )
            self.assertEqual(
                converter.unmapped_files(tmp), ['questions.md'],
            )

    def test_strip_footnotes_removes_defs_refs_and_dangling_markers(self):
        text = (
            'How do LLMs work? [^a] [^broken\n\n'
            '[^a]: [Reddit](https://reddit.com/r/example)\n'
        )
        cleaned = converter.strip_footnotes(text)
        self.assertNotIn('[^', cleaned)
        self.assertNotIn('Reddit', cleaned)
        self.assertIn('How do LLMs work?', cleaned)
