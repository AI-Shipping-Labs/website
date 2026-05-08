"""Tests for studio course CRUD views.

Verifies:
- Course list view with search and status filter
- Course create form (GET and POST)
- Course edit form (GET and POST)
- Module creation
- Unit creation and editing
- Module reorder API
"""

import uuid

from django.test import TestCase

from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    LEVEL_REGISTERED,
)
from content.models import Course, Module, Unit
from tests.fixtures import StaffUserMixin


class StudioCourseListTest(StaffUserMixin, TestCase):
    """Test course list view."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_list_returns_200(self):
        response = self.client.get('/studio/courses/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/courses/')
        self.assertTemplateUsed(response, 'studio/courses/list.html')

    def test_list_shows_courses(self):
        Course.objects.create(title='Test Course', slug='test-course')
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'Test Course')

    def test_list_filter_by_status(self):
        Course.objects.create(title='DraftCourseXYZ', slug='draft', status='draft')
        Course.objects.create(title='PublishedCourseXYZ', slug='pub', status='published')
        response = self.client.get('/studio/courses/?status=draft')
        self.assertContains(response, 'DraftCourseXYZ')
        self.assertNotContains(response, 'PublishedCourseXYZ')
        self.assertContains(response, 'data-testid="studio-status-filter"')
        self.assertContains(response, '<span class="text-xs font-medium uppercase tracking-wider text-muted-foreground">Status</span>', html=True)
        self.assertContains(
            response,
            '<option value="draft" selected>Draft</option>',
            html=True,
        )

    def test_list_search(self):
        Course.objects.create(title='Python Course', slug='python')
        Course.objects.create(title='Java Course', slug='java')
        response = self.client.get('/studio/courses/?q=Python')
        self.assertContains(response, 'Python Course')
        self.assertNotContains(response, 'Java Course')

    def test_list_empty_state(self):
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'No courses found')

    def test_list_shows_synced_and_local_origins(self):
        Course.objects.create(
            title='Synced Course', slug='synced-course',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced/course.yaml',
        )
        Course.objects.create(title='Local Course', slug='local-course')

        response = self.client.get('/studio/courses/')

        self.assertContains(response, '<th class="text-left px-6 py-3 text-xs font-medium text-muted-foreground uppercase tracking-wider">Source</th>', html=True)
        self.assertContains(response, 'Synced')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'courses/synced/course.yaml')
        self.assertContains(response, 'Local / manual')
        self.assertContains(response, 'No GitHub source metadata')
        body = response.content.decode()
        synced_row = body[body.find('Synced Course'):body.find('</tr>', body.find('Synced Course'))]
        local_row = body[body.find('Local Course'):body.find('</tr>', body.find('Local Course'))]
        self.assertIn('>View</a>', synced_row)
        self.assertIn('>Edit</a>', local_row)

    def test_list_displays_human_access_labels(self):
        cases = [
            (LEVEL_OPEN, 'Free'),
            (LEVEL_REGISTERED, 'Registered users'),
            (LEVEL_BASIC, 'Basic (Level 10)'),
            (LEVEL_MAIN, 'Main (Level 20)'),
            (LEVEL_PREMIUM, 'Premium (Level 30)'),
            (42, 'Custom (Level 42)'),
        ]
        for level, label in cases:
            Course.objects.create(
                title=f'Access {level}',
                slug=f'access-{level}',
                required_level=level,
            )

        response = self.client.get('/studio/courses/')

        for _level, label in cases:
            with self.subTest(label=label):
                self.assertContains(response, label)
        self.assertNotContains(response, '>Level 30<')

    def test_list_renders_mobile_card_hooks_and_actions(self):
        Course.objects.create(
            title='A Very Long Studio Course Title That Should Wrap Cleanly On Phones',
            slug='long-mobile-course-title-that-should-wrap',
            required_level=LEVEL_PREMIUM,
        )

        response = self.client.get('/studio/courses/')

        self.assertContains(response, 'data-testid="studio-course-row"')
        self.assertContains(response, 'data-testid="studio-course-title"')
        self.assertContains(response, 'break-words')
        self.assertContains(response, 'data-label="Source"')
        self.assertContains(response, 'data-label="Actions"')
        self.assertContains(response, '>Edit</a>')
        self.assertContains(response, '>View on site</a>')


class StudioCourseCreateRemovedTest(StaffUserMixin, TestCase):
    """Test that course create URL has been removed."""

    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_create_url_returns_404(self):
        response = self.client.get('/studio/courses/new')
        self.assertEqual(response.status_code, 404)


class StudioCourseEditTest(StaffUserMixin, TestCase):
    """Test course edit form."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Edit Course', slug='edit-course', status='draft',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_form_shows_course_data(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Edit Course')

    def test_edit_course_post(self):
        response = self.client.post(f'/studio/courses/{self.course.pk}/edit', {
            'title': 'Updated Course',
            'slug': 'edit-course',
            'status': 'published',
            'required_level': '10',
            'tags': 'course, , ai ,, cohort ',
        })
        self.assertEqual(response.status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(self.course.title, 'Updated Course')
        self.assertEqual(self.course.status, 'published')
        self.assertEqual(self.course.required_level, 10)
        self.assertEqual(self.course.tags, ['course', 'ai', 'cohort'])

    def test_edit_shows_modules(self):
        Module.objects.create(course=self.course, slug='module-1', title='Module 1', sort_order=0)
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Module 1')

    def test_edit_nonexistent_course_returns_404(self):
        response = self.client.get('/studio/courses/99999/edit')
        self.assertEqual(response.status_code, 404)

    def test_synced_course_shows_origin_metadata_and_resync(self):
        content_id = uuid.uuid4()
        Course.objects.filter(pk=self.course.pk).update(
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/edit-course/course.yaml',
            source_commit='abc1234def5678901234567890123456789abcde',
            content_id=content_id,
        )

        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'Synced from GitHub')
        self.assertContains(response, 'AI-Shipping-Labs/content')
        self.assertContains(response, 'courses/edit-course/course.yaml')
        self.assertContains(response, 'abc1234def5678901234567890123456789abcde')
        self.assertContains(response, str(content_id))
        self.assertContains(
            response,
            'https://github.com/AI-Shipping-Labs/content/blob/main/'
            'courses/edit-course/course.yaml',
        )
        self.assertContains(response, 'data-testid="resync-source-button"')

    def test_local_course_shows_manual_origin_without_github_controls(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'Local / manual')
        self.assertContains(response, 'No GitHub source metadata exists')
        self.assertNotContains(response, 'Edit on GitHub')
        self.assertNotContains(response, 'data-testid="resync-source-button"')

    def test_modules_and_units_show_row_level_origins(self):
        synced_module = Module.objects.create(
            course=self.course,
            slug='synced-module',
            title='Synced Module',
            sort_order=1,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/edit-course/synced-module/README.md',
        )
        Module.objects.create(
            course=self.course, slug='local-module',
            title='Local Module', sort_order=2,
        )
        Unit.objects.create(
            module=synced_module,
            slug='synced-unit',
            title='Synced Unit',
            sort_order=1,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/edit-course/synced-module/synced-unit.md',
        )
        Unit.objects.create(
            module=synced_module,
            slug='local-unit',
            title='Local Unit',
            sort_order=2,
        )

        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'courses/edit-course/synced-module/README.md')
        self.assertContains(response, 'courses/edit-course/synced-module/synced-unit.md')
        self.assertContains(response, 'Local / manual', count=3)


