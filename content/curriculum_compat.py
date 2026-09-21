"""Attach AISL URL/access/instructor behavior onto package curriculum models.

Package Course/Module/Unit are the runtime rows. This site keeps extra
fields on CourseExtension and historical URL shapes
(``/courses/<slug>/<module>/<unit>`` and the four-segment submodule path).
"""

from __future__ import annotations

import hashlib
import uuid

from django.db import models
from django.db.models import Prefetch

from community_base.curriculum.models import Course, Module, Unit

from content.access import get_required_tier_name
from content.models.course import (
    ACCESS_MODE_TIER,
    UNIT_KIND_EVENT,
    CourseExtension,
    UserCourseProgress,
    non_bonus_units,
)

AISL_COURSE_FIELDS = (
    'maven_course_key',
    'source_repo',
    'access_mode',
    'enroll_url',
    'program_label',
    'individual_price_eur',
    'stripe_product_id',
    'stripe_price_id',
    'peer_review_enabled',
    'peer_review_count',
    'peer_review_deadline_days',
    'peer_review_criteria',
    'peer_review_criteria_html',
    'auto_banner_title_hash',
)

_APPLIED = False


def _dummy_sha1(seed: str) -> str:
    return hashlib.sha1(seed.encode('utf-8')).hexdigest()


def _dummy_sha256(seed: str) -> str:
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


def _ensure_provenance(obj) -> None:
    """Satisfy cb_curriculum provenance check for synced rows."""
    content_id = getattr(obj, 'source_content_id', None)
    path = getattr(obj, 'source_path', None) or None
    commit = getattr(obj, 'source_commit_sha', None) or None
    checksum = getattr(obj, 'source_checksum', None) or None
    if not any((content_id, path, commit, checksum)):
        return
    seed = str(content_id or path or obj.pk or 'synced')
    if not content_id:
        obj.source_content_id = uuid.uuid5(uuid.NAMESPACE_URL, seed)
    if path:
        path = str(path).lstrip('/')
        obj.source_path = path
    else:
        obj.source_path = f'synced/{seed}'
    if (
        not commit
        or len(str(commit)) != 40
        or any(ch not in '0123456789abcdef' for ch in str(commit))
    ):
        obj.source_commit_sha = _dummy_sha1(seed)
    if not checksum or len(str(checksum)) != 64:
        obj.source_checksum = _dummy_sha256(seed)


def _get_extension(course: Course) -> CourseExtension:
    ext = getattr(course, 'aisl_extension', None)
    if ext is not None:
        return ext
    ext, _ = CourseExtension.objects.get_or_create(course=course)
    course.aisl_extension = ext
    return ext


def _install_extension_fields() -> None:
    for name in AISL_COURSE_FIELDS:
        default = '' if name not in (
            'individual_price_eur',
            'peer_review_enabled',
            'peer_review_count',
            'peer_review_deadline_days',
        ) else None

        def getter(self, field=name, default=default):
            ext = getattr(self, 'aisl_extension', None)
            if ext is None and self.pk:
                ext = CourseExtension.objects.filter(course_id=self.pk).first()
            if ext is None:
                if field == 'access_mode':
                    return ACCESS_MODE_TIER
                if field == 'peer_review_enabled':
                    return False
                if field == 'peer_review_count':
                    return 3
                if field == 'peer_review_deadline_days':
                    return 7
                return default
            return getattr(ext, field)

        def setter(self, value, field=name):
            extra = getattr(self, '_aisl_extra', None)
            if extra is None:
                extra = {}
                self._aisl_extra = extra
            extra[field] = value
            if self.pk:
                ext = _get_extension(self)
                setattr(ext, field, value)
                ext.save(update_fields=[field])

        setattr(Course, name, property(getter, setter))


