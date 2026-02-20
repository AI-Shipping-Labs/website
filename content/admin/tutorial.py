from django.contrib import admin
from content.models import Tutorial


@admin.register(Tutorial)
class TutorialAdmin(admin.ModelAdmin):
    list_display = ['title', 'date', 'required_level', 'published']
    list_filter = ['published', 'required_level', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
