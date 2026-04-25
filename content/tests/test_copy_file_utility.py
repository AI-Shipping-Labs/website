"""Unit tests for ``content.utils.copy_file.resolve_copy_file_content`` (issue #307).

These are pure utility tests — no DB, no fixtures. Each test lays out a
fake content folder under ``tempfile.TemporaryDirectory`` and exercises
the resolver directly.

The helper is content-type agnostic: it knows nothing about workshops,
courses, articles, units, etc. The tests deliberately avoid that
vocabulary too.
"""
import os
import tempfile

from django.test import SimpleTestCase

from content.utils.copy_file import resolve_copy_file_content


class ExplicitCopyFilePresentTest(SimpleTestCase):
    """copy_file is set and the file exists -> body returned, frontmatter and
    leading H1 stripped."""

    def test_reads_explicit_file_strips_frontmatter_and_leading_h1(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, '01-body.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(
                    '---\n'
                    'title: Hello\n'
                    '---\n'
                    '# Hello\n'
                    '\n'
                    'Intro paragraph.\n',
                )

            body, error = resolve_copy_file_content(folder, '01-body.md')

        self.assertIsNone(error)
        self.assertIsNotNone(body)
        # Frontmatter stripped -> no `title:` key in result.
        self.assertNotIn('title: Hello', body)
        # Leading H1 stripped -> no `# Hello` heading at the top.
        self.assertNotIn('# Hello', body)
        # Body content preserved.
        self.assertIn('Intro paragraph.', body)


class ExplicitCopyFileMissingTest(SimpleTestCase):
    """copy_file is set but the named file is not on disk."""

    def test_missing_file_returns_not_found_error(self):
        with tempfile.TemporaryDirectory() as folder:
            # Only README.md exists; the requested file does not.
            with open(os.path.join(folder, 'README.md'), 'w') as f:
                f.write('# Readme\n\nReadme body.\n')

            body, error = resolve_copy_file_content(
                folder, 'does-not-exist.md',
            )

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn("'does-not-exist.md'", error)
        self.assertIn('not found in', error)
        self.assertIn(folder, error)


class DefaultFallbackPresentTest(SimpleTestCase):
    """copy_file is unset and the default file exists -> default is read."""

    def test_default_readme_used_when_no_copy_file_declared(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'README.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('# Title\n\nReadme body.\n')

            body, error = resolve_copy_file_content(folder, None)

        self.assertIsNone(error)
        self.assertIsNotNone(body)
        # Leading H1 stripped.
        self.assertNotIn('# Title', body)
        self.assertIn('Readme body.', body)


class DefaultMissingTest(SimpleTestCase):
    """copy_file is unset and the default file does not exist -> (None, None)."""

    def test_no_source_no_error_when_default_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            # Only an unrelated non-md file is present.
            with open(os.path.join(folder, 'notes.txt'), 'w') as f:
                f.write('Plain text notes.\n')

            body, error = resolve_copy_file_content(folder, None)

        self.assertIsNone(body)
        self.assertIsNone(error)


class EmptyStringCopyFileBehavesLikeNoneTest(SimpleTestCase):
    """An empty-string ``copy_file`` setting falls through to the default
    fallback (so authors don't have to delete the key — clearing its value
    is enough)."""

    def test_empty_string_with_no_default_present_returns_none_none(self):
        with tempfile.TemporaryDirectory() as folder:
            # No README, no other md.
            body, error = resolve_copy_file_content(folder, '')

        self.assertIsNone(body)
        self.assertIsNone(error)


class TraversalRejectedTest(SimpleTestCase):
    """``..`` in the copy_file value is rejected before any file is opened."""

    def test_dotdot_path_rejected_with_filename_error(self):
        with tempfile.TemporaryDirectory() as folder:
            body, error = resolve_copy_file_content(
                folder, '../other/secret.md',
            )

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn("'../other/secret.md'", error)
        self.assertIn('must be a filename', error)
        self.assertIn('not a path', error)


# ----------------------------------------------------------------------
# Additional coverage required by the spec (recommended scenarios).
# ----------------------------------------------------------------------


class SubdirRejectedTest(SimpleTestCase):
    def test_subdir_path_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            # Even if the subdir file exists, it is rejected.
            sub = os.path.join(folder, 'sub')
            os.makedirs(sub)
            with open(os.path.join(sub, 'foo.md'), 'w') as f:
                f.write('body\n')

            body, error = resolve_copy_file_content(folder, 'sub/foo.md')

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn("'sub/foo.md'", error)
        self.assertIn('must be a filename', error)


class HiddenFilenameRejectedTest(SimpleTestCase):
    def test_leading_dot_filename_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, '.env.md'), 'w') as f:
                f.write('secret\n')

            body, error = resolve_copy_file_content(folder, '.env.md')

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn("'.env.md'", error)
        self.assertIn('must be a filename', error)


