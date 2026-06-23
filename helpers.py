"""Shared helpers: URL validation, errors, system checks."""

import ipaddress
import re
import shutil
import socket
import subprocess
from urllib.parse import urlparse

import yt_dlp

from config import DOWNLOADS_DIR, MIN_DISK_FREE_MB

ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

_BLOCKED_HOSTS = frozenset({
    'localhost', '127.0.0.1', '0.0.0.0', '::1', 'metadata', 'metadata.google.internal',
})

_PRIVATE_NETWORKS = (
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
)


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


def validate_url(url: str) -> bool:
    """HTTP/HTTPS URL validation with SSRF mitigation."""
    url = url.strip()
    if not url or len(url) > 2048:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False

    host = parsed.hostname
    if not host:
        return False
    host_lower = host.lower().rstrip('.')
    if host_lower in _BLOCKED_HOSTS:
        return False

    if _is_private_ip(host_lower):
        return False

    # Resolve hostname and reject private IPs
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM):
            addr = sockaddr[0]
            if _is_private_ip(addr):
                return False
    except socket.gaierror:
        pass

    return True


def sanitize_error(error_msg: str) -> str:
    """Return a user-friendly error message instead of raw yt-dlp tracebacks."""
    msg = str(error_msg)
    if 'is not a valid URL' in msg or 'Unsupported URL' in msg:
        return 'This URL is not supported. Try a direct link to a video or audio page.'
    if 'Video unavailable' in msg or 'Private video' in msg or 'content is not available' in msg.lower():
        return 'This content is unavailable. It may be private, deleted, or region-locked.'
    if 'Sign in to confirm' in msg or ('login' in msg.lower() and 'required' in msg.lower()):
        return 'This content requires sign-in and cannot be downloaded.'
    if 'HTTP Error 403' in msg:
        return 'Access denied by the host site. Please try again later.'
    if 'HTTP Error 429' in msg:
        return 'The host site is rate-limiting requests. Please wait a few minutes and try again.'
    if 'Network' in msg or 'connection' in msg.lower() or 'timed out' in msg.lower():
        return 'A network error occurred. Please check your connection and try again.'
    if 'ffmpeg' in msg.lower() or 'postprocessor' in msg.lower():
        return 'Post-processing failed. FFmpeg may not be installed on the server.'
    return re.sub(r'/[^\s]+/', '.../', msg)[:300]


def probe_url(url: str) -> dict:
    """Check whether yt-dlp can handle a URL and return metadata."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
        'socket_timeout': 30,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        site = info.get('extractor_key') or info.get('extractor') or 'unknown'
        return {
            'supported': True,
            'site': site,
            'title': info.get('title', ''),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration_string', ''),
        }


def check_ffmpeg() -> tuple[bool, str]:
    """Return (ok, version_or_error)."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return False, 'ffmpeg not found on PATH'
    try:
        result = subprocess.run(
            [ffmpeg, '-version'],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        first_line = (result.stdout or result.stderr or '').splitlines()[0]
        return True, first_line or 'ffmpeg available'
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


def check_disk_space() -> tuple[bool, dict]:
    """Return (ok, details) for downloads directory."""
    usage = shutil.disk_usage(DOWNLOADS_DIR)
    free_mb = usage.free // (1024 * 1024)
    ok = free_mb >= MIN_DISK_FREE_MB
    return ok, {
        'free_mb': free_mb,
        'total_mb': usage.total // (1024 * 1024),
        'min_required_mb': MIN_DISK_FREE_MB,
    }


def check_downloads_writable() -> bool:
    test_file = DOWNLOADS_DIR / '.write_test'
    try:
        test_file.write_text('ok')
        test_file.unlink()
        return True
    except OSError:
        return False


def get_health_status() -> dict:
    ffmpeg_ok, ffmpeg_info = check_ffmpeg()
    disk_ok, disk_info = check_disk_space()
    writable = check_downloads_writable()
    healthy = ffmpeg_ok and disk_ok and writable
    return {
        'status': 'ok' if healthy else 'degraded',
        'ffmpeg': {'ok': ffmpeg_ok, 'info': ffmpeg_info},
        'disk': {'ok': disk_ok, **disk_info},
        'downloads_writable': writable,
        'yt_dlp_version': yt_dlp.version.__version__,
    }
