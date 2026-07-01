"""Django admin registrations for the questionnaires app (issue #800).

Studio is the primary authoring surface; this admin is a low-fi
inspector. ``Questionnaire`` filters on ``purpose`` / ``is_active`` and
``Response`` on ``questionnaire`` / ``status`` per the spec.
"""

from django.contrib import admin

from questionnaires.models import (
    Answer,
    AnswerOptionText,
    Persona,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
)


@admin.register(Persona)
class PersonaAdmin(admin.ModelAdmin):
    list_display = ('name', 'archetype', 'is_active', 'default_questionnaire', 'order')
    list_filter = ('is_active',)
    search_fields = ('name', 'archetype')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Questionnaire)
class QuestionnaireAdmin(admin.ModelAdmin):
    list_display = ['title', 'slug', 'purpose', 'is_active', 'created_at']
    list_filter = ['purpose', 'is_active']
    search_fields = ['title', 'slug']
    prepopulated_fields = {'slug': ('title',)}


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['prompt', 'questionnaire', 'question_type', 'is_required', 'order']
    list_filter = ['question_type', 'is_required', 'questionnaire']
    search_fields = ['prompt']


@admin.register(QuestionOption)
class QuestionOptionAdmin(admin.ModelAdmin):
    list_display = ['label', 'question', 'allows_free_text', 'order']
    search_fields = ['label']


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ['respondent', 'questionnaire', 'status', 'submitted_at']
    list_filter = ['questionnaire', 'status']
    search_fields = ['respondent__email']
    raw_id_fields = ['respondent']


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ['question', 'response', 'number_value']
    list_filter = ['response__status']


@admin.register(AnswerOptionText)
class AnswerOptionTextAdmin(admin.ModelAdmin):
    list_display = ['answer', 'selected_option']
    search_fields = ['text_value', 'selected_option__label']
