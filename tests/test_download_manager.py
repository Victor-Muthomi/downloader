"""Tests for download manager queue behavior."""

import download_manager as dm


def test_enqueue_returns_task_id():
    dm.set_downloads_enabled(True)
    result = dm.enqueue_download('https://example.com/video', 'best', 4)
    assert result['status'] == 'success'
    assert 'task_id' in result
    tid = result['task_id']
    dm.cancel_task(tid)


def test_cancel_unknown_task():
    result = dm.cancel_task('00000000-0000-0000-0000-000000000000')
    assert result['status'] == 'error'


def test_verify_file_access_invalid():
    assert dm.verify_file_access('nope', 'bad-token') is None
