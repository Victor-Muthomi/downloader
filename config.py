"""Environment-driven configuration for NexDown."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = Path(os.environ.get('DOWNLOADS_DIR', BASE_DIR / 'downloads'))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-change-me-in-production')

MAX_FILE_AGE_HOURS = int(os.environ.get('MAX_FILE_AGE_HOURS', '2'))
CLEANUP_INTERVAL_SECS = int(os.environ.get('CLEANUP_INTERVAL_SECS', '600'))
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', '5'))
MAX_BATCH_SIZE = int(os.environ.get('MAX_BATCH_SIZE', '10'))
MAX_QUEUE_DEPTH = int(os.environ.get('MAX_QUEUE_DEPTH', '50'))
TASK_RETENTION_SECS = int(os.environ.get('TASK_RETENTION_SECS', '1800'))
MIN_DISK_FREE_MB = int(os.environ.get('MIN_DISK_FREE_MB', '500'))

HOST = os.environ.get('HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', '5000'))
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')

ALLOWED_FORMATS = {
    'best': 'bestvideo+bestaudio/best',
    '720p': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
    '480p': 'bestvideo[height<=480]+bestaudio/best[height<=480]/best',
    'audio': 'bestaudio[ext=m4a]/bestaudio/best',
}

RATE_LIMIT_STORAGE = os.environ.get('RATE_LIMIT_STORAGE', 'memory://')
