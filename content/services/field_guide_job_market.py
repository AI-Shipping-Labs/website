"""One-way converter: field-guide job-market scrapes to a data article.

The field-guide repo holds ``job-market/data_structured/<date>/*.yaml``:
monthly cross-sections of builtin.com AI-engineering postings (company,
position title, ``ai_type``, categorized ``skills``, ``meta.locations``).
This module converts those scrapes into the small aggregates the
``/blog/ai-engineering-job-market/`` data article renders, and writes the
exact file shape the existing ``ArticlesParser`` sync already consumes:
one markdown file whose ``data:`` frontmatter carries the aggregates plus
two ``widgets/`` HTML templates referenced through
``<!-- include:widgets/... -->`` markers (the include mechanism passes
``data: article.data_json`` to widgets at sync time).

Design constraints (issue #1714), mirroring the #1705 converter:

- Strictly one-way file transform. No database access, no git commands.
  The operator reviews the diff in the content repo; the existing webhook
  sync (or the content repo's ``scripts/sync_production.py``) does the
  DB upsert.
- Aggregates only: no posting YAML is copied into the content repo. This
  converter is the only analytics engine.
- Shares are within-scrape percentages of postings (the market-wiki
  methodology). There is no cross-month deduplication: every scrape is an
  independent cross-section.
- ``content_id`` is reused from an existing target file, so repeated
  refreshes are byte-identical (idempotent). A fresh target gets a minted
  UUID on the first ``--write``.

Refresh loop:

1. On a clean content-repo checkout, run with ``--write``::

       uv run python manage.py sync_field_guide_job_market \\
           --from-disk ~/git/ai-engineering-field-guide --write

2. Review the diff in the content repo, commit and push.
3. Let the webhook sync run, or trigger it via the existing authenticated
   sync API.
"""

from __future__ import annotations

import os
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass

import frontmatter
import yaml

#: GitHub URL of the field-guide repo's job-market directory.
GUIDE_JOB_MARKET_URL = (
    'https://github.com/alexeygrigorev/'
    'ai-engineering-field-guide/tree/main/job-market'
)

#: Subpath of the structured scrapes inside a field-guide checkout.
GUIDE_DATA_SUBPATH = 'job-market/data_structured'

#: Directory inside the content repo that receives the generated article.
ARTICLE_TARGET_DIR = 'blog/ai-engineering-job-market'

#: File name of the generated article inside its directory.
ARTICLE_FILE_NAME = 'index.md'

#: Default target checkout (``--content-repo``).
DEFAULT_CONTENT_REPO = '~/git/ai-shipping-labs-content'

#: Skill categories produced by the guide's extraction pipeline, in the
#: fixed order used by the market-wiki generator, with display labels.
SKILL_CATEGORIES = (
    ('genai', 'GenAI'),
    ('languages', 'Languages'),
    ('cloud', 'Cloud'),
    ('ml', 'ML'),
    ('databases', 'Databases'),
    ('ops', 'Ops'),
    ('data', 'Data'),
    ('web', 'Web'),
    ('domains', 'Domains'),
    ('other', 'Other'),
)

#: Top-N truncations from the issue's aggregate table.
SKILL_DEMAND_TOP = 10
TREND_TOP = {'genai': 8, 'languages': 5}
TOP_COMPANIES = 15
TOP_LOCATIONS = 10

#: Known role types with display labels; ``unknown`` covers postings whose
#: ``ai_type.type`` is missing or blank. Unexpected new values are kept and
#: appended after the known ones so data is never silently dropped.
ROLE_LABELS = {
    'ai-first': 'AI-first',
    'ai-support': 'AI-support',
    'ml-first': 'ML-first',
    'unknown': 'Unclassified',
}
ROLE_ORDER = ('ai-first', 'ai-support', 'ml-first', 'unknown')

MONTH_ABBR = {
    1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
    7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}
MONTH_FULL = {
    1: 'January', 2: 'February', 3: 'March', 4: 'April', 5: 'May',
    6: 'June', 7: 'July', 8: 'August', 9: 'September', 10: 'October',
    11: 'November', 12: 'December',
}

#: Widget templates copied into the content repo (rendered there by the
#: article include mechanism at sync time).
WIDGET_TEMPLATE_DIR = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'field_guide_job_market_widgets')
)
SKILLS_WIDGET_NAME = 'job_market_skills.html'
TRENDS_WIDGET_NAME = 'job_market_trends.html'
SKILLS_WIDGET_TARGET = f'widgets/{SKILLS_WIDGET_NAME}'
TRENDS_WIDGET_TARGET = f'widgets/{TRENDS_WIDGET_NAME}'