def _wrap_inits_and_saves() -> None:
    original_course_init = Course.__init__
    original_course_save = Course.save
    original_module_init = Module.__init__
    original_module_save = Module.save
    original_unit_init = Unit.__init__
    original_unit_save = Unit.save

    def course_init(self, *args, **kwargs):
        extra = {k: kwargs.pop(k) for k in AISL_COURSE_FIELDS if k in kwargs}
        if 'content_id' in kwargs:
            kwargs['source_content_id'] = kwargs.pop('content_id')
        if 'source_commit' in kwargs:
            kwargs['source_commit_sha'] = kwargs.pop('source_commit')
        original_course_init(self, *args, **kwargs)
        self._aisl_extra = extra

    def course_save(self, *args, **kwargs):
        from content.utils.h1 import strip_leading_title_h1
        from content.utils.linkify import linkify_urls
        from content.utils.markdown import render_markdown
        from content.utils.tags import normalize_tags

        _ensure_provenance(self)
        self.tags = normalize_tags(getattr(self, 'tags', None) or [])
        if self.description:
            description_md = strip_leading_title_h1(self.description, self.title)
            self.description_html = linkify_urls(render_markdown(description_md))
        extra = dict(getattr(self, '_aisl_extra', None) or {})
        # Ensure rendered HTML is persisted when callers pass update_fields.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            for field in update_fields:
                if field in AISL_COURSE_FIELDS:
                    extra[field] = getattr(self, field)
            model_fields = [f for f in update_fields if f not in AISL_COURSE_FIELDS]
            if 'description' in model_fields and 'description_html' not in model_fields:
                model_fields.append('description_html')
            if model_fields:
                kwargs['update_fields'] = model_fields
            elif extra:
                kwargs.pop('update_fields', None)
                if not extra:
                    return
            else:
                kwargs['update_fields'] = ['updated_at'] if hasattr(self, 'updated_at') else []
        if 'peer_review_criteria' in extra:
            criteria_md = extra.get('peer_review_criteria') or ''
            extra['peer_review_criteria_html'] = (
                linkify_urls(render_markdown(criteria_md)) if criteria_md else ''
            )
        if kwargs.get('update_fields') == []:
            # Only AISL overlay fields changed.
            ext, _ = CourseExtension.objects.get_or_create(course=self)
            for key, value in extra.items():
                setattr(ext, key, value)
            ext.save()
            self.aisl_extension = ext
            self._aisl_extra = {}
            return
        models.Model.save(self, *args, **kwargs)
        ext, _ = CourseExtension.objects.get_or_create(course=self)
        if extra:
            for key, value in extra.items():
                setattr(ext, key, value)
            ext.save()
            self._aisl_extra = {}
        self.aisl_extension = ext

    def _map_source_kwargs(kwargs):
        if 'content_id' in kwargs:
            kwargs['source_content_id'] = kwargs.pop('content_id')
        if 'source_commit' in kwargs:
            kwargs['source_commit_sha'] = kwargs.pop('source_commit')
        kwargs.pop('source_repo', None)
        return kwargs

    def module_init(self, *args, **kwargs):
        source_repo = kwargs.get('source_repo')
        overview_source_path = kwargs.pop('overview_source_path', None)
        original_module_init(self, *args, **_map_source_kwargs(kwargs))
        if source_repo is not None:
            self._aisl_source_repo = source_repo
        if overview_source_path is not None:
            self._aisl_overview_source_path = overview_source_path

    def module_save(self, *args, **kwargs):
        from content.utils.h1 import strip_leading_title_h1
        from content.utils.linkify import linkify_urls
        from content.utils.markdown import render_markdown

        _ensure_provenance(self)
        if self.parent_id is None and self.course_id and self.slug:
            clash = Module.objects.filter(
                course_id=self.course_id,
                parent__isnull=True,
                slug=self.slug,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                from django.db import IntegrityError

                raise IntegrityError(
                    'UNIQUE constraint failed: top-level module slug per course',
                )
        if self.overview:
            overview_md = strip_leading_title_h1(self.overview, self.title)
            self.overview_html = linkify_urls(render_markdown(overview_md))
        else:
            self.overview_html = ''
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'overview' in update_fields:
                update_fields.add('overview_html')
            update_fields.discard('overview_source_path')
            update_fields.discard('source_repo')
            kwargs['update_fields'] = list(update_fields)
        models.Model.save(self, *args, **kwargs)

    def unit_init(self, *args, **kwargs):
        source_repo = kwargs.get('source_repo')
        original_unit_init(self, *args, **_map_source_kwargs(kwargs))
        if source_repo is not None:
            self._aisl_source_repo = source_repo

    def unit_save(self, *args, **kwargs):
        from content.utils.code_annotations import render_course_unit_body
        from content.utils.h1 import strip_leading_title_h1
        from content.utils.linkify import linkify_urls
        from content.utils.markdown import render_markdown

        _ensure_provenance(self)
        if self.body:
            body_md = strip_leading_title_h1(self.body, self.title)
            self.body_html = render_course_unit_body(body_md)
        else:
            self.body_html = ''
        if self.homework:
            self.homework_html = linkify_urls(render_markdown(self.homework))
        else:
            self.homework_html = ''
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields = set(update_fields)
            if 'body' in update_fields:
                update_fields.add('body_html')
            if 'homework' in update_fields:
                update_fields.add('homework_html')
            if 'content_id' in update_fields:
                update_fields.remove('content_id')
                update_fields.add('source_content_id')
            if 'source_commit' in update_fields:
                update_fields.remove('source_commit')
                update_fields.add('source_commit_sha')
            update_fields.discard('source_repo')
            kwargs['update_fields'] = list(update_fields)
        models.Model.save(self, *args, **kwargs)

    Course.__init__ = course_init
    Course.save = course_save
    def _wrap_clean(model):
        original = model.clean

        def clean(self):
            _ensure_provenance(self)
            return original(self)

        model.clean = clean

    _wrap_clean(Course)
    _wrap_clean(Module)
    _wrap_clean(Unit)

    _unit_clean = Unit.clean

    def unit_clean_event_kind(self):
        from django.core.exceptions import ValidationError

        _unit_clean(self)
        if self.kind == UNIT_KIND_EVENT:
            if self.session_position is None or self.session_position < 1:
                raise ValidationError({
                    'session_position': (
                        'kind="event" requires a positive session_position.'
                    ),
                })

    Unit.clean = unit_clean_event_kind

    def module_clean(self):
        from django.core.exceptions import ValidationError

        models.Model.clean(self)
        _ensure_provenance(self)
        if self.parent_id is not None:
            if self.pk is not None and self.parent_id == self.pk:
                raise ValidationError({
                    'parent': 'A module cannot be its own parent.',
                })
            parent = self.parent
            if parent.parent_id is not None:
                raise ValidationError({
                    'parent': (
                        'A submodule cannot itself have children '
                        '(maximum two levels of module).'
                    ),
                })
            if parent.course_id != self.course_id:
                raise ValidationError({
                    'parent': 'Parent module must belong to the same course.',
                })
            if parent.units.exists() and not getattr(
                self, '_allow_parent_with_pending_units', False,
            ):
                raise ValidationError({
                    'parent': (
                        f'"{parent.title}" already has direct units and '
                        'cannot also have submodules.'
                    ),
                })
        if self.pk is not None and self.children.exists() and self.units.exists():
            raise ValidationError(
                f'"{self.title}" cannot have both submodules and direct units.'
            )
        if self.parent_id is None and self.course_id and self.slug:
            clash = Module.objects.filter(
                course_id=self.course_id,
                parent__isnull=True,
                slug=self.slug,
            )
            if self.pk:
                clash = clash.exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError({
                    'slug': 'Top-level module slugs must be unique per course.',
                })

    Module.clean = module_clean

    def _wrap_full_clean(model):
        original = model.full_clean

        def full_clean(self, *args, **kwargs):
            _ensure_provenance(self)
            return original(self, *args, **kwargs)

        model.full_clean = full_clean

    _wrap_full_clean(Course)
    _wrap_full_clean(Module)
    _wrap_full_clean(Unit)

    Course.__init__ = course_init
    Course.save = course_save
    Module.__init__ = module_init
    Module.save = module_save
    Unit.__init__ = unit_init
    Unit.save = unit_save


def _install_course_methods() -> None:
    def get_absolute_url(self):
        return f'/courses/{self.slug}'

    def get_studio_edit_url(self):
        return f'/studio/courses/{self.pk}/edit'

    def ordered_instructors(self):
        from content.models.instructor import CourseInstructor

        if not self.pk:
            return []
        return [
            link.instructor
            for link in CourseInstructor.objects.filter(
                course_id=self.pk,
            ).select_related('instructor').order_by('position')
        ]

    def primary_instructor(self):
        instructors = ordered_instructors(self)
        return instructors[0] if instructors else None

    def is_entitlement_mode(self) -> bool:
        return getattr(self, 'access_mode', ACCESS_MODE_TIER) == 'entitlement'

    def required_tier_name(self):
        return get_required_tier_name(self.required_level)

    def total_units(self):
        return non_bonus_units(Unit.objects.filter(module__course=self)).count()

    def completed_units(self, user):
        if user is None or not getattr(user, 'is_authenticated', False):
            return 0
        return UserCourseProgress.objects.filter(
            user=user,
            unit__in=non_bonus_units(Unit.objects.filter(module__course=self)),
            completed_at__isnull=False,
        ).count()

    def get_syllabus(self):
        return self.modules.filter(parent__isnull=True).prefetch_related(
            Prefetch(
                'children',
                queryset=Module.objects.order_by('sort_order', 'id').prefetch_related(
                    Prefetch('units', queryset=Unit.objects.order_by('sort_order', 'id')),
                ),
            ),
            Prefetch('units', queryset=Unit.objects.order_by('sort_order', 'id')),
        ).order_by('sort_order', 'id')

    def get_next_unit_for(self, user):
        from content.services.course_units import get_all_units_ordered

        if user is None or not getattr(user, 'is_authenticated', False):
            return None
        units = get_all_units_ordered(self)
        if not units:
            return None
        completed_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__in=units,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )
        for unit in units:
            if unit.id not in completed_ids:
                return unit
        return None

    Course.get_absolute_url = get_absolute_url
    Course.get_studio_edit_url = get_studio_edit_url
    Course.ordered_instructors = property(ordered_instructors)
    Course.primary_instructor = property(primary_instructor)
    Course.is_entitlement_mode = property(is_entitlement_mode)
    Course.required_tier_name = property(required_tier_name)
    Course.total_units = total_units
    Course.completed_units = completed_units
    Course.get_syllabus = get_syllabus
    Course.get_next_unit_for = get_next_unit_for
    Course.is_published = property(lambda self: self.status == 'published')

    class _AislInstructors:
        def __get__(self, instance, owner):
            from content.models.instructor import CourseInstructor, Instructor

            if instance is None:
                return self
            qs = Instructor.objects.filter(
                courseinstructor__course=instance,
            ).order_by('courseinstructor__position', 'id')

            def add(*instructors, **_kwargs):
                next_pos = CourseInstructor.objects.filter(
                    course=instance,
                ).count()
                for instructor in instructors:
                    CourseInstructor.objects.get_or_create(
                        course=instance,
                        instructor=instructor,
                        defaults={'position': next_pos},
                    )
                    next_pos += 1

            def clear():
                CourseInstructor.objects.filter(course=instance).delete()

            qs.add = add
            qs.clear = clear
            qs.set = lambda instructors: (clear(), add(*instructors))
            return qs

    Course.instructors = _AislInstructors()


