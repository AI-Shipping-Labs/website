"""Curated topic taxonomy for content listing filters.

The blog (and workshops) accumulate a long tail of raw tags — most used by
a single article — which made the old "wall of pills" filter unscannable.
Instead of surfacing every tag, we curate a small ordered set of topics and
map the raw tags behind each one (Fable redesign). The listing filter shows
these topics; per-item raw tags are humanized where they still render.

Each topic is ``slug -> {'label': <human name>, 'tags': [<raw tags>]}``.
An item belongs to a topic when its tag set intersects the topic's tags.
Order matters: ``primary_topic`` returns the first matching topic, so list
the broadest/most-important topics first.
"""

from collections import OrderedDict

BLOG_TOPICS = OrderedDict([
    ('ai-engineering', {
        'label': 'AI Engineering',
        'tags': [
            'ai-engineering', 'ai', 'llm', 'llms', 'ai-agents', 'agents',
            'ml-engineering', 'machine-learning', 'ml', 'data-science',
            'openai', 'gpt-4', 'dall-e', 'rag', 'search', 'crisp-dm',
            'prompt-engineering', 'fine-tuning',
        ],
    }),
    ('coding-with-ai', {
        'label': 'Coding with AI',
        'tags': [
            'ai-tools', 'claude-code', 'copilot', 'chatgpt', 'skills',
            'slash-commands', 'persistent-coding-loops', 'automation',
            'assistant', 'productivity', 'markdown', 'vibe-coding',
        ],
    }),
    ('production-infra', {
        'label': 'Production & Infra',
        'tags': [
            'aws', 'terraform', 'lambda', 'ci-cd', 'cicd', 'devops',
            'infrastructure', 'database', 'incident', 'moderation',
            'tooling-architecture', 'python', 'deployment', 'observability',
        ],
    }),
    ('career', {
        'label': 'Career',
        'tags': [
            'careers', 'career', 'interviews', 'interview', 'jobs',
            'learning-path', 'hiring',
        ],
    }),
    ('community', {
        'label': 'Community & Courses',
        'tags': [
            'ai-shipping-labs', 'sprints', 'datatalks-club', 'community',
            'ai-engineering-buildcamp', 'buildcamp', 'llm-zoomcamp',
            'zoomcamp', 'podcast', 'course', 'courses',
        ],
    }),
])

WORKSHOP_TOPICS = OrderedDict([
    ('ai-agents', {
        'label': 'AI Agents',
        'tags': [
            'ai-agents', 'coding-agents', 'agent-systems', 'agentic-loop',
            'function-calling', 'tool-calling', 'async-control', 'guardrails',
            'agent-safety', 'mcp',
        ],
    }),
    ('rag-search', {
        'label': 'RAG & Search',
        'tags': ['rag', 'search', 'information-retrieval', 'elasticsearch'],
    }),
    ('ai-engineering', {
        'label': 'AI Engineering',
        'tags': [
            'llm-engineering', 'ai-engineering', 'open-models', 'vllm',
            'runpod', 'gpu', 'comparison',
        ],
    }),
    ('coding-with-ai', {
        'label': 'Coding with AI',
        'tags': [
            'vibe-coding', 'coding-assistants', 'ai-tools',
            'developer-tools', 'claude-code',
        ],
    }),
    ('production-apps', {
        'label': 'Production & Apps',
        'tags': [
            'data-engineering', 'production-systems', 'tooling-architecture',
            'full-stack', 'django', 'python', 'fastapi', 'react', 'temporal',
            'ci-cd',
        ],
    }),
    ('career', {
        'label': 'Career',
        'tags': [
            'career', 'careers', 'personal-brand', 'linkedin', 'writing',
            'portfolio', 'job-search', 'project-selection', 'cv',
        ],
    }),
])


def _tagset(tags):
    return {str(t).strip().lower() for t in (tags or []) if str(t).strip()}


def primary_topic(tags, topics=BLOG_TOPICS):
    """Return ``(slug, label)`` of the first curated topic matching ``tags``.

    Returns ``None`` when the item's tags map to no curated topic — callers
    should hide the topic label in that case rather than invent one.
    """
    have = _tagset(tags)
    if not have:
        return None
    for slug, meta in topics.items():
        if have & _tagset(meta['tags']):
            return slug, meta['label']
    return None


def topics_with_matches(items, topics=BLOG_TOPICS):
    """Return the ordered curated topics that match at least one item.

    ``items`` is any iterable of objects exposing a ``.tags`` list. Only
    topics with a non-zero match count are surfaced, so the filter never
    shows a pill that leads to an empty page.
    """
    itemsets = [_tagset(getattr(item, 'tags', None)) for item in items]
    result = []
    for slug, meta in topics.items():
        topic_tags = _tagset(meta['tags'])
        count = sum(1 for have in itemsets if have & topic_tags)
        if count:
            result.append({'slug': slug, 'label': meta['label'], 'count': count})
    return result


def filter_by_topic(items, slug, topics=BLOG_TOPICS):
    """Return the subset of ``items`` whose tags fall under topic ``slug``.

    Unknown slug returns ``items`` unchanged (treated as no filter).
    """
    meta = topics.get(slug)
    if not meta:
        return list(items)
    topic_tags = _tagset(meta['tags'])
    return [item for item in items if _tagset(getattr(item, 'tags', None)) & topic_tags]
