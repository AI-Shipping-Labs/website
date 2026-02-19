from django import forms
from django.contrib import admin

from content.admin.widgets import TimestampEditorWidget
from content.models import Course, Module, Unit, UserCourseProgress, Cohort


# ---------------------------------------------------------------------------
# Unit form with all fields including timestamps widget
# ---------------------------------------------------------------------------

class UnitAdminForm(forms.ModelForm):
    """Custom form for Unit to use the TimestampEditorWidget for timestamps."""

    class Meta:
        model = Unit
        fields = '__all__'
        widgets = {
            'timestamps': TimestampEditorWidget(),
        }


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class UnitInline(admin.StackedInline):
    """Inline editor for units within a module.

    Shows all unit fields: title, video_url, body, homework, timestamps,
    is_preview, and sort_order. Uses StackedInline for better layout with
    large text fields.
    """
    model = Unit
    form = UnitAdminForm
    extra = 0
    ordering = ['sort_order']
    fields = [
        'title', 'sort_order', 'video_url', 'is_preview',
        'available_after_days', 'body', 'homework', 'timestamps',
    ]
    classes = ['collapse']


class CohortInline(admin.TabularInline):
    """Inline editor for cohorts within a course."""
    model = Cohort
    extra = 0
    fields = ['name', 'start_date', 'end_date', 'is_active', 'max_participants']


class ModuleInline(admin.TabularInline):
    """Inline editor for modules within a course.

    Allows adding, reordering, and deleting modules directly on the
    course edit form.
    """
    model = Module
    extra = 0
    ordering = ['sort_order']
    fields = ['title', 'sort_order']
    show_change_link = True


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

def publish_courses(modeladmin, request, queryset):
    """Publish selected courses."""
    queryset.update(status='published')


publish_courses.short_description = 'Publish selected courses'


def unpublish_courses(modeladmin, request, queryset):
    """Unpublish selected courses (set to draft)."""
    queryset.update(status='draft')


unpublish_courses.short_description = 'Unpublish selected courses'


# ---------------------------------------------------------------------------
# ModelAdmin classes
# ---------------------------------------------------------------------------

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    """Admin for Course with all fields, sorting, filtering, and nested
    module inline editor."""

    list_display = [
        'title', 'slug', 'status', 'instructor_name',
        'required_level', 'is_free', 'created_at', 'updated_at',
    ]
    list_display_links = ['title']
    list_filter = ['status', 'required_level', 'is_free']
    search_fields = ['title', 'description', 'instructor_name']
    prepopulated_fields = {'slug': ('title',)}
    actions = [publish_courses, unpublish_courses]
    inlines = [ModuleInline, CohortInline]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        (None, {
            'fields': (
                'title', 'slug', 'description', 'cover_image_url',
                'instructor_name', 'instructor_bio',
            ),
        }),
        ('Tags & Visibility', {
            'fields': ('tags', 'required_level', 'is_free'),
        }),
        ('Publishing', {
            'fields': ('status', 'discussion_url'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    """Admin for Module with nested unit inline editor.

    Provides access to the full unit editor (with body, homework,
    timestamps) via the stacked inline on each module's edit page.
    """
    list_display = ['title', 'course', 'sort_order']
    list_display_links = ['title']
    list_filter = ['course']
    search_fields = ['title', 'course__title']
    ordering = ['course', 'sort_order']
    inlines = [UnitInline]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    """Admin for Unit with all fields including timestamps widget."""

    form = UnitAdminForm
    list_display = ['title', 'module', 'sort_order', 'is_preview', 'video_url']
    list_display_links = ['title']
    list_filter = ['is_preview', 'module__course']
    search_fields = ['title', 'module__title', 'module__course__title']
    ordering = ['module__course', 'module__sort_order', 'sort_order']

    fieldsets = (
        (None, {
            'fields': ('module', 'title', 'sort_order', 'is_preview', 'available_after_days'),
        }),
        ('Video', {
            'fields': ('video_url', 'timestamps'),
        }),
        ('Content', {
            'fields': ('body', 'homework'),
        }),
    )


@admin.register(UserCourseProgress)
class UserCourseProgressAdmin(admin.ModelAdmin):
    list_display = ['user', 'unit', 'completed_at']
    list_filter = ['completed_at']
    raw_id_fields = ['user', 'unit']