def pct(count, total):
    """Within-scrape share of postings, one decimal place."""
    if not total:
        return 0.0
    return round(count / total * 100, 1)


def _split_month(month):
    """Split an ISO scrape directory name into (year, month, day) ints."""
    year, mo, day = month.split('-')
    return int(year), int(mo), int(day)


def month_label(month):
    """Compact scrape label used for table columns, e.g. ``Feb 4``."""
    _, mo, day = _split_month(month)
    return f'{MONTH_ABBR[mo]} {day}'


def full_month_label(month):
    """Full scrape label, e.g. ``Feb 4, 2026``."""
    year, mo, day = _split_month(month)
    return f'{MONTH_ABBR[mo]} {day}, {year}'


def data_through_label(month):
    """Month-granular freshness stamp, e.g. ``August 2026``."""
    year, mo, _ = _split_month(month)
    return f'{MONTH_FULL[mo]} {year}'


def ensure_guide_checkout(guide_root):
    """Validate that ``guide_root`` is a field-guide checkout with scrapes."""
    data_dir = os.path.join(guide_root, GUIDE_DATA_SUBPATH)
    if not os.path.isdir(data_dir):
        raise NotADirectoryError(
            f'No {GUIDE_DATA_SUBPATH}/ under {guide_root}. '
            'Pass the root of an ai-engineering-field-guide checkout.'
        )


def scrape_months(guide_root):
    """Return the sorted ISO-date scrape directory names under the data dir."""
    data_dir = os.path.join(guide_root, GUIDE_DATA_SUBPATH)
    months = []
    for name in os.listdir(data_dir):
        path = os.path.join(data_dir, name)
        if not os.path.isdir(path):
            continue
        parts = name.split('-')
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            continue
        months.append(name)
    return sorted(months)


def _cities_from_meta(meta):
    """City-level locations for one posting, deduplicated and in order.

    ``meta.locations`` is the source field; a posting without the list
    falls back to its singular ``meta.location``. Entries are
    ``City, COUNTRY`` strings; single-part entries are bare country codes
    and are skipped (city level only, as the market-wiki generator does).
    """
    raw_locations = meta.get('locations')
    if not raw_locations:
        single = str(meta.get('location') or '').strip()
        raw_locations = [single] if single else []
    cities = []
    for entry in raw_locations:
        text = str(entry).strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split(',')]
        if len(parts) >= 2 and parts[0] and parts[0] not in cities:
            cities.append(parts[0])
    return cities


def _skill_sets(position):
    """Per-category deduplicated skill names for one posting."""
    raw = position.get('skills') or {}
    skills = {}
    for category, _label in SKILL_CATEGORIES:
        names = []
        for skill in raw.get(category) or []:
            name = str(skill).strip()
            if name and name not in names:
                names.append(name)
        skills[category] = names
    return skills


def _posting_from_doc(doc, month):
    """Normalize one posting YAML into the flat record the aggregator uses."""
    if not isinstance(doc, dict):
        raise ValueError(
            f'Posting under {GUIDE_DATA_SUBPATH}/{month}/ is not a YAML '
            'mapping')
    position = doc.get('position') or {}
    company = doc.get('company') or {}
    meta = doc.get('meta') or {}
    ai_type = str((position.get('ai_type') or {}).get('type') or '').strip()
    return {
        'month': month,
        'skills': _skill_sets(position),
        'ai_type': ai_type or 'unknown',
        'company': str(company.get('name') or '').strip(),
        'cities': _cities_from_meta(meta),
    }


def load_postings(guide_root, months):
    """Read every posting YAML into ``{month: [records]}``.

    Files are read in sorted order and parsing errors propagate: the
    dataset is machine-generated and a silent skip would skew shares.
    """
    data_dir = os.path.join(guide_root, GUIDE_DATA_SUBPATH)
    postings = {}
    for month in months:
        records = []
        month_dir = os.path.join(data_dir, month)
        for name in sorted(os.listdir(month_dir)):
            if not name.endswith('.yaml'):
                continue
            path = os.path.join(month_dir, name)
            with open(path, encoding='utf-8') as handle:
                doc = yaml.safe_load(handle)
            records.append(_posting_from_doc(doc, month))
        postings[month] = records
    return postings


