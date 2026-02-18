from django.contrib import admin
from content.models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'date', 'difficulty', 'published']
    list_filter = ['published', 'difficulty', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
