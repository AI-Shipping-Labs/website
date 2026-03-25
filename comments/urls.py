from django.urls import path

from comments.views.api import comments_endpoint, reply_to_comment, toggle_vote

urlpatterns = [
    path(
        'api/comments/<uuid:content_id>',
        comments_endpoint,
        name='comments_endpoint',
    ),
    path(
        'api/comments/<int:comment_id>/reply',
        reply_to_comment,
        name='comments_reply',
    ),
    path(
        'api/comments/<int:comment_id>/vote',
        toggle_vote,
        name='comments_vote',
    ),
]