def _top(counts, limit):
    """Highest-count items, count descending then name for determinism."""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def compute_aggregates(postings_by_month, months):
    """Build the ``data:`` frontmatter payload from normalized postings.

    All six aggregates from the issue's table: skill demand (top skills
    per category with shares of the latest scrape), skill trends (top
    genai/languages skills per scrape month), role mix per ``ai_type``
    per month, top companies, top locations, and dataset provenance.
    """
    latest = months[-1]
    latest_records = postings_by_month[latest]
    latest_total = len(latest_records)

    # Skill demand: top skills per category in the latest scrape.
    skill_demand = []
    for category, label in SKILL_CATEGORIES:
        counts: Counter = Counter()
        for record in latest_records:
            for name in record['skills'][category]:
                counts[name] += 1
        if not counts:
            continue
        skill_demand.append({
            'category': category,
            'label': label,
            'skills': [
                {'name': name, 'count': count, 'pct': pct(count, latest_total)}
                for name, count in _top(counts, SKILL_DEMAND_TOP)
            ],
        })

    # Skill trends: top skills per category by total mentions across all
    # scrapes (the market-wiki selection), share of each scrape's postings.
    trend_groups = []
    for category in ('genai', 'languages'):
        totals: Counter = Counter()
        per_month = defaultdict(Counter)
        for month in months:
            for record in postings_by_month[month]:
                for name in record['skills'][category]:
                    totals[name] += 1
                    per_month[name][month] += 1
        if not totals:
            continue
        label = dict(SKILL_CATEGORIES)[category]
        trend_groups.append({
            'category': category,
            'label': label,
            'skills': [
                {
                    'name': name,
                    'shares': [
                        pct(per_month[name].get(month, 0),
                            len(postings_by_month[month]))
                        for month in months
                    ],
                }
                for name, _total in _top(totals, TREND_TOP[category])
            ],
        })
    skill_trends = {
        'months': list(months),
        'month_labels': [month_label(month) for month in months],
        'groups': trend_groups,
    }

    # Role mix: share of postings per ai_type per scrape month.
    role_counts = {
        month: Counter(record['ai_type']
                       for record in postings_by_month[month])
        for month in months
    }
    seen_roles = {role for counts in role_counts.values() for role in counts}
    ordered_roles = [role for role in ROLE_ORDER if role in seen_roles]
    ordered_roles += sorted(seen_roles - set(ROLE_ORDER))
    role_mix = {
        'months': list(months),
        'month_labels': [month_label(month) for month in months],
        'totals': [len(postings_by_month[month]) for month in months],
        'types': [
            {
                'type': role,
                'label': ROLE_LABELS.get(role, role.replace('-', ' ').title()),
                'shares': [
                    pct(role_counts[month].get(role, 0),
                        len(postings_by_month[month]))
                    for month in months
                ],
            }
            for role in ordered_roles
        ],
    }

    # Top companies: latest scrape.
    company_counts: Counter = Counter(
        record['company'] for record in latest_records if record['company'])
    companies = [
        {'name': name, 'count': count, 'pct': pct(count, latest_total)}
        for name, count in _top(company_counts, TOP_COMPANIES)
    ]

    # Geography: city-level locations, latest scrape. A posting listing
    # several cities counts once per city, so shares can sum above 100%.
    city_counts: Counter = Counter()
    for record in latest_records:
        for city in record['cities']:
            city_counts[city] += 1
    locations = [
        {'name': name, 'count': count, 'pct': pct(count, latest_total)}
        for name, count in _top(city_counts, TOP_LOCATIONS)
    ]

    provenance = {
        'source_dir': GUIDE_DATA_SUBPATH,
        'guide_url': GUIDE_JOB_MARKET_URL,
        'scrapes': [
            {
                'date': month,
                'label': month_label(month),
                'postings': len(postings_by_month[month]),
            }
            for month in months
        ],
        'scrape_count': len(months),
        'total_postings': sum(len(postings_by_month[m]) for m in months),
        'window': f'{full_month_label(months[0])} to '
                  f'{full_month_label(months[-1])}',
        'latest_scrape': latest,
        'latest_postings': latest_total,
        'data_through': data_through_label(latest),
    }

    return {
        'companies': companies,
        'locations': locations,
        'provenance': provenance,
        'role_mix': role_mix,
        'skill_demand': skill_demand,
        'skill_trends': skill_trends,
    }


