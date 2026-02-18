from django.contrib import admin
from content.models import Article


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['title', 'date', 'published']
    list_filter = ['published', 'date']
    search_fields = ['title', 'description']
    prepopulated_fields = {'slug': ('title',)}
