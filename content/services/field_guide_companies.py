"""One-way converter: field-guide company interview YAMLs to content repo.

The field guide repo keeps structured interview-process data in
``interview/data/job-descriptions/``: one YAML per scraped job posting,
with the posting's ``company``, ``role``, ``source_files``, optional
``process_summary``, ``steps`` and ``notable``. Several postings can belong
to the same company (Nearform currently appears twice). The site surface
wanted from this data (issue #1712) is one page per company, fed through
the normal content-sync pipeline — the guide itself is never a sync source
(same rule as #1703/#1705).

This module converts the per-posting YAMLs into per-company
``interview-companies/<slug>.yaml`` files in the exact shape the
``CompanyInterviewsParser`` sync family consumes, so a refresh is just:
convert with ``--write``, review the diff, commit and push the content
repo, and let the normal sync pipeline update the database.

Design constraints (inherited from #1705):

- Strictly one-way file transform. No database access, no git commands.
  The operator reviews the diff; the existing webhook sync does the DB
  upsert.
- Postings for the same company (identical ``company`` string) merge into
  one target file: roles are the ordered distinct union, ``source_files``
  the sorted union, and ``steps`` / ``process_summary`` / ``notable``
  come from the postings in filename order (first non-empty wins for
  ``steps``; distinct non-empty texts are joined for the prose fields).
  Slugs are the slugified company name; a collision between two distinct
  company names is an error, never a silent overwrite.
- ``status``, ``content_id`` and ``required_level`` are reused from the
  existing target file when one exists, so repeated refreshes are
  byte-identical (idempotent) and never clobber an operator edit. New
  files default to ``status: published`` — unlike the #1705 question
  banks there is no coming-soon staging state: the reviewed content-repo
  diff is the publish gate.
- ``source_files`` (the raw scrape filenames) are preserved verbatim in
  the emitted data; provenance on the site renders as one link to the
  public ``interview/data/job-descriptions/`` path, never the internal
  scrape names.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import yaml

from content.access import LEVEL_BASIC
from content.services.field_guide_interview import (
    FIELD_GUIDE_REPO_URL,
    slugify,
)

#: GitHub URL rendered as the single provenance link on company pages.
GUIDE_PROVENANCE_URL = f'{FIELD_GUIDE_REPO_URL}/tree/main/interview/data/job-descriptions'

#: Subpath of the per-posting company YAMLs inside a field-guide checkout.
GUIDE_COMPANIES_SUBPATH = 'interview/data/job-descriptions'

#: Directory inside the content repo that receives the emitted files.
TARGET_SUBDIR = 'interview-companies'

#: Default target checkout (``--content-repo``).
DEFAULT_CONTENT_REPO = '~/git/ai-shipping-labs-content'

#: Frontmatter ``status`` for freshly created target files.
NEW_FILE_STATUS = 'published'

#: Frontmatter ``required_level`` for freshly created target files:
#: detail pages are gated at Basic (issue #1712 decision).
NEW_FILE_REQUIRED_LEVEL = LEVEL_BASIC


def ensure_guide_checkout(guide_root):
    """Validate that ``guide_root`` is a field-guide checkout."""
    companies_dir = os.path.join(guide_root, GUIDE_COMPANIES_SUBPATH)
    if not os.path.isdir(companies_dir):
        raise NotADirectoryError(
            f'No {GUIDE_COMPANIES_SUBPATH}/ under {guide_root}. '
            'Pass the root of an ai-engineering-field-guide checkout.'
        )


def load_company_docs(guide_root):
    """Parse every job-description YAML under the guide's companies dir.

    Returns ``{company: [(rel_path, data), ...]}`` with the per-company
    lists ordered by source filename. Raises ``ValueError`` for a file
    that is not a mapping or carries no ``company`` field — the reviewed
    diff must never silently drop a posting.
    """
    companies_dir = os.path.join(guide_root, GUIDE_COMPANIES_SUBPATH)
    grouped: dict[str, list[tuple[str, dict]]] = {}
    for name in sorted(os.listdir(companies_dir)):
        if not name.endswith(('.yaml', '.yml')):
            continue
        rel_path = f'{GUIDE_COMPANIES_SUBPATH}/{name}'
        with open(os.path.join(companies_dir, name), encoding='utf-8') as handle:
            data = yaml.safe_load(handle)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(
                f'Invalid YAML in {rel_path}: expected a mapping, got '
                f'{type(data).__name__}'
            )
        company = data.get('company')
        if not company:
            raise ValueError(f'Missing required field "company" in {rel_path}')
        grouped.setdefault(company, []).append((rel_path, data))
    if not grouped:
        raise ValueError(
            f'No company YAML files found in {companies_dir}.'
        )
    return grouped


@dataclass
class CompanyRecord:
    """Merged interview data for one company (before target-file merge)."""

    company: str
    roles: list[str]
    process_summary: str
    steps: list[dict]
    notable: str
    source_files: list[str]
    source_rels: list[str]

    @property
    def slug(self):
        return company_slug(self.company)


def company_slug(company):
    """Stable page id for a company name (shares the #1705 slug rules)."""
    return slugify(company)


def company_entries(guide_root):
    """Return one ``CompanyRecord`` per distinct company, sorted by slug.

    Two distinct company names that slugify to the same id raise
    ``ValueError`` instead of silently merging into one page.
    """
    by_slug: dict[str, CompanyRecord] = {}
    for company, docs in load_company_docs(guide_root).items():
        slug = company_slug(company)
        record = merge_company(company, docs)
        existing = by_slug.get(slug)
        if existing is not None and existing.company != company:
            raise ValueError(
                f'Slug collision: companies "{existing.company}" and '
                f'"{company}" both map to interview-companies/{slug}.yaml. '
                'Rename one of them before converting.'
            )
        by_slug[slug] = record
    return [by_slug[slug] for slug in sorted(by_slug)]


def _distinct_join(values):
    """Join distinct non-empty strings in order (prose merge rule)."""
    parts: list[str] = []
    for value in values:
        text = (value or '').strip()
        if text and text not in parts:
            parts.append(text)
    return '\n\n'.join(parts)


def merge_company(company, docs):
    """Merge one company's postings into a single :class:`CompanyRecord`.

    ``docs`` is the per-company ``[(rel_path, data), ...]`` list from
    :func:`load_company_docs` (filename-ordered). ``steps`` come from the
    first posting that has them; the prose fields join the distinct
    non-empty texts so nothing a posting said is silently dropped.
    """
    roles: list[str] = []
    source_files: set[str] = set()
    summaries: list[str] = []
    notables: list[str] = []
    steps: list[dict] = []
    source_rels: list[str] = []
    for rel_path, data in docs:
        source_rels.append(rel_path)
        role = (data.get('role') or '').strip()
        if role and role not in roles:
            roles.append(role)
        source_files.update(data.get('source_files') or [])
        summaries.append(data.get('process_summary'))
        notables.append(data.get('notable'))
        if not steps:
            steps = list(data.get('steps') or [])
    return CompanyRecord(
        company=company,
        roles=roles,
        process_summary=_distinct_join(summaries),
        steps=steps,
        notable=_distinct_join(notables),
        source_files=sorted(source_files),
        source_rels=source_rels,
    )


def build_file(record, *, existing=None, new_uuid=None):
    """Assemble the emitted YAML text for one company.

    ``existing`` is the parsed YAML of the current target file (or None).
    ``content_id``, ``status`` and ``required_level`` are reused from it
    when present; new files need ``new_uuid`` and default to
    ``status: published`` / ``required_level:`` :data:`LEVEL_BASIC`.
    """
    existing = existing or {}
    content_id = existing.get('content_id') or new_uuid
    if not content_id:
        raise ValueError('content_id required when no existing target file')
    status = existing.get('status')
    if not status and not existing:
        status = NEW_FILE_STATUS
    required_level = existing.get('required_level')
    if required_level is None:
        required_level = (
            NEW_FILE_REQUIRED_LEVEL if not existing else None
        )

    metadata: dict = {
        'company': record.company,
        'content_id': content_id,
        'roles': record.roles,
        'source_files': record.source_files,
    }
    if record.process_summary:
        metadata['process_summary'] = record.process_summary
    if record.steps:
        metadata['steps'] = record.steps
    if record.notable:
        metadata['notable'] = record.notable
    if status:
        metadata['status'] = status
    if required_level is not None:
        metadata['required_level'] = required_level

    return yaml.safe_dump(
        metadata,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=80,
    )


def read_existing(target_path):
    """Parse the current target file's YAML, or None if absent."""
    if not os.path.isfile(target_path):
        return None
    with open(target_path, encoding='utf-8') as handle:
        data = yaml.safe_load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f'Invalid YAML in {target_path}: expected a mapping, got '
            f'{type(data).__name__}'
        )
    return data


