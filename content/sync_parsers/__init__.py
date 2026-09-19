"""Site content sync parsers registered with the package engine.

Registration mirrors the legacy pipeline order so cross-family behaviors
(courses before instructors, workshops before article stale sweeps, ...)
are preserved under the package orchestration.
"""

from content.sync_parsers.families.articles import ArticlesParser
from content.sync_parsers.families.company_interviews import CompanyInterviewsParser
from content.sync_parsers.families.courses import CoursesParser
from content.sync_parsers.families.curated_links import CuratedLinksParser
from content.sync_parsers.families.downloads import DownloadsParser
from content.sync_parsers.families.events import EventsParser
from content.sync_parsers.families.instructors import InstructorsParser
from content.sync_parsers.families.interview_questions import InterviewQuestionsParser
from content.sync_parsers.families.knowledge_base import (
    DocsPagesParser,
    WikiPagesParser,
)
from content.sync_parsers.families.marketing_pages import MarketingPagesParser
from content.sync_parsers.families.member_wiki import MemberWikiPagesParser
from content.sync_parsers.families.projects import ProjectsParser
from content.sync_parsers.families.tiers import TiersParser
from content.sync_parsers.families.workshops import WorkshopsParser

_PARSER_ORDER = (
    CoursesParser,
    WorkshopsParser,
    ArticlesParser,
    ProjectsParser,
    EventsParser,
    InstructorsParser,
    CuratedLinksParser,
    DownloadsParser,
    MarketingPagesParser,
    InterviewQuestionsParser,
    # Company interviews (issue #1712) depend on nothing from the other
    # families; they sit next to the question banks they extend.
    CompanyInterviewsParser,
    TiersParser,
    # Knowledge base pages fill package-owned storage (issue #1685) and
    # depend on nothing from the other families.
    WikiPagesParser,
    DocsPagesParser,
    # Member wiki topic pages fill the site-owned topics app (issue
    # #1688); they likewise depend on nothing from the other families.
    MemberWikiPagesParser,
)

_registered = False


def register_all():
    """Register every family parser exactly once (idempotent per process)."""
    global _registered
    if _registered:
        return
    from content.sync_parsers.base import register

    for parser_cls in _PARSER_ORDER:
        register(parser_cls)
    _registered = True
