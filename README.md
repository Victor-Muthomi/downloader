# NexDown

**NexDown** is a self-hosted, multi-platform video downloader with a clean web UI. Paste a link from YouTube, TikTok, Vimeo, Instagram, Twitter/X, or [1,000+ other sites supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), choose a quality preset, and save the file to your device.

Built with **Flask**, **yt-dlp**, and a lightweight frontend — no database, no accounts, no tracking.

---

## Features

| Feature | Description |
|---------|-------------|
| **Multi-platform** | Any URL yt-dlp supports — not limited to YouTube |
| **Batch downloads** | Paste multiple URLs (one per line) and download up to 10 at once |
| **Live queue** | Track every job independently with real-time progress |
| **Quality presets** | Best, 720p, 480p, or audio-only (M4A) |
| **Parallel fragments** | Configurable download threads (1–16) for faster transfers |
| **Auto cleanup** | Downloaded files are deleted from the server after 2 hours |
| **Rate limiting** | Built-in protection against abuse via Flask-Limiter |
| **Download queue** | Jobs wait in queue when concurrency limit is reached — batch downloads no longer fail mid-batch |
| **Tokenized downloads** | Files served via signed task tokens, not guessable filenames |
| **Health checks** | `GET /health` reports FFmpeg, disk space, and yt-dlp status |
| **Cancel support** | Cancel queued or in-progress downloads via API or UI |
| **Session recovery** | Page refresh re-attaches to active server-side tasks |

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| **Python** | 3.10+ | 3.11 or 3.12 recommended |
| **FFmpeg** | Any recent | Required for merging video/audio and audio extraction |
| **pip** | Latest | For installing Python packages |

### Install FFmpeg

**Ubuntu / Debian**
```bash
sudo apt update && sudo apt install -y ffmpeg
```

**macOS (Homebrew)**
```bash
brew install ffmpeg
```

**Windows**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add `ffmpeg` to your `PATH`.

Verify installation:
```bash
ffmpeg -version
```

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/your-username/downloader.git
cd downloader
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Linux / macOS
# venv\Scripts\activate         # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment (optional)

```bash
cp .env.example .env
# Edit .env — set SECRET_KEY in production
```

### 5. Run the server

```bash
python app.py
```

Or with Gunicorn:

```bash
gunicorn -w 2 -b 0.0.0.0:5000 app:app --timeout 300
```

Open **http://localhost:5000** in your browser.

### Docker

```bash
docker compose up --build
```

The container includes FFmpeg, a health check on `/health`, and a persistent `downloads` volume.

---

## Usage

### Web UI

1. Paste one or more video URLs into the input box (one URL per line for batch downloads).
2. Select a **quality** preset and optional **thread** count.
3. Click **Download**.
4. Watch progress in the queue panel. When a job finishes, click **Save to device**.

You can start new downloads while others are still running. Up to **5 concurrent downloads** are allowed at a time.

### Supported URL examples

```
https://www.youtube.com/watch?v=dQw4w9WgXcQ
https://youtu.be/dQw4w9WgXcQ
https://www.tiktok.com/@user/video/1234567890
https://vimeo.com/123456789
https://twitter.com/user/status/1234567890
```

> Site support depends on your installed yt-dlp version. Update regularly to keep extractors working.

---

## API Reference

All endpoints accept and return JSON unless noted.

### `POST /probe`

Check whether a URL is supported and fetch metadata **without** downloading.

**Request**
```json
{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }
```

**Response (200)**
```json
{
  "status": "success",
  "supported": true,
  "site": "Youtube",
  "title": "Rick Astley - Never Gonna Give You Up",
  "thumbnail": "https://…",
  "duration": "3:33"
}
```

---

### `POST /download`

Start a single background download.

**Request**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "format": "720p",
  "threads": 4
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | — | Full `http://` or `https://` URL |
| `format` | string | `"best"` | One of: `best`, `720p`, `480p`, `audio` |
| `threads` | int | `4` | Concurrent fragment downloads (1–16) |

**Response (200)**
```json
{
  "status": "success",
  "task_id": "a1b2c3d4-…",
  "url": "https://…",
  "message": "Download started"
}
```

---

### `POST /download/batch`

Start multiple downloads in one request.

**Request**
```json
{
  "urls": [
    "https://www.youtube.com/watch?v=…",
    "https://vimeo.com/123456789"
  ],
  "format": "best",
  "threads": 4
}
```

Maximum **10 URLs** per batch. Duplicate URLs are deduplicated.

**Response (200)**
```json
{
  "status": "success",
  "message": "Started 2 download(s)",
  "tasks": [
    { "task_id": "…", "url": "https://…" }
  ],
  "errors": []
}
```

---

### `GET /status/<task_id>`

Poll download progress.

**Response (200)**
```json
{
  "status": "processing",
  "progress": 42.5,
  "message": "Downloading: 42.5%  •  2.1MiB/s  •  ETA 0:15",
  "url": "https://…"
}
```

When complete:
```json
{
  "status": "success",
  "progress": 100,
  "message": "Download complete!",
  "filename": "Video_Title.mp4",
  "title": "Video Title",
  "duration": "3:33",
  "thumbnail": "https://…",
  "site": "Youtube"
}
```

Possible `status` values: `queued`, `processing`, `success`, `error`, `cancelled`.

Additional fields while downloading: `phase`, `speed`, `eta`, `downloaded`, `total_size`, `queue_position`.

When complete, responses include a tokenized download URL:

```json
{
  "status": "success",
  "download_url": "/file/<task_id>?token=…",
  "filename": "Video_Title-abc12345.mp4"
}
```

