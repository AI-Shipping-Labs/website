"""Tests for the sync_field_guide_interview converter command (issue #1705)."""

import tempfile
import uuid
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import frontmatter
from django.core.management import call_command
from django.test import TestCase

from content.models import InterviewCategory
from content.sync_parsers.families.interview_questions import (
    _sync_interview_question,
)

GUIDE_THEORY = """# Theory Questions

Based on candidate reports about [AI engineering](https://example.com/role) interviews.

## Format

Typically 45-60 minutes, conversational. [^fmt]

## Interview Questions

Actual questions reported by candidates.

### LLM Practice

Understanding how LLMs work.

- How do LLMs work? [^a]
- What is temperature? [^b] [^a]

### RAG Systems

Connecting LLMs to knowledge.

- What's RAG? [^a]

Examples:

- What is semantic caching? [^b]

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

EXISTING_THEORY = """---
content_id: 11111111-1111-1111-1111-111111111111
description: old description
sections: []
status: coming-soon
title: Theory Interview Questions
---

old body
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
        self.assertIn('01-theory.md -> interview-questions/theory.md', output)
        self.assertIn('2 sections, 4 questions', output)
        self.assertIn(
            '04-ai-system-design.md -> interview-questions/system-design.md',
            output,
        )
        self.assertIn('Dry run', output)

    def test_write_maps_slugs_and_skips_unmapped_files(self):
        output = self._run(write=True)
        self.assertTrue(self._target('theory.md').exists())
        self.assertTrue(self._target('system-design.md').exists())
        self.assertFalse(self._target('01-theory.md').exists())
        self.assertFalse(self._target('ai-system-design.md').exists())
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
        self.assertEqual(
            [q['question'] for q in sections[0]['qa']],
            ['How do LLMs work?', 'What is temperature?'],
        )
        # H2-direct bullets in the source become their own section.
        system_design = frontmatter.loads(
            self._target('system-design.md').read_text(encoding='utf-8'))
        self.assertEqual(
            [s['id'] for s in system_design.metadata['sections']],
            ['questions', 'typical-ai-system-design-questions'],
        )

    def test_footnotes_stripped_and_provenance_line_present(self):
        self._run(write=True)
        text = self._target('theory.md').read_text(encoding='utf-8')
        self.assertNotIn('[^', text)
        marker = '<!-- after-questions -->'
        self.assertIn(marker, text)
        after = text.split(marker, 1)[1]
        self.assertIn(
            'https://github.com/alexeygrigorev/ai-engineering-field-guide',
            after,
        )
        # Format prose is kept as body above the marker.
        self.assertIn('## Format', text.split(marker, 1)[0])

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

    def test_idempotent_second_run_byte_identical(self):
        self._run(write=True)
        target_dir = self.repo / 'interview-questions'
        first = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self._run(write=True)
        second = {p.name: p.read_bytes() for p in target_dir.iterdir()}
        self.assertEqual(first, second)
        self.assertEqual(set(first), {'theory.md', 'system-design.md'})

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
