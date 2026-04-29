from django.apps import AppConfig


class CommunityConfig(AppConfig):
    name = 'community'

    def ready(self):
        from community.services.import_slack import register_slack_import_adapter

        register_slack_import_adapter()
