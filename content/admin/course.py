from django.contrib import admin

from content.models import Course, Module, Unit, UserCourseProgress


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 1
    ordering = ['sort_order']


class UnitInline(admin.TabularInline):
    model = Unit
    extra = 1
    ordering = ['sort_order']
    fields = ['title', 'sort_order', 'video_url', 'is_preview']


def publish_courses(modeladmin, request, queryset):
    """Publish selected courses."""
    queryset.update(status='published')


publish_courses.short_description = 'Publish selected courses'


def unpublish_courses(modeladmin, request, queryset):
    """Unpublish selected courses (set to draft)."""
    queryset.update(status='draft')


unpublish_courses.short_description = 'Unpublish selected courses'


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'slug', 'status', 'instructor_name',
        'required_level', 'is_free', 'created_at',
    ]
    list_filter = ['status', 'required_level', 'is_free']
    search_fields = ['title', 'description', 'instructor_name']
    prepopulated_fields = {'slug': ('title',)}
    actions = [publish_courses, unpublish_courses]
    inlines = [ModuleInline]

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
    )


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ['title', 'course', 'sort_order']
    list_filter = ['course']
    ordering = ['course', 'sort_order']
    inlines = [UnitInline]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ['title', 'module', 'sort_order', 'is_preview']
    list_filter = ['is_preview', 'module__course']
    ordering = ['module__course', 'module__sort_order', 'sort_order']


@admin.register(UserCourseProgress)
class UserCourseProgressAdmin(admin.ModelAdmin):
    list_display = ['user', 'unit', 'completed_at']
    list_filter = ['completed_at']
    raw_id_fields = ['user', 'unit']