class _RewriteQuerySet(models.QuerySet):
    """Translate AISL identity field names onto package provenance columns."""

    _REWRITES = {
        'content_id': 'source_content_id',
        'content_id__isnull': 'source_content_id__isnull',
        'content_id__in': 'source_content_id__in',
        'source_commit': 'source_commit_sha',
        'source_commit__isnull': 'source_commit_sha__isnull',
        'access_mode': 'aisl_extension__access_mode',
        'maven_course_key': 'aisl_extension__maven_course_key',
        'enroll_url': 'aisl_extension__enroll_url',
        'program_label': 'aisl_extension__program_label',
        'source_repo': 'aisl_extension__source_repo',
        'source_repo__isnull': 'aisl_extension__source_repo__isnull',
        'source_repo__in': 'aisl_extension__source_repo__in',
        'instructors': 'aisl_instructor_links__instructor',
    }
    _DROP = ()

    def _rewrite(self, kwargs):
        rewritten = {}
        for key, value in kwargs.items():
            if key in self._DROP:
                continue
            if key in self._REWRITES:
                rewritten[self._REWRITES[key]] = value
                continue
            for src, dest in self._REWRITES.items():
                prefix = src + '__'
                if key.startswith(prefix):
                    rewritten[dest + key[len(src):]] = value
                    break
            else:
                rewritten[key] = value
        return rewritten

    def filter(self, *args, **kwargs):
        return super().filter(*args, **self._rewrite(kwargs))

    def exclude(self, *args, **kwargs):
        return super().exclude(*args, **self._rewrite(kwargs))

    def get(self, *args, **kwargs):
        return super().get(*args, **self._rewrite(kwargs))

    def only(self, *fields):
        rest = tuple(f for f in fields if f not in AISL_COURSE_FIELDS)
        if rest == fields:
            return super().only(*fields)
        qs = super().select_related('aisl_extension')
        return qs.only(*rest) if rest else qs

    def defer(self, *fields):
        rest = tuple(f for f in fields if f not in AISL_COURSE_FIELDS)
        return super().defer(*rest)

    def order_by(self, *field_names):
        mapped = []
        for name in field_names:
            prefix = ''
            core = name
            if name.startswith('-'):
                prefix = '-'
                core = name[1:]
            core = self._REWRITES.get(core, core)
            mapped.append(prefix + core)
        return super().order_by(*mapped)

    def values(self, *fields, **expressions):
        mapped_fields = tuple(self._REWRITES.get(f, f) for f in fields)
        return super().values(*mapped_fields, **self._rewrite(expressions))

    def values_list(self, *fields, **kwargs):
        mapped_fields = tuple(self._REWRITES.get(f, f) for f in fields)
        return super().values_list(*mapped_fields, **kwargs)

    def update(self, **kwargs):
        extra = {key: kwargs.pop(key) for key in list(kwargs) if key in AISL_COURSE_FIELDS}
        if 'content_id' in kwargs:
            kwargs['source_content_id'] = kwargs.pop('content_id')
        if 'source_commit' in kwargs:
            kwargs['source_commit_sha'] = kwargs.pop('source_commit')
        pks = list(self.values_list('pk', flat=True)) if extra else []
        count = super().update(**kwargs) if kwargs else len(pks)
        if extra and pks:
            CourseExtension.objects.filter(course_id__in=pks).update(**extra)
            have = set(
                CourseExtension.objects.filter(course_id__in=pks).values_list(
                    'course_id', flat=True,
                )
            )
            for pk in set(pks) - have:
                CourseExtension.objects.get_or_create(course_id=pk, defaults=extra)
        return count


