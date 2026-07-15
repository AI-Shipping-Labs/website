"""Code-owned taxonomy for public workshop catalog facets."""

FACET_TOPIC = "topic"
FACET_TECHNOLOGY = "technology"
FACET_EXCLUDED = "excluded"


TECHNOLOGY_TAGS = frozenset({
    "ci-cd",
    "claude-code",
    "django",
    "elasticsearch",
    "fastapi",
    "gpu",
    "mcp",
    "python",
    "react",
    "runpod",
    "temporal",
    "vllm",
})

EXCLUDED_TAGS = frozenset({
    "comparison",
    "cv",
    "job-search",
    "linkedin",
    "personal-brand",
    "portfolio",
    "project-selection",
    "writing",
})


def facet_for_tag(tag):
    """Return the public catalog facet for an exact stored tag value."""
    if tag in TECHNOLOGY_TAGS:
        return FACET_TECHNOLOGY
    if tag in EXCLUDED_TAGS:
        return FACET_EXCLUDED
    return FACET_TOPIC
