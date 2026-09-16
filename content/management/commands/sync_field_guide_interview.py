"""Convert field-guide interview question banks into content-repo markdown.

One-way converter between a local checkout of the AI Engineering Field
Guide (https://github.com/alexeygrigorev/ai-engineering-field-guide) and
the content repo's ``interview-questions/*.md`` files. The guide keeps
evolving (new questions, new footnote sources, monthly additions) while
hand-converted copies on the site drift stale; this command refreshes
them.

The command only reads and writes plain files: it never touches the
database and never runs git. The operator reviews the diff, commits and
pushes the content repo, and the existing content sync (webhook push or
the authenticated sync API) upserts the ``InterviewCategory`` rows, so
Studio sync history stays meaningful.

Refresh loop:

1. On a clean content-repo checkout, run with ``--write``::

       uv run python manage.py sync_field_guide_interview \\
           --from-disk ~/git/ai-engineering-field-guide --write

2. Review the diff in the content repo, commit and push.
3. Let the webhook sync run, or trigger the sync via the existing
   authenticated sync API (the content repo's ``scripts/sync_production.py``
   does this).

Without ``--write`` the command is a dry run: it prints the per-file
summary and writes nothing.
"""

import os
import re
import uuid
from argparse import RawDescriptionHelpFormatter
from pathlib import Path

import frontmatter
import yaml
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

FIELD_GUIDE_REPO_URL = (
    'https://github.com/alexeygrigorev/ai-engineering-field-guide'
)
PROVENANCE_LINE = (
    'Question sources are tracked in the [AI Engineering Field Guide]'
    f'({FIELD_GUIDE_REPO_URL}) under interview/questions.'
)
AFTER_QUESTIONS_MARKER = '<!-- after-questions -->'
NEW_FILE_STATUS = 'coming-soon'

# Guide file under interview/questions/ -> content repo slug (the public
# /interview/<slug>/ URL). Numeric prefixes are stripped so a refresh
# updates the six existing InterviewCategory rows in place; 04 also
# drops the "ai-" infix to keep the existing system-design slug.
GUIDE_FILE_TO_SLUG = {
    '01-theory.md': 'theory',
    '02-coding.md': 'coding',
    '03-project-deep-dive.md': 'project-deep-dive',
    '04-ai-system-design.md': 'system-design',
    '05-behavioral.md': 'behavioral',
    '06-home-assignments.md': 'home-assignments',
}

# Aggregate question dump in the guide; never becomes a category.
AGGREGATE_GUIDE_FILE = 'questions.md'

# H2 headings that are narrative context rather than question banks:
# their content stays on the page as body text above the question
# lists. Unknown H2 headings default to question banks so new monthly
# sections (for example "July 2026 Additions") convert without a code
# change; a new narrative heading needs one entry here.
PROSE_HEADINGS = frozenset({
    'format',
    'coding round formats',
    'expectations',
    'what interviewers evaluate',
    'how to prepare',
    'sources',
})
# Narrative heading whose H3 subsections are still question banks: its
# own text becomes body prose, its children become sections.
PROSE_HEADINGS_WITH_QUESTION_CHILDREN = frozenset({
    'ai system design vs system design',
})

H1_RE = re.compile(r'^# (.+)$')
H2_RE = re.compile(r'^## (.+)$')
H3_RE = re.compile(r'^### (.+)$')
FOOTNOTE_DEF_RE = re.compile(r'^\[\^[^\]]+\]:')
FOOTNOTE_MARK_RE = re.compile(r'\[\^[^\]]+\]')
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]*\)')


def _clean_line(line):
    """Strip footnote markers and trailing padding from one line."""
    line = FOOTNOTE_MARK_RE.sub('', line)
    line = re.sub(r' {2,}', ' ', line)
    return line.rstrip()


def _strip_footnote_definitions(text):
    kept = [
        line for line in text.splitlines()
        if not FOOTNOTE_DEF_RE.match(line.strip())
    ]
    return '\n'.join(kept)


def _split_h3_blocks(lines):
    """Split content lines into (preamble_lines, [(h3_title, lines)])."""
    preamble = []
    blocks = []
    current_title = None
    current_lines = []
    for line in lines:
        match = H3_RE.match(line)
        if match:
            if current_title is not None:
                blocks.append((current_title, current_lines))
            current_title = match.group(1).strip()
            current_lines = []
        elif current_title is None:
            preamble.append(line)
        else:
            current_lines.append(line)
    if current_title is not None:
        blocks.append((current_title, current_lines))
    return preamble, blocks