@dataclass
class RefreshSummary:
    """Per-company outcome of one converter run."""

    company: str
    slug: str
    source_rels: list[str]
    target_path: str
    role_count: int
    step_count: int
    status: str
    existed: bool
    written: bool


def refresh(guide_root, content_repo, *, write=False):
    """Convert every company's postings into the content repo.

    With ``write=False`` nothing is written to disk (dry run). Returns one
    :class:`RefreshSummary` per company, sorted by slug.
    """
    ensure_guide_checkout(guide_root)
    summaries = []
    target_dir = os.path.join(content_repo, TARGET_SUBDIR)
    if write:
        os.makedirs(target_dir, exist_ok=True)
    for record in company_entries(guide_root):
        target_path = os.path.join(target_dir, f'{record.slug}.yaml')
        existing = read_existing(target_path)
        existed = existing is not None
        # Generated even on dry runs: a dry run must be able to render the
        # full file text for a brand-new target without writing anything.
        new_uuid = str(uuid.uuid4()) if not existed else None
        text = build_file(record, existing=existing, new_uuid=new_uuid)
        written = False
        if write:
            with open(target_path, 'w', encoding='utf-8') as handle:
                handle.write(text)
            written = True
        summaries.append(RefreshSummary(
            company=record.company,
            slug=record.slug,
            source_rels=list(record.source_rels),
            target_path=target_path,
            role_count=len(record.roles),
            step_count=len(record.steps),
            status=(existing or {}).get('status') or (
                NEW_FILE_STATUS if not existed else ''
            ),
            existed=existed,
            written=written,
        ))
    return summaries
