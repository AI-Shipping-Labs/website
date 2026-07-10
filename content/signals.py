from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from content.models import Download, MarketingPage
from content.nav_availability import (
    refresh_marketing_pages_nav_cache,
    refresh_published_downloads_nav_cache,
)


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


@receiver(post_migrate, dispatch_uid='content.warm_downloads_nav_availability')
def warm_downloads_nav_availability(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'content':
        refresh_published_downloads_nav_cache()


@receiver(post_migrate, dispatch_uid='content.warm_marketing_pages_nav')
def warm_marketing_pages_nav(app_config, **kwargs):
    if getattr(app_config, 'label', None) == 'content':
        refresh_marketing_pages_nav_cache()