def _split_intro_and_questions(lines):
    """Return (intro, questions) for one section's content lines.

    Questions are the top-level ``- `` bullets; the intro is the prose
    before the first bullet. Prose after the first bullet (quotes,
    asides) is dropped.
    """
    intro_parts = []
    questions = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- '):
            questions.append(_clean_line(stripped[2:]).strip())
        elif not questions and stripped:
            text = _clean_line(stripped).strip()
            # Skip bare list lead-ins ("Examples:", "Types:") — they
            # mean nothing once separated from their bullets.
            if text.endswith(':') and len(text.split()) <= 4:
                continue
            intro_parts.append(text)
    intro = ' '.join(intro_parts).strip().rstrip(':').strip()
    return intro, questions


def _body_chunk(title, lines):
    """Render a narrative H2 block as page-body markdown, or '' if empty."""
    text = _strip_footnote_definitions('\n'.join(_clean_line(l) for l in lines))
    text = re.sub(r'\n{3,}', '\n\n', text).strip('\n')
    if not text.strip():
        return ''
    return f'## {title}\n\n{text}'


class GuideQuestionConverter:
    """Converts one guide question file into frontmatter + body strings."""

    def __init__(self):
        self._seen_section_ids = set()

    def convert(self, text):
        lines = text.splitlines()
        title, body_start = self._extract_title(lines)
        description = self._extract_description(lines, body_start)
        sections, body_chunks = self._walk_h2_blocks(lines, body_start)
        return {
            'title': title,
            'description': description,
            'sections': sections,
        }, self._build_body(body_chunks)

    def _extract_title(self, lines):
        for index, line in enumerate(lines):
            match = H1_RE.match(line)
            if match:
                return match.group(1).strip(), index + 1
        raise ValueError('no H1 title found')

    def _extract_description(self, lines, start):
        paragraph = []
        for line in lines[start:]:
            if H2_RE.match(line) or H3_RE.match(line):
                break
            stripped = _clean_line(line).strip()
            if stripped:
                paragraph.append(MD_LINK_RE.sub(r'\1', stripped))
            elif paragraph:
                break
        return ' '.join(paragraph)

    def _walk_h2_blocks(self, lines, start):
        sections = []
        body_chunks = []
        current_title = None
        current_lines = []
        for line in lines[start:]:
            match = H2_RE.match(line)
            if match:
                self._absorb_h2_block(
                    current_title, current_lines, sections, body_chunks,
                )
                current_title = match.group(1).strip()
                current_lines = []
            elif current_title is not None:
                current_lines.append(line)
        self._absorb_h2_block(current_title, current_lines, sections, body_chunks)
        return sections, body_chunks

    def _absorb_h2_block(self, title, lines, sections, body_chunks):
        if title is None:
            return
        key = title.lower()
        if key == 'sources':
            # Footnote definitions live here; replaced by the
            # provenance line after <!-- after-questions -->.
            return
        if key in PROSE_HEADINGS:
            body_chunks.append(_body_chunk(title, lines))
            return
        preamble, h3_blocks = _split_h3_blocks(lines)
        if key in PROSE_HEADINGS_WITH_QUESTION_CHILDREN:
            # Narrative comparison text, but its H3 children are
            # genuine question banks (e.g. "Traditional system design").
            body_chunks.append(_body_chunk(title, preamble))
        else:
            self._add_sections_from_container(title, preamble, sections)
        for h3_title, h3_lines in h3_blocks:
            self._add_h3_section(h3_title, h3_lines, sections)

    def _add_sections_from_container(self, title, preamble_lines, sections):
        intro, questions = _split_intro_and_questions(preamble_lines)
        if questions:
            sections.append(self._make_section(title, intro, questions))

    def _add_h3_section(self, title, lines, sections):
        intro, questions = _split_intro_and_questions(lines)
        if questions:
            sections.append(self._make_section(title, intro, questions))

    def _make_section(self, title, intro, questions):
        base_id = slugify(title) or 'section'
        section_id = base_id
        suffix = 2
        while section_id in self._seen_section_ids:
            section_id = f'{base_id}-{suffix}'
            suffix += 1
        self._seen_section_ids.add(section_id)
        return {
            'id': section_id,
            'title': title,
            'intro': intro,
            'qa': [{'question': question} for question in questions],
        }

    def _build_body(self, body_chunks):
        parts = [chunk for chunk in body_chunks if chunk]
        body = '\n'.join([''] + parts + [''])  # leading blank line style
        return (
            f'{body}\n{AFTER_QUESTIONS_MARKER}\n\n{PROVENANCE_LINE}\n'
        )


