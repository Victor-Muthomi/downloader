"""Tests for Flask routes."""

from io import BytesIO

import pytest

import app as app_module
from app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_health_endpoint(client):
    res = client.get('/health')
    assert res.status_code in (200, 503)
    data = res.get_json()
    assert 'ffmpeg' in data
    assert 'yt_dlp_version' in data
    assert 'downloads_enabled' in data


def test_download_rejects_private_url(client):
    res = client.post('/download', json={'url': 'http://127.0.0.1/test', 'format': 'best'})
    assert res.status_code == 400


def test_download_missing_url(client):
    res = client.post('/download', json={'format': 'best'})
    assert res.status_code == 400


def test_status_not_found(client):
    res = client.get('/status/nonexistent-id')
    assert res.status_code == 404


def test_file_requires_token(client):
    res = client.get('/file/fake-task-id')
    assert res.status_code == 404


def test_active_tasks(client):
    res = client.get('/tasks/active')
    assert res.status_code == 200
    assert 'tasks' in res.get_json()


def test_probe_rejects_invalid_url(client):
    res = client.post('/probe', json={'url': 'http://localhost/private'})
    assert res.status_code == 400


def test_batch_download_accepts_txt_upload(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        'enqueue_download',
        lambda url, fmt, threads: {'status': 'success', 'task_id': f'task-{url[-1]}', 'url': url},
    )

    data = {
        'format': 'best',
        'threads': '4',
        'urls_file': (BytesIO(b'https://example.com/one\nhttps://example.com/two\n'), 'urls.txt'),
    }

    res = client.post('/download/batch', data=data, content_type='multipart/form-data')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'success'
    assert len(payload['tasks']) == 2


def test_batch_download_accepts_json_urls(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        'enqueue_download',
        lambda url, fmt, threads: {'status': 'success', 'task_id': f'task-{url[-1]}', 'url': url},
    )

    res = client.post(
        '/download/batch',
        json={
            'urls': ['https://example.com/one', 'https://example.com/two'],
            'format': 'best',
            'threads': 4,
        },
    )
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['status'] == 'success'
    assert len(payload['tasks']) == 2
