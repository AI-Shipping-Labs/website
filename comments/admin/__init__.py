from django.contrib import admin

from comments.models import Comment, CommentVote


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'content_id', 'user', 'parent', 'created_at')
    list_filter = ('created_at',)
    raw_id_fields = ('user', 'parent')


@admin.register(CommentVote)
class CommentVoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'comment', 'user', 'created_at')
    raw_id_fields = ('comment', 'user')
