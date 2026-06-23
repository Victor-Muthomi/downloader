import os
import re
import time
import uuid
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

MAX_FILE_AGE_HOURS = 2          # auto-delete files older than this
CLEANUP_INTERVAL_SECS = 600     # run cleanup every 10 minutes
ALLOWED_FORMATS = {
    'best':      'bestvideo+bestaudio/best',
    '720p':      'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
    '480p':      'bestvideo[height<=480]+bestaudio/best[height<=480]/best',
    'audio':     'bestaudio[ext=m4a]/bestaudio/best',
}

# YouTube URL validation regex
YT_URL_RE = re.compile(
    r'^(https?://)?(www\.)?(youtube\.com/(watch\?.*v=|shorts/|embed/|live/)|youtu\.be/)[A-Za-z0-9_\-]{11}'
)
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# In-memory task store  (task_id -> dict)
download_tasks: dict = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_error(error_msg: str) -> str:
    """Return a user-friendly error message instead of raw yt-dlp tracebacks."""
    msg = str(error_msg)
    if 'is not a valid URL' in msg or 'Unsupported URL' in msg:
        return 'The URL provided is not supported. Please enter a valid YouTube video link.'
    if 'Video unavailable' in msg or 'Private video' in msg:
        return 'This video is unavailable. It may be private, deleted, or region-locked.'
    if 'Sign in to confirm' in msg or 'age' in msg.lower():
        return 'This video requires age verification and cannot be downloaded.'
    if 'HTTP Error 403' in msg:
        return 'Access denied by YouTube. Please try again later.'
    if 'HTTP Error 429' in msg:
        return 'YouTube is rate-limiting requests. Please wait a few minutes and try again.'
    if 'Network' in msg or 'connection' in msg.lower() or 'timed out' in msg.lower():
        return 'A network error occurred. Please check your connection and try again.'
    if 'ffmpeg' in msg.lower() or 'postprocessor' in msg.lower():
        return 'Post-processing failed. FFmpeg may not be installed on the server.'
    # Generic fallback — hide internal paths
    return re.sub(r'/[^\s]+/', '.../', msg)[:300]


def validate_youtube_url(url: str) -> bool:
    """Strict regex validation for YouTube URLs."""
    return bool(YT_URL_RE.match(url.strip()))


def progress_hook(task_id: str, info: dict):
    """Called by yt-dlp during download to report progress."""
    if task_id not in download_tasks:
        return

    if info.get('status') == 'downloading':
        raw = ANSI_RE.sub('', info.get('_percent_str', '0%')).strip()
        try:
            percent = min(float(raw.replace('%', '')), 99.9)
        except (ValueError, TypeError):
            percent = 0.0
        speed = info.get('_speed_str', '')
        speed = ANSI_RE.sub('', speed).strip() if speed else ''
        eta = info.get('_eta_str', '')
        eta = ANSI_RE.sub('', eta).strip() if eta else ''
        msg = f"Downloading: {raw}"
        if speed:
            msg += f"  •  {speed}"
        if eta:
            msg += f"  •  ETA {eta}"

        download_tasks[task_id].update({
            'status': 'processing',
            'progress': percent,
            'message': msg,
        })

    elif info.get('status') == 'finished':
        download_tasks[task_id].update({
            'status': 'processing',
            'progress': 100,
            'message': 'Merging audio & video streams…',
        })


def download_worker(task_id: str, url: str, fmt: str, threads: int = 4):
    """Background thread that performs the actual download."""
    try:
        ydl_opts = {
            'format': ALLOWED_FORMATS.get(fmt, 'bestvideo+bestaudio/best'),
            'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title).80s.%(ext)s'),
            'progress_hooks': [lambda d: progress_hook(task_id, d)],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'updatetime': False,
            'restrictfilenames': True,
            'concurrent_fragment_downloads': threads,
            'merge_output_format': 'mp4' if fmt != 'audio' else None,
            'postprocessor_args': {
                'ffmpeg': ['-async', '1', '-metadata:s:v:0', 'handler_name=VideoHandler', '-metadata:s:a:0', 'handler_name=SoundHandler']
            },
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }] if fmt != 'audio' else [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
            }],
            'socket_timeout': 45,
            'retries': 5,
            'fragment_retries': 5,
        }
        # Remove None values
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # yt-dlp may change ext after merge
            if fmt != 'audio' and not filename.endswith('.mp4'):
                possible = os.path.splitext(filename)[0] + '.mp4'
                if os.path.exists(possible):
                    filename = possible

            basename = os.path.basename(filename)
            download_tasks[task_id].update({
                'status': 'success',
                'progress': 100,
                'message': 'Download complete!',
                'filename': basename,
                'title': info.get('title', basename),
                'duration': info.get('duration_string', ''),
                'thumbnail': info.get('thumbnail', ''),
            })
            app.logger.info('Download complete: %s', basename)

    except yt_dlp.utils.DownloadError as e:
        app.logger.error('yt-dlp DownloadError: %s', e)
        download_tasks[task_id].update({
            'status': 'error',
            'progress': 0,
            'message': sanitize_error(str(e)),
        })
    except yt_dlp.utils.ExtractorError as e:
        app.logger.error('yt-dlp ExtractorError: %s', e)
        download_tasks[task_id].update({
            'status': 'error',
            'progress': 0,
            'message': sanitize_error(str(e)),
        })
    except OSError as e:
        app.logger.error('OS error during download: %s', e)
        download_tasks[task_id].update({
            'status': 'error',
            'progress': 0,
            'message': 'Server storage error. Please try again later.',
        })
    except Exception as e:
        app.logger.exception('Unexpected error during download')
        download_tasks[task_id].update({
            'status': 'error',
            'progress': 0,
            'message': sanitize_error(str(e)),
        })


