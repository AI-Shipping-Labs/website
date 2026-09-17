"""One-way converter: AI Engineering Field Guide question banks to content repo.

The six ``interview-questions/*.md`` files in ``AI-Shipping-Labs/content``
are a hand conversion of the field guide's question banks
(``interview/questions/01-theory.md`` .. ``06-home-assignments.md``). The
guide keeps evolving while the site copies drift stale. This module is the
missing upstream step: it converts guide markdown into the exact file shape
the existing ``InterviewQuestionsParser`` already consumes, so a refresh is
just: convert with ``--write``, review the diff, commit and push the content
repo, and let the normal sync pipeline update the database.

Design constraints (issue #1705):

- Strictly one-way file transform. No database access, no git commands.
  The operator reviews the diff; the existing webhook sync (or the content
  repo's ``scripts/sync_production.py``) does the DB upsert.
- The numeric filename prefix is stripped (``01-theory.md`` -> ``theory.md``)
  so refreshed content updates the existing six ``InterviewCategory`` rows
  and public URLs instead of creating duplicates. ``04-ai-system-design.md``
  maps to ``system-design.md`` (the one non-mechanical rename).
- ``status`` is never flipped: an existing target keeps its current
  ``status`` line, new files default to ``coming-soon``. Publishing stays an
  explicit operator edit.
- ``content_id`` is reused from the existing target file when one exists,
  so repeated refreshes are byte-identical (idempotent).
- ``[^footnote]`` markers and footnote definitions are stripped from all
  emitted text; a single provenance line with the guide repo URL is added
  after the ``<!-- after-questions -->`` marker instead.

Question-bank detection is structural with a small blocklist of known
narrative sections (``Format``, ``How to Prepare``, ``Sources``, ...): an
H2 section is a question bank when it is not blocklisted and it contains at
least one top-level bullet, either directly or under an H3. New bank
sections added by monthly guide updates (for example ``June 2026
Additions``) are picked up automatically; anything unexpected shows up in
the reviewed diff.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field

import frontmatter
import yaml

#: GitHub URL of the field-guide repo, used in the emitted provenance line.
FIELD_GUIDE_REPO_URL = 'https://github.com/alexeygrigorev/ai-engineering-field-guide'

#: Subpath of the question banks inside a field-guide checkout.
GUIDE_QUESTIONS_SUBPATH = 'interview/questions'

#: Directory inside the content repo that receives the emitted files.
TARGET_SUBDIR = 'interview-questions'

#: Default target checkout (``--content-repo``).
DEFAULT_CONTENT_REPO = '~/git/ai-shipping-labs-content'

#: Frontmatter ``status`` for freshly created target files.
NEW_FILE_STATUS = 'coming-soon'

#: Body marker that splits page prose from the rendered question lists.
AFTER_QUESTIONS_MARKER = '<!-- after-questions -->'

#: Aggregate question dump in the guide; never becomes a category.
AGGREGATE_GUIDE_FILE = 'questions.md'

#: Renames applied after the numeric prefix is stripped. Only
#: ``04-ai-system-design`` needs one today; any other prefixed file converts
#: to its plain prefix-stripped name.
SLUG_RENAMES = {
    'ai-system-design': 'system-design',
}

#: H2 titles that are guide prose, never question banks, even when they
#: contain bullets (``Format`` lists round shapes, ``How to Prepare`` lists
#: study tips). Matched lowercased after stripping.
NON_BANK_SECTION_TITLES = frozenset({
    'format',
    'how to prepare',
    'sources',
    'coding round formats',
    'what interviewers evaluate',
    'expectations',
})

#: Narrative H2 whose own bullets are comparisons rather than questions, but
#: whose H3 subsections are genuine question banks: the H2 block stays on the
#: page as body prose while the children convert to sections.
PROSE_WITH_QUESTION_CHILDREN = frozenset({
    'ai system design vs system design',
})

# The guide has at least one malformed reference (``[^process-analysis
# [^fonzi-ai]``); after the well-formed refs are stripped this catches the
# dangling ``[^name`` remnant.
_FOOTNOTE_DEF_RE = re.compile(r'^\[\^[A-Za-z0-9_-]+\]:[^\n]*$', re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r'\[\^[A-Za-z0-9_-]+\]')
_FOOTNOTE_DANGLING_RE = re.compile(
    r'\[\^[A-Za-z0-9_-]+(?=\s|$)', re.MULTILINE,
)
_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]*\)')
_SLUG_CLEAN_RE = re.compile(r'[^a-z0-9]+')
_PREFIX_RE = re.compile(r'^\d+-(.+)\.md$')


def slugify(text):
    """Stable section id: lowercase, non-alphanumerics collapsed to dashes."""
    return _SLUG_CLEAN_RE.sub('-', text.lower()).strip('-')


def strip_footnotes(text):
    """Remove footnote definitions, reference markers, and leftover gaps."""
    text = _FOOTNOTE_DEF_RE.sub('', text)
    text = _FOOTNOTE_REF_RE.sub('', text)
    text = _FOOTNOTE_DANGLING_RE.sub('', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r' +\n', '\n', text)
    return text


def source_files(guide_root):
    """Map guide question files to target slugs.

    Returns a sorted list of ``(source_rel_path, target_slug)`` tuples. Only
    files carrying the guide's numeric prefix convert (the aggregate
    ``questions.md`` never becomes a category), and the prefix is stripped
    so refreshed content lands on the existing slugs.
    """
    questions_dir = os.path.join(guide_root, GUIDE_QUESTIONS_SUBPATH)
    mapping = []
    for name in sorted(os.listdir(questions_dir)):
        match = _PREFIX_RE.match(name)
        if not match:
            continue
        base = match.group(1)
        slug = SLUG_RENAMES.get(base, base)
        mapping.append((f'{GUIDE_QUESTIONS_SUBPATH}/{name}', slug))
    return mapping


def unmapped_files(guide_root):
    """Return the guide question files the converter deliberately skips."""
    questions_dir = os.path.join(guide_root, GUIDE_QUESTIONS_SUBPATH)
    mapped = {os.path.basename(rel) for rel, _ in source_files(guide_root)}
    return sorted(
        name for name in os.listdir(questions_dir)
        if name.endswith('.md') and name not in mapped
    )


def ensure_guide_checkout(guide_root):
    """Validate that ``guide_root`` is a field-guide checkout."""
    questions_dir = os.path.join(guide_root, GUIDE_QUESTIONS_SUBPATH)
    if not os.path.isdir(questions_dir):
        raise NotADirectoryError(
            f'No {GUIDE_QUESTIONS_SUBPATH}/ under {guide_root}. '
            'Pass the root of an ai-engineering-field-guide checkout.'
        )


@dataclass
class _Chunk:
    """Lines under one heading, subdivided by the next heading level down."""

    title: str | None
    lines: list[str] = field(default_factory=list)


def _split_headings(lines, prefix):
    """Split ``lines`` into chunks at ``prefix`` headings (e.g. '## ')."""
    chunks: list[_Chunk] = [_Chunk(None)]
    for line in lines:
        if line.startswith(prefix):
            chunks.append(_Chunk(line[len(prefix):].strip()))
        else:
            chunks[-1].lines.append(line)
    return chunks


def _is_bullet(line):
    return line.startswith('- ')


@dataclass
class _QuestionGroup:
    """A heading plus the question bullets and intro prose under it."""

    title: str
    intro: str
    questions: list[str]

    @property
    def has_questions(self):
        return bool(self.questions)


def _extract_group(title, lines):
    """Build the question group for one heading's lines.

    Top-level ``- `` bullets become questions; an indented line continues
    the previous bullet; prose before the first bullet becomes the intro.
    Bare list lead-ins (``Examples:``, ``Types:``) are dropped, and prose
    after the first bullet (quotes, asides) is dropped.
    """
    intro_parts: list[str] = []
    questions: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if _is_bullet(line):
            questions.append(strip_footnotes(line[2:].strip()))
        elif line.startswith((' ', '\t')) and questions:
            questions[-1] += ' ' + strip_footnotes(line.strip())
        elif not questions and line.strip():
            text = strip_footnotes(line.strip())
            if text.endswith(':') and len(text.split()) <= 4:
                continue
            intro_parts.append(text)
    intro = ' '.join(part for part in intro_parts if part).strip()
    intro = intro.rstrip(':').strip()
    questions = [q.strip() for q in questions if q.strip()]
    return _QuestionGroup(title=title or '', intro=intro, questions=questions)


def _chunk_text(title, lines):
    """Render one H2 block back to page-body markdown, or '' if no content.

    The heading itself is dropped when the block has no body text left
    (headings-only blocks carry no information).
    """
    text = strip_footnotes('\n'.join(lines)).strip()
    if not text:
        return ''
    text = re.sub(r'\n{3,}', '\n\n', text)
    return f'## {title}\n\n{text}' if title else text


def _make_section(group, seen_ids):
    """Build one ``sections[]`` entry with a stable, deduplicated slug id."""
    base_id = slugify(group.title) or 'section'
    section_id = base_id
    suffix = 2
    while section_id in seen_ids:
        section_id = f'{base_id}-{suffix}'
        suffix += 1
    seen_ids.add(section_id)
    return {
        'id': section_id,
        'title': group.title,
        'intro': group.intro,
        'qa': [{'question': question} for question in group.questions],
    }


@dataclass
class ConvertedSource:
    """Result of converting one guide file (before target-file merge)."""

    title: str
    description: str
    sections: list[dict]
    body: str

    @property
    def question_count(self):
        return sum(len(s['qa']) for s in self.sections)


def _h1_block(lines):
    """Extract (title, intro_lines) from the preamble before the first H2.

    Blank lines are kept so paragraphs stay separated in the body and the
    description can stop at the end of the first paragraph.
    """
    title = ''
    intro_lines: list[str] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith('# ') and not title:
            title = strip_footnotes(line[2:].strip())
        elif line and not line.startswith('#'):
            intro_lines.append(line)
        elif not line and intro_lines:
            intro_lines.append(line)
    return title, intro_lines


def _description(intro_lines):
    """Frontmatter description: the first intro paragraph, links stripped."""
    paragraph: list[str] = []
    started = False
    for raw in intro_lines:
        stripped = raw.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith(('#', '- ')):
            break
        started = True
        paragraph.append(_MD_LINK_RE.sub(r'\1', strip_footnotes(stripped)))
    return ' '.join(paragraph).strip()


def _intro_text(intro_lines):
    """Render the H1-area paragraphs as page-body markdown."""
    text = strip_footnotes('\n'.join(intro_lines)).strip()
    return re.sub(r'\n{3,}', '\n\n', text)


def convert_source(text):
    """Convert one guide question file to a :class:`ConvertedSource`.

    The guide file has no frontmatter: the H1 becomes ``title``, the first
    intro paragraph becomes ``description``, question groups become
    ``sections``, and every other piece of prose is kept as the markdown
    body (which renders above the question lists on the site).
    """
    text = text.replace('\r\n', '\n')
    h2_chunks = _split_headings(text.split('\n'), '## ')
    title, intro_lines = _h1_block(h2_chunks[0].lines)

    sections: list[dict] = []
    seen_ids: set[str] = set()
    prose_chunks: list[str] = []

    for chunk in h2_chunks[1:]:
        key = (chunk.title or '').lower()
        if key == 'sources':
            # Footnote definitions live here; replaced by the provenance
            # line after <!-- after-questions -->.
            continue
        h3_chunks = _split_headings(chunk.lines, '### ')
        preamble = _extract_group(chunk.title, h3_chunks[0].lines)
        groups = [_extract_group(h3.title, h3.lines) for h3 in h3_chunks[1:]]
        has_banks = (
            preamble.has_questions or any(g.has_questions for g in groups)
        )

        if key in NON_BANK_SECTION_TITLES or not has_banks:
            prose_chunks.append(_chunk_text(chunk.title, chunk.lines))
            continue
        if key in PROSE_WITH_QUESTION_CHILDREN or not preamble.has_questions:
            # Narrative intro text stays on the page above the lists.
            block = _chunk_text(chunk.title, h3_chunks[0].lines)
            if block:
                prose_chunks.append(block)
        else:
            sections.append(_make_section(preamble, seen_ids))
        for group in groups:
            if group.has_questions:
                sections.append(_make_section(group, seen_ids))
            elif group.intro:
                prose_chunks.append(f'### {group.title}\n\n{group.intro}')

    body = '\n\n'.join(
        part for part in (
            _intro_text(intro_lines),
            *[chunk for chunk in prose_chunks if chunk],
        ) if part
    )
    return ConvertedSource(
        title=title,
        description=_description(intro_lines),
        sections=sections,
        body=body,
    )


def build_file(converted, *, source_rel, existing=None, new_uuid=None):
    """Assemble the emitted file text for one converted source.

    ``existing`` is the parsed frontmatter of the current target file (or
    None). ``content_id`` and ``status`` are reused from it when present;
    new files default to ``status: coming-soon`` and need ``new_uuid``.
    """
    existing = existing or {}
    metadata: dict = {
        'content_id': existing.get('content_id') or new_uuid,
        'description': converted.description,
        'sections': converted.sections,
        'title': converted.title,
    }
    if not metadata['content_id']:
        raise ValueError('content_id required when no existing target file')
    status = existing.get('status')
    if status:
        metadata['status'] = status
    elif not existing:
        metadata['status'] = NEW_FILE_STATUS

    provenance = (
        f'Source: [AI Engineering Field Guide]({FIELD_GUIDE_REPO_URL})'
        f' ({source_rel})'
    )
    body = converted.body
    if body:
        body += '\n\n'
    body += f'{AFTER_QUESTIONS_MARKER}\n\n{provenance}\n'

    yaml_text = yaml.safe_dump(
        metadata,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=80,
    )
    return f'---\n{yaml_text}---\n\n{body}'


def read_existing(target_path):
    """Parse the current target file's frontmatter, or None if absent."""
    if not os.path.isfile(target_path):
        return None
    post = frontmatter.load(target_path)
    return dict(post.metadata)


