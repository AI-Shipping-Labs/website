from django.contrib import admin
from content.models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'date', 'difficulty', 'required_level', 'published']
    list_filter = ['published', 'required_level', 'difficulty', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