---

### `GET /tasks/active`

List all queued and in-progress tasks (for page-refresh recovery).

---

### `POST /cancel/<task_id>`

Cancel a queued or in-progress download.

---

### `GET /health`

Health check for monitoring and Docker.

```json
{
  "status": "ok",
  "ffmpeg": {"ok": true, "info": "ffmpeg version …"},
  "disk": {"ok": true, "free_mb": 12000, "min_required_mb": 500},
  "downloads_writable": true,
  "yt_dlp_version": "2024.12.06",
  "downloads_enabled": true
}
```

Returns HTTP 503 when degraded (missing FFmpeg, low disk, etc.).

---

### `GET /file/<task_id>?token=…`

Download a finished file using the token from `download_url` in the status response.

```
GET /file/a1b2c3d4-…?token=secure-random-token
```

Returns the file as an attachment. Tokens expire when the file is auto-deleted (default: 2 hours).

---

## Configuration

Copy `.env.example` to `.env` or set environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | (dev default) | Flask secret — **change in production** |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `5000` | Bind port |
| `FLASK_DEBUG` | `0` | Enable Flask debug mode |
| `DOWNLOADS_DIR` | `./downloads` | Temporary file storage |
| `MAX_FILE_AGE_HOURS` | `2` | Hours before downloaded files are deleted |
| `CLEANUP_INTERVAL_SECS` | `600` | How often the file cleanup job runs |
| `MAX_CONCURRENT_DOWNLOADS` | `5` | Max simultaneous active downloads |
| `MAX_BATCH_SIZE` | `10` | Max URLs per batch request |
| `MAX_QUEUE_DEPTH` | `50` | Max pending jobs in the wait queue |
| `TASK_RETENTION_SECS` | `1800` | Seconds to keep completed tasks in memory |
| `MIN_DISK_FREE_MB` | `500` | Minimum free disk before disabling downloads |
| `RATE_LIMIT_STORAGE` | `memory://` | Flask-Limiter backend (`redis://…` for multi-worker) |

Rate limits (Flask-Limiter):

| Endpoint | Limit |
|----------|-------|
| Global default | 200 requests / hour |
| `POST /download` | 15 / minute |
| `POST /download/batch` | 10 / minute |
| `POST /probe` | 30 / minute |
| `GET /file/…` | 30 / minute |
| `GET /health` | exempt |
| `GET /tasks/active` | exempt |
| `POST /cancel/…` | 30 / minute |

---

## Production Deployment

The built-in Flask server is for **development only**. Use a production WSGI server behind a reverse proxy.

### Gunicorn (recommended)

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app --timeout 300
```

> Downloads can take several minutes. Set `--timeout` high enough to avoid worker kills during long merges.

### systemd service example

```ini
[Unit]
Description=NexDown video downloader
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/downloader
Environment="PATH=/opt/downloader/venv/bin"
ExecStart=/opt/downloader/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app --timeout 300
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Nginx reverse proxy snippet

```nginx
server {
    listen 80;
    server_name down.example.com;

    client_max_body_size 10M;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

### Keep yt-dlp up to date

Site extractors break frequently. Schedule regular updates:

```bash
pip install -U yt-dlp
```

Or add a weekly cron job:
```cron
0 3 * * 0 /opt/downloader/venv/bin/pip install -U yt-dlp
```

---

## Project Structure

```
downloader/
├── app.py              # Flask routes and error handlers
├── config.py           # Environment-driven settings
├── helpers.py          # URL validation, health checks, probe
├── download_manager.py # Queue, workers, task lifecycle
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── tests/
│   ├── test_helpers.py
│   ├── test_routes.py
│   └── test_download_manager.py
├── templates/
│   └── index.html
├── static/
│   ├── style.css
│   └── script.js
└── downloads/          # Temporary storage (auto-created, gitignored)
```

### Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## Troubleshooting

### "Post-processing failed. FFmpeg may not be installed"

FFmpeg is missing or not on `PATH`. Install it (see [Requirements](#requirements)) and restart the server.

### "This URL is not supported"

- Confirm the link is a direct page URL (not a search result or redirect).
- Update yt-dlp: `pip install -U yt-dlp`
- Test with `POST /probe` to see the exact error.

### "Too many active downloads"

Jobs are queued automatically. If the queue is full, raise `MAX_QUEUE_DEPTH` in `.env`. Active slot limit is controlled by `MAX_CONCURRENT_DOWNLOADS`.

### Downloads are slow

- Increase **Threads** (up to 16) in the UI.
- Some hosts rate-limit server IPs; a residential proxy is outside this project's scope.

### "Access denied by the host site" (HTTP 403)

The source site blocked the request. Retry later or update yt-dlp. Some content requires cookies or login, which this app does not support out of the box.

---

## Security Notes

- NexDown is designed for **personal or trusted-network use**. Do not expose it to the public internet without authentication and additional hardening.
- Downloaded files require a signed token (`download_url`) — filenames alone are not sufficient to download.
- Private/local URLs (localhost, RFC1918) are blocked to reduce SSRF risk.
- Rate limiting is per IP; use `RATE_LIMIT_STORAGE=redis://…` for multi-worker Gunicorn.
- Task state is in-memory only and is pruned after `TASK_RETENTION_SECS` (default 30 min).

---

## Legal Disclaimer

You are responsible for ensuring you have the right to download and store any content you retrieve. Many platforms prohibit downloading in their Terms of Service. NexDown is a tool; how you use it is your responsibility.

---

## License

MIT — see [LICENSE](LICENSE) if present, or adapt as needed for your deployment.