def _read_existing_target(path):
    """Return (content_id, status) preserved from an existing target file."""
    if not path.exists():
        return None, None
    try:
        post = frontmatter.loads(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise CommandError(
            f'Cannot parse existing target {path}: {exc}'
        ) from exc
    content_id = post.metadata.get('content_id')
    try:
        content_id = str(uuid.UUID(content_id))
    except (TypeError, ValueError, AttributeError):
        content_id = None
    status = post.metadata.get('status')
    return content_id, status


class Command(BaseCommand):
    help = __doc__

    def create_parser(self, *args, **kwargs):
        parser = super().create_parser(*args, **kwargs)
        parser.formatter_class = RawDescriptionHelpFormatter
        return parser

    def add_arguments(self, parser):
        parser.add_argument(
            '--from-disk',
            required=True,
            help='Path to a local checkout of the field guide repo.',
        )
        parser.add_argument(
            '--content-repo',
            default=str(Path('~/git/ai-shipping-labs-content').expanduser()),
            help='Path to the content repo checkout to write into '
                 '(default: ~/git/ai-shipping-labs-content).',
        )
        parser.add_argument(
            '--write',
            action='store_true',
            help='Write the converted files. Without this flag the '
                 'command is a dry run and prints the summary only.',
        )

    def handle(self, *args, **options):
        guide_dir = Path(options['from_disk']).expanduser().resolve()
        content_repo = Path(options['content_repo']).expanduser().resolve()
        write = options['write']

        questions_dir = guide_dir / 'interview' / 'questions'
        target_dir = content_repo / 'interview-questions'
        if not questions_dir.is_dir():
            raise CommandError(
                f'No interview/questions/ directory under {guide_dir}. '
                'Pass --from-disk pointing at a field-guide checkout.'
            )
        if not target_dir.is_dir():
            raise CommandError(
                f'No interview-questions/ directory under {content_repo}. '
                'Pass --content-repo pointing at a content repo checkout.'
            )

        skipped = []
        for path in sorted(questions_dir.glob('*.md')):
            slug = GUIDE_FILE_TO_SLUG.get(path.name)
            if slug is None:
                reason = (
                    'aggregate file, not a category bank'
                    if path.name == AGGREGATE_GUIDE_FILE
                    else 'not a known guide question file'
                )
                skipped.append(f'{path.name} ({reason})')
                continue
            self._convert_one(path, slug, target_dir, write)

        if skipped:
            self.stdout.write('Skipped: ' + '; '.join(skipped))
        if not write:
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    'Dry run: no files written. Re-run with --write to '
                    'update the content repo.'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    'Wrote the files above. Review the diff, commit and '
                    'push the content repo, then run the content sync.'
                )
            )

    def _convert_one(self, source_path, slug, target_dir, write):
        # Fresh instance per file: section-id dedupe must not leak across banks.
        converter = GuideQuestionConverter()
        text = source_path.read_text(encoding='utf-8')
        meta, body = converter.convert(text)

        target_path = target_dir / f'{slug}.md'
        existing_id, existing_status = _read_existing_target(target_path)
        content_id = existing_id or str(uuid.uuid4())
        if existing_id is None:
            status = NEW_FILE_STATUS
            status_note = f'defaulted to {NEW_FILE_STATUS} (new file)'
        elif existing_status:
            status = existing_status
            status_note = f'kept "{existing_status}"'
        else:
            status = None
            status_note = 'kept (none)'

        frontmatter_meta = {
            'content_id': content_id,
            'description': meta['description'],
            'sections': meta['sections'],
            'title': meta['title'],
        }
        if status:
            frontmatter_meta['status'] = status

        section_count = len(meta['sections'])
        question_count = sum(
            len(section['qa']) for section in meta['sections']
        )
        rel_target = os.path.join('interview-questions', target_path.name)
        self.stdout.write(
            f'{source_path.name} -> {rel_target}: '
            f'{section_count} sections, {question_count} questions '
            f'({status_note})'
        )

        if write:
            yaml_text = yaml.safe_dump(
                frontmatter_meta,
                sort_keys=True,
                allow_unicode=True,
                default_flow_style=False,
                width=80,
            )
            target_path.write_text(
                f'---\n{yaml_text}---{body}', encoding='utf-8',
            )