@dataclass
class RefreshSummary:
    """Per-file outcome of one converter run."""

    source_rel: str
    slug: str
    target_path: str
    section_count: int
    question_count: int
    status: str
    existed: bool
    written: bool


def refresh(guide_root, content_repo, *, write=False):
    """Convert every guide question bank into the content repo.

    With ``write=False`` nothing is written to disk (dry run). Returns one
    :class:`RefreshSummary` per converted source file.
    """
    summaries = []
    target_dir = os.path.join(content_repo, TARGET_SUBDIR)
    if write:
        os.makedirs(target_dir, exist_ok=True)
    for source_rel, slug in source_files(guide_root):
        source_path = os.path.join(guide_root, source_rel)
        with open(source_path, encoding='utf-8') as handle:
            converted = convert_source(handle.read())
        target_path = os.path.join(target_dir, f'{slug}.md')
        existing = read_existing(target_path)
        existed = existing is not None
        # Generated even on dry runs: a dry run must be able to render the
        # full file text for a brand-new target without writing anything.
        new_uuid = str(uuid.uuid4()) if not existed else None
        text = build_file(
            converted,
            source_rel=source_rel,
            existing=existing,
            new_uuid=new_uuid,
        )
        written = False
        if write:
            with open(target_path, 'w', encoding='utf-8') as handle:
                handle.write(text)
            written = True
        summaries.append(RefreshSummary(
            source_rel=source_rel,
            slug=slug,
            target_path=target_path,
            section_count=len(converted.sections),
            question_count=converted.question_count,
            status=(existing or {}).get('status') or (
                NEW_FILE_STATUS if not existed else ''
            ),
            existed=existed,
            written=written,
        ))
    return summaries
