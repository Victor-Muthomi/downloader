"""Download queue, workers, task lifecycle, and file token management."""

import glob
import logging
import os
import queue
import secrets
import threading
import time
import uuid
from typing import Optional

import yt_dlp

from config import (
    ALLOWED_FORMATS,
    CLEANUP_INTERVAL_SECS,
    DOWNLOADS_DIR,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_FILE_AGE_HOURS,
    MAX_QUEUE_DEPTH,
    TASK_RETENTION_SECS,
)
from helpers import ANSI_RE, sanitize_error

logger = logging.getLogger(__name__)

download_tasks: dict = {}
_cancel_events: dict[str, threading.Event] = {}
_tasks_lock = threading.Lock()
_job_queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_DEPTH)
_slots = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)
_downloads_enabled = True


def set_downloads_enabled(enabled: bool) -> None:
    global _downloads_enabled
    _downloads_enabled = enabled


def downloads_enabled() -> bool:
    return _downloads_enabled


def _is_cancelled(task_id: str) -> bool:
    ev = _cancel_events.get(task_id)
    return ev is not None and ev.is_set()


def _apply_metadata(task_id: str, info: dict) -> None:
    site = info.get('extractor_key') or info.get('extractor') or ''
    with _tasks_lock:
        if task_id not in download_tasks:
            return
        download_tasks[task_id].update({
            'title': info.get('title', ''),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration_string', ''),
            'site': site,
        })


def _fail_task(task_id: str, message: str) -> None:
    with _tasks_lock:
        if task_id not in download_tasks:
            return
        download_tasks[task_id].update({
            'status': 'error',
            'phase': 'failed',
            'progress': 0,
            'message': message,
            'completed_at': time.time(),
        })


def _cancel_task(task_id: str) -> None:
    with _tasks_lock:
        if task_id not in download_tasks:
            return
        download_tasks[task_id].update({
            'status': 'cancelled',
            'phase': 'cancelled',
            'progress': 0,
            'message': 'Download cancelled.',
            'completed_at': time.time(),
        })


def _update_queue_positions() -> None:
    with _tasks_lock:
        queued = [
            (tid, t.get('created_at', 0))
            for tid, t in download_tasks.items()
            if t.get('status') == 'queued'
        ]
        queued.sort(key=lambda x: x[1])
        for pos, (tid, _) in enumerate(queued, start=1):
            download_tasks[tid]['queue_position'] = pos
            download_tasks[tid]['message'] = f'Queued (position {pos})…'


def progress_hook(task_id: str, info: dict) -> None:
    if _is_cancelled(task_id):
        raise yt_dlp.utils.DownloadError('Download cancelled by user')

    with _tasks_lock:
        if task_id not in download_tasks:
            return

        if info.get('status') == 'downloading':
            raw = ANSI_RE.sub('', info.get('_percent_str', '0%')).strip()
            try:
                percent = min(float(raw.replace('%', '')), 99.9)
            except (ValueError, TypeError):
                percent = 0.0
            speed = ANSI_RE.sub('', info.get('_speed_str', '') or '').strip()
            eta = ANSI_RE.sub('', info.get('_eta_str', '') or '').strip()
            downloaded = ANSI_RE.sub('', info.get('_downloaded_bytes_str', '') or '').strip()
            total = ANSI_RE.sub('', info.get('_total_bytes_str', '') or '').strip()
            msg = f'Downloading: {raw}'
            if speed:
                msg += f'  •  {speed}'
            if eta:
                msg += f'  •  ETA {eta}'

            download_tasks[task_id].update({
                'status': 'processing',
                'phase': 'downloading',
                'progress': percent,
                'message': msg,
                'speed': speed,
                'eta': eta,
                'downloaded': downloaded,
                'total_size': total,
            })

        elif info.get('status') == 'finished':
            download_tasks[task_id].update({
                'status': 'processing',
                'phase': 'merging',
                'progress': 100,
                'message': 'Merging audio & video streams…',
                'speed': '',
                'eta': '',
            })


def _cleanup_partial_files(task_id: str) -> None:
    pattern = str(DOWNLOADS_DIR / f'*-{task_id[:8]}.*')
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            logger.debug('Could not remove partial file %s', path)