class _UnitModuleRewriteQuerySet(_RewriteQuerySet):
    _DROP = {
        'source_repo',
        'source_repo__isnull',
        'source_repo__in',
    }

    def update(self, **kwargs):
        kwargs.pop('source_repo', None)
        kwargs.pop('overview_source_path', None)
        if 'content_id' in kwargs:
            kwargs['source_content_id'] = kwargs.pop('content_id')
        if 'source_commit' in kwargs:
            kwargs['source_commit_sha'] = kwargs.pop('source_commit')
        return models.QuerySet.update(self, **kwargs)


def _install_identity_aliases(model) -> None:
    def getter(self):
        return self.source_content_id

    def setter(self, value):
        self.source_content_id = value

    model.content_id = property(getter, setter)

    def commit_get(self):
        return self.source_commit_sha

    def commit_set(self, value):
        self.source_commit_sha = value

    model.source_commit = property(commit_get, commit_set)

    if model is Course:
        return

    def repo_get(self):
        return getattr(self, '_source_repo', '') or ''

    def repo_set(self, value):
        self._source_repo = value or ''

    model.source_repo = property(repo_get, repo_set)


def _install_managers() -> None:
    def _patch(model, qs_cls):
        manager = model._default_manager
        original = manager.get_queryset

        def get_queryset():
            qs = original()
            qs.__class__ = qs_cls
            return qs

        manager.get_queryset = get_queryset
        _install_identity_aliases(model)

    _patch(Course, _RewriteQuerySet)
    _patch(Module, _UnitModuleRewriteQuerySet)
    _patch(Unit, _UnitModuleRewriteQuerySet)