class StudioCourseSourceManagedCleanupTest(StaffUserMixin, TestCase):
    """Tests for the source-managed course edit cleanup (issue #490).

    Verifies that source-managed courses:
    - render a compact source action row (sticky bar) containing all three
      source actions: View on site, Edit on GitHub, Re-sync source
    - link to the course YAML operator guide from the sticky bar
    - do not duplicate the View on site link in the page header
    - render absent Stripe/price metadata as `Not configured`, not as empty
      input fields that look configured
    - do not duplicate `Individual purchase` rows between the form and the
      side-panel state block
    - preserve the existing local/non-synced edit flow untouched
    """

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.synced = Course.objects.create(
            title='Synced',
            slug='synced',
            status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced/course.yaml',
        )

    def test_source_managed_compact_action_row_contains_three_source_actions(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        self.assertContains(response, 'data-testid="sticky-action-row"')
        self.assertContains(response, 'data-testid="sticky-github-source-link"')
        self.assertContains(response, 'data-testid="sticky-view-on-site"')
        self.assertContains(response, 'data-testid="sticky-resync-source-button"')

    def test_source_managed_links_to_course_yaml_guide(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        self.assertContains(response, 'data-testid="sticky-docs-link"')
        self.assertContains(response, '_docs/course_yaml.md')
        self.assertContains(response, 'Course YAML guide')

    def test_source_managed_header_omits_duplicate_view_on_site_link(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        # Sticky bar version remains the single canonical "View on site"
        # link in the action area; the page-header repeat is suppressed
        # for source-managed courses.
        self.assertNotContains(response, 'data-testid="view-on-site"')

    def test_source_managed_stripe_fields_render_as_not_configured(self):
        # No price, no product, no price ID set on the course.
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        self.assertContains(response, 'data-testid="individual-purchase-readonly"')
        # Disabled empty <input> fields are not rendered for synced courses
        # — they used to look like editable empty fields.
        self.assertNotContains(response, 'name="individual_price_eur"')

        body = response.content.decode()
        price_dd_start = body.find('data-testid="readonly-individual-price"')
        product_dd_start = body.find('data-testid="readonly-stripe-product"')
        price_id_dd_start = body.find('data-testid="readonly-stripe-price"')
        self.assertGreater(price_dd_start, 0)
        self.assertGreater(product_dd_start, 0)
        self.assertGreater(price_id_dd_start, 0)
        for start in (price_dd_start, product_dd_start, price_id_dd_start):
            chunk = body[start:start + 400]
            self.assertIn('Not configured', chunk)

    def test_source_managed_stripe_fields_show_real_values_when_present(self):
        Course.objects.filter(pk=self.synced.pk).update(
            individual_price_eur='49.00',
            stripe_product_id='prod_TEST123',
            stripe_price_id='price_TEST456',
        )

        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        body = response.content.decode()
        price_chunk = body[body.find('data-testid="readonly-individual-price"'):]
        product_chunk = body[body.find('data-testid="readonly-stripe-product"'):]
        price_id_chunk = body[body.find('data-testid="readonly-stripe-price"'):]
        self.assertIn('EUR 49.00', price_chunk[:200])
        self.assertIn('prod_TEST123', product_chunk[:300])
        self.assertIn('price_TEST456', price_id_chunk[:300])
        self.assertNotIn('Not configured', price_chunk[:200])

    def test_source_managed_state_panel_omits_duplicate_individual_purchase(self):
        response = self.client.get(f'/studio/courses/{self.synced.pk}/edit')

        body = response.content.decode()
        state_panel_start = body.find('data-testid="course-state-panel"')
        # Slice the state panel block (until end of containing div) and
        # confirm "Individual purchase" only appears in the main form's
        # section, not duplicated here.
        self.assertGreater(state_panel_start, 0)
        state_panel_end = body.find('</div>', body.find('</dl>', state_panel_start))
        state_panel_block = body[state_panel_start:state_panel_end]
        self.assertNotIn('Individual purchase', state_panel_block)

    def test_local_course_individual_purchase_input_remains_editable(self):
        local = Course.objects.create(
            title='Local', slug='local', status='draft',
        )

        response = self.client.get(f'/studio/courses/{local.pk}/edit')

        # Local courses keep the editable input — only synced ones lose it.
        self.assertContains(response, 'name="individual_price_eur"')
        self.assertContains(response, 'data-testid="individual-purchase-section"')
        # State panel still shows Individual purchase row for local courses,
        # but absent value reads as "Not configured" (not "Disabled").
        self.assertContains(response, 'data-testid="course-state-panel"')
        body = response.content.decode()
        state_panel_start = body.find('data-testid="course-state-panel"')
        state_panel_end = body.find('</div>', body.find('</dl>', state_panel_start))
        state_panel_block = body[state_panel_start:state_panel_end]
        self.assertIn('Individual purchase', state_panel_block)
        self.assertIn('Not configured', state_panel_block)
        self.assertNotIn('Disabled', state_panel_block)

    def test_local_course_save_post_still_works(self):
        local = Course.objects.create(
            title='Local Editable', slug='local-editable', status='draft',
        )

        response = self.client.post(f'/studio/courses/{local.pk}/edit', {
            'title': 'Local Editable Updated',
            'slug': 'local-editable',
            'status': 'published',
            'required_level': '0',
            'individual_price_eur': '19.00',
        })
        self.assertEqual(response.status_code, 302)
        local.refresh_from_db()
        self.assertEqual(local.title, 'Local Editable Updated')
        self.assertEqual(str(local.individual_price_eur), '19.00')


class StudioCourseCompactModulesTest(StaffUserMixin, TestCase):
    """Tests for the compact modules/units display on course edit (issue #491).

    Verifies that the modules/units section:
    - shows summary counts (modules, units) at the top
    - renders each module as a `<details>` element collapsed by default
    - displays unit count and origin badge in the module summary
    - keeps unit View/Edit actions and add-unit forms inside the details body
    - keeps the add-module form for local courses
    - keeps source-managed read-only behavior (no add forms)
    """

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Compact Course', slug='compact-course', status='draft',
        )
        self.module1 = Module.objects.create(
            course=self.course, slug='m1', title='Module One', sort_order=1,
        )
        self.module2 = Module.objects.create(
            course=self.course, slug='m2', title='Module Two', sort_order=2,
        )
        Unit.objects.create(
            module=self.module1, slug='u1', title='Unit A', sort_order=1,
        )
        Unit.objects.create(
            module=self.module1, slug='u2', title='Unit B', sort_order=2,
        )
        Unit.objects.create(
            module=self.module2, slug='u3', title='Unit C', sort_order=1,
        )

    def test_summary_counts_render_module_and_unit_totals(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'data-testid="modules-summary-counts"')
        body = response.content.decode()
        summary_start = body.find('data-testid="modules-summary-counts"')
        self.assertGreater(summary_start, 0)
        chunk = body[summary_start:summary_start + 200]
        self.assertIn('2 modules', chunk)
        self.assertIn('3 units', chunk)

    def test_modules_render_as_collapsed_details_by_default(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        # Each module is a <details> element. None should have the `open`
        # attribute — collapsed-by-default is the contract.
        body = response.content.decode()
        details_count = body.count('data-testid="module-disclosure"')
        self.assertEqual(details_count, 2)
        # Ensure no module disclosure has the `open` attribute. Scan each
        # `<details ... data-testid="module-disclosure"` opening tag.
        idx = 0
        while True:
            tag_start = body.find('<details', idx)
            if tag_start < 0:
                break
            tag_end = body.find('>', tag_start)
            tag = body[tag_start:tag_end]
            if 'data-testid="module-disclosure"' in tag:
                self.assertNotIn(
                    ' open',
                    tag,
                    f'<details> element should NOT be open by default: {tag}',
                )
            idx = tag_end + 1

    def test_module_summary_shows_title_order_and_unit_count(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        body = response.content.decode()
        # Locate the first module's summary block.
        summary_start = body.find('data-testid="module-summary"')
        self.assertGreater(summary_start, 0)
        summary_end = body.find('</summary>', summary_start)
        summary_block = body[summary_start:summary_end]
        self.assertIn('Module One', summary_block)
        self.assertIn('#1', summary_block)
        self.assertIn('2 units', summary_block)

    def test_second_module_summary_shows_correct_unit_count(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        body = response.content.decode()
        # Find the second module's summary.
        first = body.find('data-testid="module-summary"')
        second = body.find('data-testid="module-summary"', first + 1)
        self.assertGreater(second, first)
        end = body.find('</summary>', second)
        chunk = body[second:end]
        self.assertIn('Module Two', chunk)
        self.assertIn('1 unit', chunk)
        # Singular form, not "1 units".
        self.assertNotIn('1 units', chunk)

    def test_unit_edit_links_present_inside_module_body(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        # Even though collapsed by default, the unit edit links exist in DOM.
        self.assertContains(response, 'data-testid="unit-edit-link"', count=3)
        self.assertContains(response, 'Unit A')
        self.assertContains(response, 'Unit B')
        self.assertContains(response, 'Unit C')

    def test_add_module_form_remains_for_local_course(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'data-testid="add-module-form"')
        self.assertContains(response, 'New module title...')

    def test_add_unit_form_remains_inside_each_module_for_local_course(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        # One add-unit form per module on a local course.
        self.assertContains(response, 'data-testid="add-unit-form"', count=2)

    def test_synced_course_omits_add_module_and_add_unit_forms(self):
        synced = Course.objects.create(
            title='Synced',
            slug='synced-compact',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced-compact/course.yaml',
        )
        synced_module = Module.objects.create(
            course=synced, slug='sm', title='Synced Module', sort_order=1,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced-compact/sm/README.md',
        )
        Unit.objects.create(
            module=synced_module, slug='su', title='Synced Unit', sort_order=1,
        )

        response = self.client.get(f'/studio/courses/{synced.pk}/edit')

        self.assertNotContains(response, 'data-testid="add-module-form"')
        self.assertNotContains(response, 'data-testid="add-unit-form"')
        # View link still present (label switches View/Edit based on synced).
        self.assertContains(response, 'data-testid="unit-edit-link"')
        body = response.content.decode()
        link_start = body.find('data-testid="unit-edit-link"')
        link_end = body.find('</a>', link_start)
        self.assertIn('View', body[link_start:link_end])

    def test_modules_section_uses_compact_testid_marker(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        self.assertContains(response, 'data-testid="course-modules-section"')
        self.assertContains(response, 'data-testid="modules-list"')

    def test_workflow_panel_appears_before_modules_section_in_dom(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')

        body = response.content.decode()
        workflow_pos = body.find('data-testid="course-workflow-panel"')
        modules_pos = body.find('data-testid="course-modules-section"')
        self.assertGreater(workflow_pos, 0)
        self.assertGreater(modules_pos, 0)
        self.assertLess(
            workflow_pos, modules_pos,
            'Workflows panel must precede modules section in DOM order so '
            'mobile users see Manage Access/Enrollments before the modules '
            'tree.',
        )

    def test_empty_course_renders_empty_state_and_zero_counts(self):
        empty = Course.objects.create(
            title='Empty', slug='empty-compact', status='draft',
        )
        response = self.client.get(f'/studio/courses/{empty.pk}/edit')

        self.assertContains(response, 'No modules yet')
        body = response.content.decode()
        summary_start = body.find('data-testid="modules-summary-counts"')
        chunk = body[summary_start:summary_start + 200]
        self.assertIn('0 modules', chunk)
        self.assertIn('0 units', chunk)


class StudioModuleCreateTest(StaffUserMixin, TestCase):
    """Test module creation within a course."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='module-test', status='draft',
        )

    def test_create_module(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'New Module'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Module.objects.filter(course=self.course, title='New Module').exists()
        )

    def test_create_module_increments_sort_order(self):
        Module.objects.create(course=self.course, slug='m1', title='M1', sort_order=0)
        self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'M2'},
        )
        m2 = Module.objects.get(course=self.course, title='M2')
        self.assertEqual(m2.sort_order, 1)

    def test_create_module_redirects_to_course_edit(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'Redirect Module'},
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )


class StudioUnitCreateTest(StaffUserMixin, TestCase):
    """Test unit creation within a module."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='unit-test', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, slug='module', title='Module', sort_order=0,
        )

    def test_create_unit(self):
        self.client.post(
            f'/studio/modules/{self.module.pk}/units/add',
            {'title': 'New Unit'},
        )
        self.assertTrue(
            Unit.objects.filter(module=self.module, title='New Unit').exists()
        )

    def test_create_unit_redirects_to_course_edit(self):
        response = self.client.post(
            f'/studio/modules/{self.module.pk}/units/add',
            {'title': 'Redirect Unit'},
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )


class StudioUnitEditTest(StaffUserMixin, TestCase):
    """Test unit editing."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Course', slug='unit-edit', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, slug='module', title='Module', sort_order=0,
        )
        self.unit = Unit.objects.create(
            module=self.module, slug='unit', title='Unit', sort_order=0,
        )

    def test_edit_unit_form_returns_200(self):
        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_unit_form_shows_data(self):
        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')
        self.assertContains(response, 'Unit')

    def test_edit_unit_post(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Updated Unit',
            'video_url': 'https://youtube.com/test',
            'body': '# Lesson',
            'homework': '# Homework',
        })
        self.assertEqual(response.status_code, 302)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.title, 'Updated Unit')
        self.assertEqual(self.unit.video_url, 'https://youtube.com/test')
        self.assertIn('<h1>Lesson</h1>', self.unit.body_html)

    def test_edit_unit_is_preview(self):
        self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Preview Unit',
            'is_preview': 'on',
        })
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.is_preview)

    def test_edit_unit_redirects_to_course(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Redirect Test',
        })
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )

    def test_synced_unit_detail_shows_unit_source_path(self):
        self.course.source_repo = 'AI-Shipping-Labs/content'
        self.course.source_path = 'courses/unit-edit/course.yaml'
        self.course.save()
        self.unit.source_repo = 'AI-Shipping-Labs/content'
        self.unit.source_path = 'courses/unit-edit/module/unit.md'
        self.unit.source_commit = 'def1234def5678901234567890123456789abcde'
        self.unit.content_id = uuid.uuid4()
        self.unit.save()

        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')

        self.assertContains(response, 'Synced from GitHub')
        self.assertContains(response, 'courses/unit-edit/module/unit.md')
        self.assertContains(
            response,
            'https://github.com/AI-Shipping-Labs/content/blob/main/'
            'courses/unit-edit/module/unit.md',
        )
        self.assertNotContains(response, 'courses/unit-edit/course.yaml')


class StudioModuleReorderTest(StaffUserMixin, TestCase):
    """Test module reorder API."""

    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.course = Course.objects.create(
            title='Reorder', slug='reorder', status='draft',
        )
        self.m1 = Module.objects.create(
            course=self.course, slug='m1', title='M1', sort_order=0,
        )
        self.m2 = Module.objects.create(
            course=self.course, slug='m2', title='M2', sort_order=1,
        )

    def test_reorder_modules(self):
        import json
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/reorder',
            json.dumps([
                {'id': self.m1.pk, 'sort_order': 1},
                {'id': self.m2.pk, 'sort_order': 0},
            ]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertEqual(self.m1.sort_order, 1)
        self.assertEqual(self.m2.sort_order, 0)

    def test_reorder_requires_post(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/modules/reorder',
        )
        self.assertEqual(response.status_code, 405)