# ---------------------------------------------------------------------------
# Automatic file cleanup
# ---------------------------------------------------------------------------
def cleanup_old_files():
    """Delete files in downloads/ older than MAX_FILE_AGE_HOURS."""
    while True:
        try:
            cutoff = time.time() - (MAX_FILE_AGE_HOURS * 3600)
            for fname in os.listdir(DOWNLOADS_DIR):
                fpath = os.path.join(DOWNLOADS_DIR, fname)
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    app.logger.info('Cleanup: deleted %s', fname)
        except Exception:
            app.logger.exception('Error during cleanup')
        time.sleep(CLEANUP_INTERVAL_SECS)

_cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
_cleanup_thread.start()

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/download', methods=['POST'])
@limiter.limit("15 per minute")
def start_download():
    """Accept a URL + format, kick off background download, return task_id."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'Invalid request body.'}), 400

    url = (data.get('url') or '').strip()
    fmt = (data.get('format') or 'best').strip()
    try:
        threads = int(data.get('threads') or 4)
    except (ValueError, TypeError):
        threads = 4

    # --- Validation ---
    if not url:
        return jsonify({'status': 'error', 'message': 'No URL provided.'}), 400
    if not validate_youtube_url(url):
        return jsonify({
            'status': 'error',
            'message': 'Invalid YouTube URL. Accepted formats: youtube.com/watch?v=…, youtu.be/…, youtube.com/shorts/…'
        }), 400
    if fmt not in ALLOWED_FORMATS:
        return jsonify({'status': 'error', 'message': f'Unsupported format. Choose from: {", ".join(ALLOWED_FORMATS.keys())}'}), 400
    
    # Thread validation (safe range 1-16)
    threads = max(1, min(threads, 16))

    task_id = str(uuid.uuid4())
    download_tasks[task_id] = {
        'status': 'processing',
        'progress': 0,
        'message': 'Initializing download…',
        'created_at': time.time(),
    }

    thread = threading.Thread(target=download_worker, args=(task_id, url, fmt, threads), daemon=True)
    thread.start()

    return jsonify({'status': 'success', 'task_id': task_id, 'message': 'Download started'})


@app.route('/status/<task_id>', methods=['GET'])
@limiter.exempt
def get_status(task_id):
    """Return current progress for a download task."""
    task = download_tasks.get(task_id)
    if not task:
        return jsonify({'status': 'error', 'message': 'Task not found. It may have expired.'}), 404
    # Don't expose internal fields
    safe = {k: v for k, v in task.items() if k != 'created_at'}
    return jsonify(safe)


@app.route('/file/<path:filename>', methods=['GET'])
@limiter.limit("30 per minute")
def serve_file(filename):
    """Serve a downloaded file to the user's browser for saving to device."""
    # Prevent directory traversal
    safe_name = os.path.basename(filename)
    fpath = os.path.join(DOWNLOADS_DIR, safe_name)
    if not os.path.isfile(fpath):
        abort(404)
    return send_from_directory(DOWNLOADS_DIR, safe_name, as_attachment=True)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({'status': 'error', 'message': 'Resource not found.'}), 404
    return render_template('index.html'), 404

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({
        'status': 'error',
        'message': 'Too many requests. Please wait a minute before trying again.'
    }), 429

@app.errorhandler(500)
def server_error(e):
    app.logger.exception('Internal server error')
    return jsonify({'status': 'error', 'message': 'An internal server error occurred.'}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
