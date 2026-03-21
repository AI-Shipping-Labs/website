from django.contrib import admin

from content.models import LearningPath


@admin.register(LearningPath)
class LearningPathAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'source_repo', 'updated_at']
    search_fields = ['title', 'slug']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['source_repo', 'source_path', 'source_commit', 'created_at', 'updated_at']