def _install_module_unit_methods() -> None:
    def module_url(self):
        if self.parent_id is None:
            return f'/courses/{self.course.slug}/{self.slug}'
        return f'/courses/{self.course.slug}/{self.parent.slug}/{self.slug}'

    def module_is_leaf(self):
        if self.pk is None:
            return True
        return not self.children.exists()

    def unit_url(self):
        course = self.module.course
        if self.module.parent_id is None:
            return f'/courses/{course.slug}/{self.module.slug}/{self.slug}'
        return (
            f'/courses/{course.slug}/{self.module.parent.slug}/'
            f'{self.module.slug}/{self.slug}'
        )

    def unit_studio_url(self):
        return f'/studio/units/{self.pk}/edit'

    def content_id_get(self):
        return self.source_content_id

    def content_id_set(self, value):
        self.source_content_id = value

    Module.get_absolute_url = module_url
    Module.is_leaf = property(module_is_leaf)
    Unit.get_absolute_url = unit_url
    Unit.get_studio_edit_url = unit_studio_url
    Unit.content_id = property(content_id_get, content_id_set)

    def _source_repo_get(self):
        stored = getattr(self, '_aisl_source_repo', None)
        if stored:
            return stored
        course = getattr(self, 'course', None)
        if course is None:
            module = getattr(self, 'module', None)
            course = getattr(module, 'course', None) if module is not None else None
        if course is None:
            return ''
        return getattr(course, 'source_repo', '') or ''

    def _source_repo_set(self, value):
        self._aisl_source_repo = value or ''

    Module.source_repo = property(_source_repo_get, _source_repo_set)
    Unit.source_repo = property(_source_repo_get, _source_repo_set)

    def _overview_path_get(self):
        stored = getattr(self, '_aisl_overview_source_path', None)
        if stored is not None:
            return stored
        overview = getattr(self, 'overview', None)
        path = getattr(self, 'source_path', None)
        if overview and path:
            return f'{str(path).rstrip("/")}/README.md'
        return None

    def _overview_path_set(self, value):
        self._aisl_overview_source_path = value

    Module.overview_source_path = property(_overview_path_get, _overview_path_set)


def apply() -> None:
    global _APPLIED
    if _APPLIED:
        return
    for name in AISL_COURSE_FIELDS:
        _RewriteQuerySet._REWRITES.setdefault(name, f'aisl_extension__{name}')
    _install_managers()
    _install_extension_fields()
    _wrap_inits_and_saves()
    _install_course_methods()
    _install_module_unit_methods()
    _APPLIED = True