def _markdown_table(headers, rows):
    """Render a pipe table; rows are lists of pre-formatted cells.

    Pipe characters inside cells are escaped: the dataset contains
    employer names like ``Holiday Channel | My Holiday World``, and an
    unescaped pipe would split the row.
    """
    def cell(value):
        return str(value).replace('|', '\\|')

    lines = [
        '| ' + ' | '.join(cell(header) for header in headers) + ' |',
        '| ' + ' | '.join(
            '---:' if alignment == 'right' else '---'
            for alignment in ('left', *(['right'] * (len(headers) - 1)))
        ) + ' |',
    ]
    lines.extend(
        '| ' + ' | '.join(cell(value) for value in cells) + ' |'
        for cells in rows)
    return '\n'.join(lines)


def render_article_body(aggregates):
    """Build the article markdown body (no H1; the layout renders the title)."""
    provenance = aggregates['provenance']
    latest_label = full_month_label(provenance['latest_scrape'])

    intro = (
        'The AI engineering job market, measured. This page aggregates '
        f"{provenance['total_postings']:,} AI engineering job postings from "
        f"builtin.com, collected in {provenance['scrape_count']} monthly "
        f"scrapes ({provenance['window']}) and structured in the AI "
        'Engineering Field Guide. Every table below is generated from those '
        'postings: which skills employers ask for, how demand moves month to '
        'month, who is hiring, and where the jobs are.'
    )

    demand_intro = (
        f"Share of postings that mention each skill, per category, in the "
        f"latest scrape ({latest_label}, {provenance['latest_postings']:,} "
        'postings). A skill is counted once per posting, and only when the '
        'description spells it out, so shares are a floor, not a ceiling.'
    )

    trends_intro = (
        'Month-over-month shares for the top skills and role types. Every '
        'scrape is an independent cross-section of builtin.com listings, so '
        'each share is the fraction of that scrape\'s postings - not '
        'headcount and not hiring volume.'
    )

    companies = aggregates['companies']
    companies_intro = (
        f"Employers with the most postings in the latest scrape "
        f"({latest_label}):"
    )
    companies_table = _markdown_table(
        ['Company', 'Postings', 'Share'],
        [
            (company['name'], str(company['count']), f"{company['pct']}%")
            for company in companies
        ],
    ) if companies else 'No company names available in the latest scrape.'

    locations = aggregates['locations']
    locations_intro = (
        f"City-level posting locations in the latest scrape ({latest_label}). "
        'A posting that lists several cities counts once per city, so the '
        'shares can add up to more than 100%.'
    )
    locations_table = _markdown_table(
        ['Location', 'Postings', 'Share'],
        [
            (location['name'], str(location['count']), f"{location['pct']}%")
            for location in locations
        ],
    ) if locations else 'No locations available in the latest scrape.'

    scrapes_table = _markdown_table(
        ['Scrape', 'Postings'],
        [
            *[
                (scrape['label'], str(scrape['postings']))
                for scrape in provenance['scrapes']
            ],
            ('Total', f"{provenance['total_postings']:,}"),
        ],
    )

    methodology = (
        'The raw dataset lives in the guide repo\'s '
        f"[job-market directory]({provenance['guide_url']}); each monthly "
        'directory holds one YAML file per posting with the extracted '
        'skills, role type, and locations. Shares are within-scrape '
        'percentages of postings, rounded to one decimal place. There is no '
        'cross-month deduplication: a company hiring in several months '
        'appears in each month\'s cross-section. This page is regenerated '
        'from the scrapes by a converter, so the numbers update whenever a '
        f"new monthly scrape lands. Data through {provenance['data_through']}."
    )

    footer = (
        'This page is part of [The AI Engineering Field Guide]'
        '(/blog/ai-engineering-field-guide) series, which turns the same '
        'dataset into a practical read about the AI engineer role. If you '
        'want the structured path - interview prep, projects, and a '
        'community that builds - [become a member](/membership).'
    )

    parts = [
        intro,
        '## What job postings ask for',
        demand_intro,
        '<!-- include:widgets/job_market_skills.html -->',
        '## How demand is moving',
        trends_intro,
        '<!-- include:widgets/job_market_trends.html -->',
        '## Top employers',
        companies_intro,
        companies_table,
        '## Where the jobs are',
        locations_intro,
        locations_table,
        '## Dataset and methodology',
        scrapes_table,
        methodology,
        footer,
    ]
    return '\n\n'.join(parts) + '\n'


def article_description(aggregates):
    """Frontmatter description with the headline numbers."""
    provenance = aggregates['provenance']
    return (
        f"Skills, trends, employers, and locations from "
        f"{provenance['total_postings']:,} real AI engineering job "
        f"postings, aggregated from {provenance['scrape_count']} monthly "
        f"scrapes of builtin.com. Data through {provenance['data_through']}."
    )


