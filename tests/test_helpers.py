"""Tests for URL validation and error sanitization."""

import pytest

from helpers import sanitize_error, validate_url


@pytest.mark.parametrize('url,expected', [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', True),
    ('http://example.com/video', True),
    ('ftp://example.com/video', False),
    ('not-a-url', False),
    ('', False),
    ('https://localhost/video', False),
    ('https://127.0.0.1/video', False),
])
def test_validate_url(url, expected):
    assert validate_url(url) is expected


def test_sanitize_error_unsupported():
    msg = sanitize_error('Unsupported URL: foo')
    assert 'not supported' in msg.lower()


def test_sanitize_error_truncates_paths():
    msg = sanitize_error('Error at /home/user/secret/path/file.py line 1')
    assert '/home/' not in msg
