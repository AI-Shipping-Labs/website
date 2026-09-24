"""Public, stable UUID share-link redirects (issue #1802)."""

import uuid
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Module,
    Project,
    Tutorial,
    Unit,
    Workshop,
    WorkshopPage,
)
from events.models import Event


class ContentShareLinkTest(TestCase):
    def _published_targets(self):
        today = date(2026, 9, 24)
        course_id = uuid.uuid4()
        module_id = uuid.uuid4()
        unit_id = uuid.uuid4()
        course = Course.objects.create(
            title='Share course', slug='share-course', status='published',
            source_content_id=course_id,
        )
        module = Module.objects.create(
            title='Share module', slug='share-module', course=course,
            source_content_id=module_id,
        )

        workshop = Workshop.objects.create(
            title='Share workshop', slug='share-workshop', date=today,
            status='published', content_id=uuid.uuid4(),
        )
        workshop_page = WorkshopPage.objects.create(
            workshop=workshop, title='Share page', slug='share-page',
            content_id=uuid.uuid4(),
        )

        targets = [
            (
                Article.objects.create(
                    title='Learning path', slug='learning-path', date=today,
                    page_type='learning_path', content_id=uuid.uuid4(),
                ),
                None,
            ),
            (
                Project.objects.create(
                    title='Project', slug='project', date=today,
                    content_id=uuid.uuid4(),
                ),
                None,
            ),
            (
                Tutorial.objects.create(
                    title='Tutorial', slug='tutorial', date=today,
                    content_id=uuid.uuid4(),
                ),
                None,
            ),
            (
                Download.objects.create(
                    title='Download', slug='download', file_url='https://example.com/file.pdf',
                    content_id=uuid.uuid4(),
                ),
                None,
            ),
            (workshop, None),
            (workshop_page, None),
            (
                MarketingPage.objects.create(
                    title='Marketing page', public_path='/share-marketing-page',
                    status='published', content_id=uuid.uuid4(),
                ),
                None,
            ),
            (
                Event.objects.create(
                    title='Event', slug='share-event', start_datetime=timezone.now(),
                    status='completed', published=True, content_id=uuid.uuid4(),
                ),
                None,
            ),
            (course, course_id),
            (module, module_id),
            (
                Unit.objects.create(
                    title='Share unit', slug='share-unit', module=module,
                    source_content_id=unit_id,
                ),
                unit_id,
            ),
        ]
        return [
            (target, source_id or target.content_id)
            for target, source_id in targets
        ]

    def test_get_and_head_redirect_all_supported_published_types(self):
        for target, content_id in self._published_targets():
            with self.subTest(model=target.__class__.__name__):
                url = f'/c/{content_id}'
                expected = target.get_absolute_url()

                get_response = self.client.get(url)
                self.assertEqual(get_response.status_code, 302)
                self.assertEqual(get_response['Location'], expected)
                self.assertEqual(get_response.content, b'')

                head_response = self.client.head(url)
                self.assertEqual(head_response.status_code, 302)
                self.assertEqual(head_response['Location'], expected)
                self.assertEqual(head_response.content, b'')

    def test_destination_is_resolved_again_after_identity_preserving_path_changes(self):
        targets = self._published_targets()
        changes = {
            Article: ('slug', 'renamed-learning-path'),
            Course: ('slug', 'renamed-share-course'),
            Module: ('slug', 'renamed-share-module'),
            Unit: ('slug', 'renamed-share-unit'),
            Workshop: ('slug', 'renamed-share-workshop'),
            WorkshopPage: ('slug', 'renamed-share-page'),
            MarketingPage: ('public_path', '/renamed-share-marketing-page'),
        }

        for target, content_id in targets:
            change = changes.get(target.__class__)
            if change is None:
                continue
            field_name, new_value = change
            original_url = target.get_absolute_url()
            setattr(target, field_name, new_value)
            target.save()

            with self.subTest(model=target.__class__.__name__):
                response = self.client.get(f'/c/{content_id}')
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response['Location'], original_url)
                self.assertEqual(response['Location'], target.get_absolute_url())

    def test_preserves_raw_query_string_and_uses_current_unit_url(self):
        targets = self._published_targets()
        unit, content_id = next(
            (target, content_id)
            for target, content_id in targets
            if isinstance(target, Unit)
        )
        query = 'cohort=owned-key&tag=one&tag=two&return=%2Fcourses%2Fstart'

        response = self.client.get(f'/c/{content_id}?{query}')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], f'{unit.get_absolute_url()}?{query}')

    def test_buildcamp_single_unit_share_uses_current_short_url(self):
        content_id = uuid.uuid4()
        course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='ai-buildcamp',
            status='published', source_content_id=uuid.uuid4(),
        )
        week = Module.objects.create(
            title='Foundations', slug='foundations', course=course,
        )
        session = Module.objects.create(
            title='Session 1', slug='session', course=course,
            parent=week, sort_order=1,
        )
        unit = Unit.objects.create(
            title='Session 1', slug='session-1', module=session,
            source_content_id=content_id,
        )

        response = self.client.get(f'/c/{content_id}')

        self.assertEqual(unit.get_absolute_url(), '/courses/ai-buildcamp/foundations/session')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/courses/ai-buildcamp/foundations/session')

        session.slug = 'session-8'
        session.save(update_fields=['slug'])
        response_after_change = self.client.get(f'/c/{content_id}')

        self.assertEqual(unit.get_absolute_url(), '/courses/ai-buildcamp/foundations/session-8')
        self.assertEqual(response_after_change.status_code, 302)
        self.assertEqual(response_after_change['Location'], unit.get_absolute_url())

    def test_published_gated_course_uses_normal_course_detail_access_response(self):
        course_id = uuid.uuid4()
        course = Course.objects.create(
            title='Premium share course', slug='premium-share-course',
            status='published', required_level=30, source_content_id=course_id,
        )

        response = self.client.get(f'/c/{course_id}')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], course.get_absolute_url())
        destination = self.client.get(response['Location'])
        self.assertFalse(destination.context['has_access'])
        self.assertEqual(destination.context['required_tier_name'], 'Premium')

    def test_unpublished_targets_and_unpublished_curriculum_parents_return_404(self):
        today = date(2026, 9, 24)
        hidden_ids = [uuid.uuid4() for _ in range(10)]
        Article.objects.create(
            title='Hidden article', slug='hidden-article', date=today,
            published=False, content_id=hidden_ids[0],
        )
        Project.objects.create(
            title='Hidden project', slug='hidden-project', date=today,
            published=False, content_id=hidden_ids[1],
        )
        Tutorial.objects.create(
            title='Hidden tutorial', slug='hidden-tutorial', date=today,
            published=False, content_id=hidden_ids[2],
        )
        Download.objects.create(
            title='Hidden download', slug='hidden-download',
            file_url='https://example.com/file.pdf', published=False,
            content_id=hidden_ids[3],
        )
        draft_workshop = Workshop.objects.create(
            title='Draft workshop', slug='draft-workshop', date=today,
            status='draft', content_id=hidden_ids[4],
        )
        WorkshopPage.objects.create(
            workshop=draft_workshop, title='Draft page', slug='draft-page',
            content_id=hidden_ids[5],
        )
        MarketingPage.objects.create(
            title='Draft marketing page', public_path='/draft-share-page',
            status='draft', content_id=hidden_ids[6],
        )
        Event.objects.create(
            title='Cancelled event', slug='cancelled-share-event',
            start_datetime=timezone.now(), status='cancelled', published=True,
            content_id=hidden_ids[7],
        )
        draft_course = Course.objects.create(
            title='Draft course', slug='draft-share-course', status='draft',
            source_content_id=hidden_ids[8],
        )
        draft_module = Module.objects.create(
            title='Draft module', slug='draft-share-module', course=draft_course,
            source_content_id=uuid.uuid4(),
        )
        Unit.objects.create(
            title='Unit in draft course', slug='draft-share-unit',
            module=draft_module, source_content_id=hidden_ids[9],
        )

        for content_id in hidden_ids:
            with self.subTest(content_id=content_id):
                self.assertEqual(self.client.get(f'/c/{content_id}').status_code, 404)

    def test_unknown_invalid_and_ambiguous_ids_return_404(self):
        unknown_id = uuid.uuid4()
        shared_id = uuid.uuid4()
        Article.objects.create(
            title='First duplicate', slug='first-duplicate', date=date(2026, 9, 24),
            content_id=shared_id,
        )
        Project.objects.create(
            title='Second duplicate', slug='second-duplicate',
            date=date(2026, 9, 24), content_id=shared_id, published=False,
        )

        self.assertEqual(self.client.get(f'/c/{unknown_id}').status_code, 404)
        self.assertEqual(self.client.get('/c/not-a-uuid').status_code, 404)
        self.assertEqual(self.client.get(f'/c/{shared_id}').status_code, 404)

        course = Course.objects.create(
            title='Curriculum duplicate course', slug='curriculum-duplicate-course',
            status='published',
        )
        curriculum_id = uuid.uuid4()
        Module.objects.create(
            title='Duplicate module 1', slug='duplicate-module-1', course=course,
            source_content_id=curriculum_id,
        )
        Module.objects.create(
            title='Duplicate module 2', slug='duplicate-module-2', course=course,
            source_content_id=curriculum_id,
        )
        self.assertEqual(self.client.get(f'/c/{curriculum_id}').status_code, 404)

    def test_nonlocal_canonical_destination_is_rejected(self):
        article_id = uuid.uuid4()
        Article.objects.create(
            title='Unsafe destination', slug='unsafe-destination',
            date=date(2026, 9, 24), content_id=article_id,
        )

        with patch.object(Article, 'get_absolute_url', return_value='//example.com/elsewhere'):
            response = self.client.get(f'/c/{article_id}')

        self.assertEqual(response.status_code, 404)
