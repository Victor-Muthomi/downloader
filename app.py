"""NexDown Flask application."""

import logging

import os

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

from config import ALLOWED_FORMATS, DOWNLOADS_DIR, FLASK_DEBUG, HOST, MAX_BATCH_SIZE, PORT, RATE_LIMIT_STORAGE, SECRET_KEY
from download_manager import (
    cancel_task,
    downloads_enabled,
    enqueue_download,
    get_task,
    list_active_tasks,
    set_downloads_enabled,
    start_background_threads,
    verify_file_access,
)
from helpers import check_ffmpeg, get_health_status, probe_url, sanitize_error, validate_url

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.config['SECRET_KEY'] = SECRET_KEY

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=['200 per hour'],
    storage_uri=RATE_LIMIT_STORAGE,
)


def _validate_startup() -> None:
    ffmpeg_ok, ffmpeg_msg = check_ffmpeg()
    if not ffmpeg_ok:
        app.logger.error('Startup check failed: %s', ffmpeg_msg)
        set_downloads_enabled(False)
    else:
        app.logger.info('FFmpeg OK: %s', ffmpeg_msg)

    health = get_health_status()
    if health['status'] != 'ok':
        app.logger.warning('Health check degraded: %s', health)
        if not health['downloads_writable'] or not health['disk']['ok']:
            set_downloads_enabled(False)


_validate_startup()
start_background_threads()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health', methods=['GET'])
@limiter.exempt
def health():
    body = get_health_status()
    body['downloads_enabled'] = downloads_enabled()
    code = 200 if body['status'] == 'ok' and downloads_enabled() else 503
    return jsonify(body), code


@app.route('/probe', methods=['POST'])
@limiter.limit('30 per minute')
def probe():
    if not downloads_enabled():
        return jsonify({'status': 'error', 'message': 'Downloads are temporarily disabled.'}), 503

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid request body.'}), 400

    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'status': 'error', 'message': 'No URL provided.'}), 400
    if not validate_url(url):
        return jsonify({'status': 'error', 'message': 'Invalid URL. Use a full http:// or https:// link.'}), 400

    try:
        result = probe_url(url)
        return jsonify({'status': 'success', **result})
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'status': 'error', 'message': sanitize_error(str(e))}), 400
    except yt_dlp.utils.ExtractorError as e:
        return jsonify({'status': 'error', 'message': sanitize_error(str(e))}), 400
    except Exception as e:
        app.logger.exception('Probe failed')
        return jsonify({'status': 'error', 'message': sanitize_error(str(e))}), 400


@app.route('/download', methods=['POST'])
@limiter.limit('15 per minute')
def start_download():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid request body.'}), 400

    url = (data.get('url') or '').strip()
    fmt = (data.get('format') or 'best').strip()
    try:
        threads = int(data.get('threads') or 4)
    except (ValueError, TypeError):
        threads = 4

    if not url:
        return jsonify({'status': 'error', 'message': 'No URL provided.'}), 400
    if not validate_url(url):
        return jsonify({'status': 'error', 'message': 'Invalid URL. Use a full http:// or https:// link.'}), 400
    if fmt not in ALLOWED_FORMATS:
        return jsonify({
            'status': 'error',
            'message': f'Unsupported format. Choose from: {", ".join(ALLOWED_FORMATS.keys())}',
        }), 400

    threads = max(1, min(threads, 16))
    result = enqueue_download(url, fmt, threads)
    if result['status'] == 'error':
        return jsonify(result), 429
    return jsonify({**result, 'message': 'Download queued'})


@app.route('/download/batch', methods=['POST'])
@limiter.limit('10 per minute')
def start_batch_download():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid request body.'}), 400

    urls = data.get('urls') or []
    if not isinstance(urls, list) or not urls:
        return jsonify({'status': 'error', 'message': 'No URLs provided.'}), 400

    fmt = (data.get('format') or 'best').strip()
    try:
        threads = int(data.get('threads') or 4)
    except (ValueError, TypeError):
        threads = 4

    if fmt not in ALLOWED_FORMATS:
        return jsonify({
            'status': 'error',
            'message': f'Unsupported format. Choose from: {", ".join(ALLOWED_FORMATS.keys())}',
        }), 400

    threads = max(1, min(threads, 16))

    seen = set()
    unique_urls = []
    for raw in urls[:MAX_BATCH_SIZE]:
        url = (raw or '').strip()
        if url and url not in seen:
            seen.add(url)
            unique_urls.append(url)

    if not unique_urls:
        return jsonify({'status': 'error', 'message': 'No valid URLs provided.'}), 400

    tasks = []
    errors = []
    for url in unique_urls:
        if not validate_url(url):
            errors.append({'url': url, 'message': 'Invalid URL.'})
            continue
        result = enqueue_download(url, fmt, threads)
        if result['status'] == 'success':
            tasks.append({'task_id': result['task_id'], 'url': url})
        else:
            errors.append({'url': url, 'message': result['message']})

    if not tasks:
        return jsonify({
            'status': 'error',
            'message': errors[0]['message'] if errors else 'Could not queue downloads.',
            'errors': errors,
        }), 429

    return jsonify({
        'status': 'success',
        'message': f'Queued {len(tasks)} download(s)',
        'tasks': tasks,
        'errors': errors,
    })


@app.route('/status/<task_id>', methods=['GET'])
@limiter.exempt
def get_status(task_id):
    task = get_task(task_id)
    if not task:
        return jsonify({'status': 'error', 'message': 'Task not found. It may have expired.'}), 404
    return jsonify(task)


@app.route('/tasks/active', methods=['GET'])
@limiter.exempt
def active_tasks():
    return jsonify({'tasks': list_active_tasks()})


@app.route('/cancel/<task_id>', methods=['POST'])
@limiter.limit('30 per minute')
def cancel_download(task_id):
    result = cancel_task(task_id)
    code = 200 if result['status'] == 'success' else 400
    return jsonify(result), code


@app.route('/file/<task_id>', methods=['GET'])
@limiter.limit('30 per minute')
def serve_file(task_id):
    token = request.args.get('token', '')
    if not token:
        abort(404)
    filename = verify_file_access(task_id, token)
    if not filename:
        abort(404)
    safe_name = os.path.basename(filename)
    fpath = DOWNLOADS_DIR / safe_name
    if not fpath.is_file():
        abort(404)
    return send_from_directory(str(DOWNLOADS_DIR), safe_name, as_attachment=True)


@app.errorhandler(404)
def not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'status': 'error', 'message': 'Resource not found.'}), 404
    return render_template('index.html'), 404


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({
        'status': 'error',
        'message': 'Too many requests. Please wait a minute before trying again.',
    }), 429


@app.errorhandler(500)
def server_error(e):
    app.logger.exception('Internal server error')
    return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500


if __name__ == '__main__':
    app.run(debug=FLASK_DEBUG, port=PORT, host=HOST)
