"""Course.maven_course_key / Cohort.external_key model + sync coverage (issue #1659)."""

import os
import shutil
import tempfile

from django.db import IntegrityError, transaction
from django.test import TestCase

from content.models import Cohort, Course
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class CourseMavenCourseKeyModelTest(TestCase):
    def test_field_defaults_blank(self):
        course = Course.objects.create(title="T", slug="t-1659")
        self.assertEqual(course.maven_course_key, "")


class CohortExternalKeyModelTest(TestCase):
    def setUp(self):
        self.course = Course.objects.create(title="T", slug="t-1659-cohort")

    def test_field_defaults_blank(self):
        cohort = Cohort.objects.create(
            course=self.course, name="Cohort A",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        self.assertEqual(cohort.external_key, "")

    def test_multiple_blank_external_keys_are_legal(self):
        Cohort.objects.create(
            course=self.course, name="Cohort A",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        Cohort.objects.create(
            course=self.course, name="Cohort B",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        self.assertEqual(Cohort.objects.filter(course=self.course).count(), 2)

    def test_duplicate_external_key_within_course_is_rejected(self):
        Cohort.objects.create(
            course=self.course, name="Cohort A", external_key="cohort-4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cohort.objects.create(
                    course=self.course, name="Cohort A (dup)", external_key="cohort-4",
                    start_date="2026-09-21", end_date="2026-11-22",
                )

    def test_same_external_key_allowed_across_different_courses(self):
        other_course = Course.objects.create(title="Other", slug="t-1659-other")
        Cohort.objects.create(
            course=self.course, name="Cohort A", external_key="cohort-4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        Cohort.objects.create(
            course=other_course, name="Cohort A", external_key="cohort-4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        self.assertEqual(Cohort.objects.filter(external_key="cohort-4").count(), 2)


class _CourseYamlSyncFixtureBase(TestCase):
    """Writes a minimal single-course repo on disk for sync tests."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/buildcamp-course-1659",
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, rel_path, text):
        full = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
        return full

    def _write_course_yaml(self, extras=""):
        body = (
            'title: "Buildcamp"\n'
            'slug: "buildcamp-1659"\n'
            'required_level: 20\n'
            'content_id: "dddddddd-dddd-dddd-dddd-dddddddddddd"\n'
        ) + extras
        self._write("course.yaml", body)


class MavenCourseKeySyncTest(_CourseYamlSyncFixtureBase):
    def test_maven_course_key_syncs_onto_course(self):
        self._write_course_yaml(extras='maven_course_key: "from-rag-to-agents"\n')

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug="buildcamp-1659")
        self.assertEqual(course.maven_course_key, "from-rag-to-agents")

    def test_blank_maven_course_key_by_default(self):
        self._write_course_yaml()

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug="buildcamp-1659")
        self.assertEqual(course.maven_course_key, "")


class CohortsYamlSyncTest(_CourseYamlSyncFixtureBase):
    def test_cohorts_list_creates_cohort_rows(self):
        self._write_course_yaml(
            extras=(
                "cohorts:\n"
                "  - key: cohort-4\n"
                "    name: Cohort 4\n"
                "    start_date: 2026-09-21\n"
                "    end_date: 2026-11-22\n"
            ),
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug="buildcamp-1659")
        cohort = Cohort.objects.get(course=course, external_key="cohort-4")
        self.assertEqual(cohort.name, "Cohort 4")
        self.assertEqual(str(cohort.start_date), "2026-09-21")
        self.assertEqual(str(cohort.end_date), "2026-11-22")

    def test_resync_updates_changed_cohort_fields(self):
        self._write_course_yaml(
            extras=(
                "cohorts:\n"
                "  - key: cohort-4\n"
                "    name: Cohort 4\n"
                "    start_date: 2026-09-21\n"
                "    end_date: 2026-11-22\n"
            ),
        )
        sync_content_source(self.source, repo_dir=self.temp_dir)

        self._write_course_yaml(
            extras=(
                "cohorts:\n"
                "  - key: cohort-4\n"
                "    name: Cohort 4 (renamed)\n"
                "    start_date: 2026-09-22\n"
                "    end_date: 2026-11-23\n"
            ),
        )
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug="buildcamp-1659")
        cohort = Cohort.objects.get(course=course, external_key="cohort-4")
        self.assertEqual(cohort.name, "Cohort 4 (renamed)")
        self.assertEqual(str(cohort.start_date), "2026-09-22")
        self.assertEqual(str(cohort.end_date), "2026-11-23")
        self.assertEqual(
            Cohort.objects.filter(course=course, external_key="cohort-4").count(), 1,
        )

    def test_cohort_removed_from_yaml_is_never_deleted(self):
        self._write_course_yaml(
            extras=(
                "cohorts:\n"
                "  - key: cohort-4\n"
                "    name: Cohort 4\n"
                "    start_date: 2026-09-21\n"
                "    end_date: 2026-11-22\n"
            ),
        )
        sync_content_source(self.source, repo_dir=self.temp_dir)

        self._write_course_yaml()  # cohorts: dropped entirely
        log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(log.errors, [])

        course = Course.objects.get(slug="buildcamp-1659")
        self.assertTrue(
            Cohort.objects.filter(course=course, external_key="cohort-4").exists()
        )

    def test_cohort_entry_missing_required_field_fails_only_that_course(self):
        self._write_course_yaml(
            extras=(
                "cohorts:\n"
                "  - key: cohort-4\n"
                "    start_date: 2026-09-21\n"
                "    end_date: 2026-11-22\n"
            ),
        )

        log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # The run itself completes (does not raise); the failure is recorded
        # per-course in the SyncLog, and the course row was still created
        # from its otherwise-valid course.yaml (the cohort entry alone failed).
        self.assertTrue(log.errors)
        self.assertTrue(
            any("name" in str(e.get("error", "")) for e in log.errors)
        )
        self.assertTrue(Course.objects.filter(slug="buildcamp-1659").exists())
        self.assertFalse(
            Cohort.objects.filter(
                course__slug="buildcamp-1659", external_key="cohort-4",
            ).exists()
        )

    def test_cohorts_not_a_list_fails_only_that_course(self):
        self._write_course_yaml(extras="cohorts: not-a-list\n")

        log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertTrue(log.errors)
        self.assertTrue(Course.objects.filter(slug="buildcamp-1659").exists())