def build_article_file(aggregates, *, existing=None, new_uuid=None):
    """Assemble the generated article file text.

    ``existing`` is the parsed frontmatter of the current target file (or
    None). ``content_id`` is reused from it when present; a fresh target
    needs ``new_uuid``. The ``date`` is the latest scrape date, so the
    article's freshness follows the data.
    """
    existing = existing or {}
    content_id = existing.get('content_id') or new_uuid
    if not content_id:
        raise ValueError('content_id required when no existing target file')
    metadata = {
        'author': 'Alexey Grigorev',
        'content_id': content_id,
        'data': aggregates,
        'date': aggregates['provenance']['latest_scrape'],
        'description': article_description(aggregates),
        'page_type': 'blog',
        'required_level': 0,
        'slug': 'ai-engineering-job-market',
        'status': 'published',
        'tags': ['field-guide', 'ai-engineering'],
        'title': 'AI Engineering Job Market: Skills, Trends, and Employers',
    }
    yaml_text = yaml.safe_dump(
        metadata,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
        width=80,
    )
    return f'---\n{yaml_text}---\n\n{render_article_body(aggregates)}'


def widget_template_text(name):
    """Read one bundled widget template shipped with the converter."""
    path = os.path.join(WIDGET_TEMPLATE_DIR, name)
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def read_existing(target_path):
    """Parse the current target file's frontmatter, or None if absent."""
    if not os.path.isfile(target_path):
        return None
    post = frontmatter.load(target_path)
    return dict(post.metadata)


@dataclass
class ScrapeCount:
    """Posting count for one scrape, for the command's printed summary."""

    date: str
    postings: int


@dataclass
class RefreshSummary:
    """Outcome of one converter run, for the command's printed summary."""

    months: list[str]
    postings_by_month: dict
    total_postings: int
    data_through: str
    article_rel: str
    article_existed: bool
    content_id: str | None
    skill_demand_categories: int
    trend_skill_count: int
    company_count: int
    location_count: int
    role_type_count: int
    written: bool

    def per_scrape_counts(self):
        """One :class:`ScrapeCount` per scrape, in chronological order."""
        return [
            ScrapeCount(date=month, postings=len(postings))
            for month, postings in self.postings_by_month.items()
        ]


def refresh(guide_root, content_repo, *, write=False):
    """Compute the aggregates and optionally write the content-repo files.

    With ``write=False`` nothing is written to disk (dry run). Returns the
    :class:`RefreshSummary` the command prints.
    """
    months = scrape_months(guide_root)
    if not months:
        raise ValueError(
            f'No monthly scrape directories found under '
            f'{os.path.join(guide_root, GUIDE_DATA_SUBPATH)}')
    postings = load_postings(guide_root, months)
    aggregates = compute_aggregates(postings, months)

    article_rel = f'{ARTICLE_TARGET_DIR}/{ARTICLE_FILE_NAME}'
    article_path = os.path.join(content_repo, article_rel)
    existing = read_existing(article_path)
    # Generated even on dry runs: a dry run must be able to render the
    # full file text for a brand-new target without writing anything.
    existing_id = (existing or {}).get('content_id')
    new_uuid = str(uuid.uuid4()) if not existing_id else None
    article_text = build_article_file(
        aggregates, existing=existing, new_uuid=new_uuid)

    written = False
    if write:
        os.makedirs(os.path.dirname(article_path), exist_ok=True)
        with open(article_path, 'w', encoding='utf-8') as handle:
            handle.write(article_text)
        for widget_target, widget_name in (
            (SKILLS_WIDGET_TARGET, SKILLS_WIDGET_NAME),
            (TRENDS_WIDGET_TARGET, TRENDS_WIDGET_NAME),
        ):
            widget_path = os.path.join(content_repo, widget_target)
            os.makedirs(os.path.dirname(widget_path), exist_ok=True)
            with open(widget_path, 'w', encoding='utf-8') as handle:
                handle.write(widget_template_text(widget_name))
        written = True

    return RefreshSummary(
        months=months,
        postings_by_month=postings,
        total_postings=aggregates['provenance']['total_postings'],
        data_through=aggregates['provenance']['data_through'],
        article_rel=article_rel,
        article_existed=existing is not None,
        content_id=(existing or {}).get('content_id'),
        skill_demand_categories=len(aggregates['skill_demand']),
        trend_skill_count=sum(
            len(group['skills'])
            for group in aggregates['skill_trends']['groups']),
        company_count=len(aggregates['companies']),
        location_count=len(aggregates['locations']),
        role_type_count=len(aggregates['role_mix']['types']),
        written=written,
    )
