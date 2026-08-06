"""Django admin for Book Club models (issue #1362).

The primary operator surface is Studio (``/studio/books/``); this admin is a
thin superuser-facing fallback, mirroring how sprints expose both.
"""

from django.contrib import admin

from bookclub.models import Book, Chapter


class ChapterInline(admin.TabularInline):
    model = Chapter
    extra = 0
    fields = ('number', 'title', 'deadline', 'week_label', 'event')
    ordering = ('number',)


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'slug', 'status', 'required_level', 'start_date')
    list_filter = ('status', 'required_level')
    search_fields = ('title', 'author', 'slug')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [ChapterInline]


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ('book', 'number', 'title', 'deadline', 'week_label')
    list_filter = ('book',)
    search_fields = ('title',)
    ordering = ('book', 'number')
