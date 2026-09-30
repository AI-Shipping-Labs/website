from community_base.knowledge_base.models import KnowledgeBasePage
from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from content.models import Download, MarketingPage
from content.models.cohort import Cohort
from content.nav_availability import (
    refresh_knowledge_base_nav_cache,
    refresh_marketing_pages_nav_cache,
    refresh_published_downloads_nav_cache,
    refresh_topics_nav_cache,
)
from content.services.coursework_bridge import (
    ensure_curriculum_cohort,
    remove_unused_curriculum_cohort,
)
from topics.models import TopicPage


@receiver(
    post_save,
    sender=Download,
    dispatch_uid='content.refresh_downloads_nav_availability_on_save',
)
@receiver(
    post_delete,
    sender=Download,
    dispatch_uid='content.refresh_downloads_nav_availability_on_delete',
)
def refresh_downloads_nav_availability(**kwargs):
    refresh_published_downloads_nav_cache()


@receiver(
    post_save,
    sender=MarketingPage,
    dispatch_uid='content.refresh_marketing_pages_nav_on_save',
)
@receiver(
    post_delete,
    sender=MarketingPage,
    dispatch_uid='content.refresh_marketing_pages_nav_on_delete',
)
def refresh_marketing_pages_nav(**kwargs):
    refresh_marketing_pages_nav_cache()


@receiver(
    post_save,
    sender=KnowledgeBasePage,
    dispatch_uid='content.refresh_kb_nav_availability_on_save',
)
@receiver(
    post_delete,
    sender=KnowledgeBasePage,
    dispatch_uid='content.refresh_kb_nav_availability_on_delete',
)
def refresh_knowledge_base_nav_availability(**kwargs):
    refresh_knowledge_base_nav_cache()


@receiver(
    post_save,
    sender=TopicPage,
    dispatch_uid='content.refresh_topics_nav_availability_on_save',
)
@receiver(
    post_delete,
    sender=TopicPage,
    dispatch_uid='content.refresh_topics_nav_availability_on_delete',
)
def refresh_topics_nav_availability(**kwargs):
    # Issue #1688: the sync family's cleanup refreshes the same flag; this
    # covers Studio, admin, and fixture writes outside the sync engine.
    refresh_topics_nav_cache()


@receiver(post_migrate, dispatch_uid='content.warm_downloads_nav_availability')
def warm_downloads_nav_availability(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'content':
        refresh_published_downloads_nav_cache()


@receiver(post_migrate, dispatch_uid='content.warm_marketing_pages_nav')
def warm_marketing_pages_nav(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'content':
        refresh_marketing_pages_nav_cache()


@receiver(post_migrate, dispatch_uid='content.warm_kb_nav_availability')
def warm_knowledge_base_nav_availability(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'cb_knowledge_base':
        refresh_knowledge_base_nav_cache()


@receiver(post_migrate, dispatch_uid='content.warm_topics_nav_availability')
def warm_topics_nav_availability(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'topics':
        refresh_topics_nav_cache()


@receiver(
    post_save,
    sender=Cohort,
    dispatch_uid='content.mirror_cohort_to_curriculum_on_save',
)
def mirror_cohort_to_curriculum(sender, instance, raw=False, **kwargs):
    """Issue #1696: keep the cohort's ``cb_curriculum.Cohort`` mirror in step."""
    if raw:
        return
    ensure_curriculum_cohort(instance)


@receiver(
    post_delete,
    sender=Cohort,
    dispatch_uid='content.remove_curriculum_mirror_on_delete',
)
def remove_curriculum_mirror(sender, instance, **kwargs):
    """Drop the mirror of a deleted cohort when no coursework uses it."""
    remove_unused_curriculum_cohort(instance.curriculum_cohort_id)
