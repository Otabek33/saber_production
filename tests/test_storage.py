"""Production static storage: lenient with the vendored theme, strict with our own files."""

import re

import pytest

from finance_saber.core.storage import StaticFilesStorage

CSS_URL = re.compile(r"(?P<matched>url\((?P<url>.*?)\))")


def test_missing_reference_in_vendored_theme_is_left_unchanged(tmp_path):
    storage = StaticFilesStorage(location=tmp_path)
    match = CSS_URL.search("url(missing.css.map)")
    converter = storage.url_converter("matdash\\libs\\lib.css", {})  # Windows-style name
    assert converter(match) == "url(missing.css.map)"


def test_missing_reference_in_project_files_still_fails(tmp_path):
    storage = StaticFilesStorage(location=tmp_path)
    match = CSS_URL.search("url(missing.png)")
    with pytest.raises(ValueError):
        storage.url_converter("css/app.css", {})(match)
