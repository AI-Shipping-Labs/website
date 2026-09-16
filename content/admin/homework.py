from django.contrib import admin

from content.models.homework import Answer, Homework, Question, Submission


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ['source_question_id', 'text', 'question_type', 'answer_type', 'correct_answer', 'scores_for_correct_answer']


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ['title', 'cohort', 'due_date', 'state']
    list_filter = ['state', 'cohort__course']
    raw_id_fields = ['cohort']
    search_fields = ['title', 'slug', 'cohort__name']
    inlines = [QuestionInline]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['homework', 'text', 'question_type', 'answer_type']
    list_filter = ['question_type', 'answer_type']
    raw_id_fields = ['homework']
    search_fields = ['text', 'source_question_id']


class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 0
    fields = ['question', 'answer_text', 'is_correct']
    raw_id_fields = ['question']


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ['student', 'homework', 'submitted_at', 'questions_score', 'total_score']
    list_filter = ['homework__cohort__course']
    raw_id_fields = ['student', 'homework', 'enrollment']
    search_fields = ['student__email', 'homework__title']
    inlines = [AnswerInline]