def download_worker(task_id: str, url: str, fmt: str, threads: int = 4) -> None:
    """Perform a single download job."""
    short_id = task_id[:8]
    outtmpl = str(DOWNLOADS_DIR / f'%(title).60s-{short_id}.%(ext)s')

    try:
        if _is_cancelled(task_id):
            _cancel_task(task_id)
            return

        with _tasks_lock:
            download_tasks[task_id].update({
                'status': 'processing',
                'phase': 'preparing',
                'message': 'Fetching video info…',
                'queue_position': 0,
            })

        ydl_opts = {
            'format': ALLOWED_FORMATS.get(fmt, 'bestvideo+bestaudio/best'),
            'outtmpl': outtmpl,
            'progress_hooks': [lambda d, tid=task_id: progress_hook(tid, d)],
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'updatetime': False,
            'restrictfilenames': True,
            'concurrent_fragment_downloads': threads,
            'merge_output_format': 'mp4' if fmt != 'audio' else None,
            'postprocessor_args': {
                'ffmpeg': [
                    '-async', '1',
                    '-metadata:s:v:0', 'handler_name=VideoHandler',
                    '-metadata:s:a:0', 'handler_name=SoundHandler',
                ],
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
        ydl_opts = {k: v for k, v in ydl_opts.items() if v is not None}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if _is_cancelled(task_id):
                _cancel_task(task_id)
                _cleanup_partial_files(task_id)
                return

            info = ydl.extract_info(url, download=False)
            _apply_metadata(task_id, info)

            with _tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id].update({
                        'phase': 'starting',
                        'message': 'Starting download…',
                    })

            if _is_cancelled(task_id):
                _cancel_task(task_id)
                _cleanup_partial_files(task_id)
                return

            if hasattr(ydl, 'process_info'):
                ydl.process_info(info)
            else:
                ydl.extract_info(url, download=True)

            filename = ydl.prepare_filename(info)
            if fmt != 'audio' and not filename.endswith('.mp4'):
                possible = os.path.splitext(filename)[0] + '.mp4'
                if os.path.exists(possible):
                    filename = possible

            basename = os.path.basename(filename)
            token = secrets.token_urlsafe(32)
            token_expires = time.time() + (MAX_FILE_AGE_HOURS * 3600)
            site = info.get('extractor_key') or info.get('extractor') or ''

            with _tasks_lock:
                download_tasks[task_id].update({
                    'status': 'success',
                    'phase': 'complete',
                    'progress': 100,
                    'message': 'Download complete!',
                    'filename': basename,
                    'title': info.get('title', basename),
                    'duration': info.get('duration_string', ''),
                    'thumbnail': info.get('thumbnail', ''),
                    'site': site,
                    'download_token': token,
                    'token_expires_at': token_expires,
                    'download_url': f'/file/{task_id}?token={token}',
                    'completed_at': time.time(),
                })
            logger.info('Download complete: %s', basename)

    except yt_dlp.utils.DownloadError as e:
        if _is_cancelled(task_id) or 'cancelled' in str(e).lower():
            _cancel_task(task_id)
            _cleanup_partial_files(task_id)
            return
        logger.error('yt-dlp DownloadError: %s', e)
        _fail_task(task_id, sanitize_error(str(e)))
        _cleanup_partial_files(task_id)
    except yt_dlp.utils.ExtractorError as e:
        logger.error('yt-dlp ExtractorError: %s', e)
        _fail_task(task_id, sanitize_error(str(e)))
        _cleanup_partial_files(task_id)
    except OSError as e:
        logger.error('OS error during download: %s', e)
        _fail_task(task_id, 'Server storage error. Please try again later.')
        _cleanup_partial_files(task_id)
    except Exception as e:
        logger.exception('Unexpected error during download')
        _fail_task(task_id, sanitize_error(str(e)))
        _cleanup_partial_files(task_id)
    finally:
        _cancel_events.pop(task_id, None)


def _run_job(task_id: str, url: str, fmt: str, threads: int) -> None:
    try:
        download_worker(task_id, url, fmt, threads)
    finally:
        _slots.release()
        _try_dispatch_queued()


def _try_dispatch_queued() -> None:
    while True:
        try:
            job = _job_queue.get_nowait()
        except queue.Empty:
            break
        task_id, url, fmt, threads = job
        if _is_cancelled(task_id):
            _job_queue.task_done()
            continue
        with _tasks_lock:
            task = download_tasks.get(task_id)
            if not task or task.get('status') == 'cancelled':
                _job_queue.task_done()
                continue
        if not _slots.acquire(blocking=False):
            _job_queue.put(job)
            break
        _job_queue.task_done()
        threading.Thread(
            target=_run_job,
            args=(task_id, url, fmt, threads),
            daemon=True,
        ).start()
        return


def _queue_dispatcher() -> None:
    while True:
        job = _job_queue.get()
        if job is None:
            _job_queue.task_done()
            break
        task_id, url, fmt, threads = job
        if _is_cancelled(task_id):
            _job_queue.task_done()
            continue
        with _tasks_lock:
            task = download_tasks.get(task_id)
            if not task or task.get('status') == 'cancelled':
                _job_queue.task_done()
                continue
        _slots.acquire()
        _job_queue.task_done()
        threading.Thread(
            target=_run_job,
            args=(task_id, url, fmt, threads),
            daemon=True,
        ).start()


def enqueue_download(url: str, fmt: str, threads: int) -> dict:
    """Create a download task and enqueue it."""
    if not _downloads_enabled:
        return {'status': 'error', 'message': 'Downloads are disabled. FFmpeg or disk check failed at startup.'}

    task_id = str(uuid.uuid4())
    _cancel_events[task_id] = threading.Event()

    with _tasks_lock:
        if _job_queue.qsize() >= MAX_QUEUE_DEPTH:
            return {'status': 'error', 'message': f'Queue is full. Maximum {MAX_QUEUE_DEPTH} pending jobs.'}
        download_tasks[task_id] = {
            'status': 'queued',
            'phase': 'queued',
            'progress': 0,
            'message': 'Queued…',
            'created_at': time.time(),
            'url': url,
            'format': fmt,
            'speed': '',
            'eta': '',
            'downloaded': '',
            'total_size': '',
            'queue_position': 0,
        }

    try:
        _job_queue.put((task_id, url, fmt, threads), block=False)
    except queue.Full:
        with _tasks_lock:
            download_tasks.pop(task_id, None)
        _cancel_events.pop(task_id, None)
        return {'status': 'error', 'message': f'Queue is full. Maximum {MAX_QUEUE_DEPTH} pending jobs.'}

    _update_queue_positions()
    return {'status': 'success', 'task_id': task_id, 'url': url}


def cancel_task(task_id: str) -> dict:
    with _tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return {'status': 'error', 'message': 'Task not found.'}
        if task.get('status') in ('success', 'error', 'cancelled'):
            return {'status': 'error', 'message': 'Task already finished.'}

    ev = _cancel_events.setdefault(task_id, threading.Event())
    ev.set()

    with _tasks_lock:
        task = download_tasks.get(task_id)
        if task and task.get('status') == 'queued':
            _cancel_task(task_id)
            _update_queue_positions()
            return {'status': 'success', 'message': 'Download cancelled.'}

    return {'status': 'success', 'message': 'Cancellation requested.'}


def get_task(task_id: str) -> Optional[dict]:
    with _tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return None
        safe = {k: v for k, v in task.items() if k not in ('download_token',)}
        return safe


def verify_file_access(task_id: str, token: str) -> Optional[str]:
    """Return filename if token is valid, else None."""
    with _tasks_lock:
        task = download_tasks.get(task_id)
        if not task or task.get('status') != 'success':
            return None
        if task.get('download_token') != token:
            return None
        if time.time() > task.get('token_expires_at', 0):
            return None
        return task.get('filename')


def list_active_tasks() -> list:
    with _tasks_lock:
        result = []
        for tid, task in download_tasks.items():
            if task.get('status') in ('queued', 'processing'):
                result.append({
                    'task_id': tid,
                    'url': task.get('url', ''),
                    'format': task.get('format', ''),
                    'status': task.get('status'),
                    'phase': task.get('phase'),
                    'progress': task.get('progress', 0),
                    'queue_position': task.get('queue_position', 0),
                    'title': task.get('title', ''),
                    'thumbnail': task.get('thumbnail', ''),
                })
        return result


def cleanup_old_files() -> None:
    while True:
        try:
            cutoff = time.time() - (MAX_FILE_AGE_HOURS * 3600)
            for fname in os.listdir(DOWNLOADS_DIR):
                fpath = DOWNLOADS_DIR / fname
                if fpath.is_file() and fpath.stat().st_mtime < cutoff:
                    fpath.unlink()
                    logger.info('Cleanup: deleted %s', fname)
        except Exception:
            logger.exception('Error during file cleanup')
        time.sleep(CLEANUP_INTERVAL_SECS)


def prune_old_tasks() -> None:
    while True:
        try:
            cutoff = time.time() - TASK_RETENTION_SECS
            with _tasks_lock:
                to_remove = [
                    tid for tid, task in download_tasks.items()
                    if task.get('status') in ('success', 'error', 'cancelled')
                    and task.get('completed_at', 0) < cutoff
                ]
                for tid in to_remove:
                    download_tasks.pop(tid, None)
                    _cancel_events.pop(tid, None)
        except Exception:
            logger.exception('Error during task pruning')
        time.sleep(60)


def start_background_threads() -> None:
    threading.Thread(target=_queue_dispatcher, daemon=True, name='queue-dispatcher').start()
    threading.Thread(target=cleanup_old_files, daemon=True, name='file-cleanup').start()
    threading.Thread(target=prune_old_tasks, daemon=True, name='task-prune').start()
