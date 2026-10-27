from django.core.exceptions import SuspiciousFileOperation
from whitenoise.storage import CompressedManifestStaticFilesStorage

# Vendored theme folder under static/. Its CSS/JS reference files the theme doesn't
# ship (source maps, and @imports into the theme author's node_modules).
VENDORED_THEME = "matdash/"


class StaticFilesStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's compressed, cache-busting storage, tolerant of the theme's dangling references.

    collectstatic normally fails when a CSS/JS file references a missing file. In
    files under VENDORED_THEME such references are left unchanged instead; in any
    other file they still fail the build.
    """

    def url_converter(self, name, hashed_files, template=None, ignored_blocks=None):
        converter = super().url_converter(name, hashed_files, template, ignored_blocks)
        if not name.replace("\\", "/").startswith(VENDORED_THEME):  # Windows paths use backslashes
            return converter

        def tolerant_converter(matchobj):
            try:
                return converter(matchobj)
            except (ValueError, SuspiciousFileOperation):
                return matchobj["matched"]

        return tolerant_converter
