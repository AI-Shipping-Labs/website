from django.contrib import admin

from content.models import InterviewCategory


@admin.register(InterviewCategory)
class InterviewCategoryAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'status', 'source_repo', 'updated_at']
    search_fields = ['title', 'slug']
    list_filter = ['status']
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ['source_repo', 'source_path', 'source_commit', 'created_at', 'updated_at']