class NonMdExtensionRejectedTest(SimpleTestCase):
    def test_non_md_extension_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'notes.txt'), 'w') as f:
                f.write('plain text\n')

            body, error = resolve_copy_file_content(folder, 'notes.txt')

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn("'notes.txt'", error)
        self.assertIn('must be a .md file', error)


class NonStringCopyFileRejectedTest(SimpleTestCase):
    def test_integer_copy_file_rejected_with_string_error(self):
        with tempfile.TemporaryDirectory() as folder:
            body, error = resolve_copy_file_content(folder, 123)

        self.assertIsNone(body)
        self.assertIsNotNone(error)
        self.assertIn('123', error)
        self.assertIn('must be a string', error)


class FrontmatterOnlyTest(SimpleTestCase):
    def test_only_frontmatter_returns_empty_body(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'stub.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('---\ntitle: Stub\n---\n')

            body, error = resolve_copy_file_content(folder, 'stub.md')

        self.assertEqual(body, '')
        self.assertIsNone(error)

    def test_completely_empty_file_returns_empty_body(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'empty.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('')

            body, error = resolve_copy_file_content(folder, 'empty.md')

        self.assertEqual(body, '')
        self.assertIsNone(error)


class NoLeadingH1Test(SimpleTestCase):
    def test_body_without_leading_h1_returned_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'doc.md')
            content = 'Just a paragraph.\n\n## A subheading\n\nMore text.\n'
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)

            body, error = resolve_copy_file_content(folder, 'doc.md')

        self.assertIsNone(error)
        # No H1 to strip -> body content unchanged. (The frontmatter
        # parser may normalise the trailing newline, but the actual body
        # text is intact.)
        self.assertIn('Just a paragraph.', body)
        # The H2 deeper in the document is preserved (we strip H1, not H2).
        self.assertIn('## A subheading', body)
        self.assertIn('More text.', body)
        # No H1 was injected.
        self.assertNotIn('# ', body[:2])


class SetextH1StrippedTest(SimpleTestCase):
    def test_setext_h1_stripped_same_as_atx(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'doc.md')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('Title\n=====\n\nBody paragraph.\n')

            body, error = resolve_copy_file_content(folder, 'doc.md')

        self.assertIsNone(error)
        self.assertNotIn('Title', body)
        self.assertNotIn('=====', body)
        self.assertIn('Body paragraph.', body)


class DefaultNoneDisablesFallbackTest(SimpleTestCase):
    def test_default_none_skips_readme_even_if_present(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'README.md'), 'w') as f:
                f.write('# Readme\n\nReadme body.\n')

            body, error = resolve_copy_file_content(
                folder, None, default=None,
            )

        self.assertIsNone(body)
        self.assertIsNone(error)


class CaseInsensitiveMdExtensionTest(SimpleTestCase):
    def test_uppercase_md_extension_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'doc.MD')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('Body.\n')

            body, error = resolve_copy_file_content(folder, 'doc.MD')

        self.assertIsNone(error)
        self.assertIn('Body.', body)
