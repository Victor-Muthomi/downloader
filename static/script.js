/**
 * NexDown — Client-side logic
 * Active download cards, rich progress, and local history.
 */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('downloadForm');
    const urlInput = document.getElementById('urlInput');
    const pasteBtn = document.getElementById('pasteBtn');
    const formatSelect = document.getElementById('formatSelect');
    const threadInput = document.getElementById('threadInput');
    const downloadBtn = document.getElementById('downloadBtn');
    const btnIcon = downloadBtn.querySelector('.btn-icon');
    const btnText = downloadBtn.querySelector('span');
    const spinner = downloadBtn.querySelector('.spinner');
    const activePanel = document.getElementById('activePanel');
    const activeDownloads = document.getElementById('activeDownloads');
    const activeCount = document.getElementById('activeCount');
    const historyPanel = document.getElementById('historyPanel');
    const downloadHistory = document.getElementById('downloadHistory');
    const historyCount = document.getElementById('historyCount');
    const historyEmpty = document.getElementById('historyEmpty');
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');
    const toastContainer = document.getElementById('toastContainer');

    const HISTORY_KEY = 'nexdown_history';
    const SESSION_KEY = 'nexdown_active';
    const MAX_HISTORY = 50;
    const FILE_TTL_MS = 2 * 60 * 60 * 1000;

    const activeJobs = new Map();
    let pollTimer = null;

    const PHASE_LABELS = {
        queued: 'Queued',
        preparing: 'Preparing',
        starting: 'Starting',
        downloading: 'Downloading',
        merging: 'Merging',
        complete: 'Complete',
        failed: 'Failed',
        cancelled: 'Cancelled',
    };

    const THUMB_PLACEHOLDER = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 9.5L15 12L10 14.5V9.5Z"/></svg>`;

    // ── Utilities ─────────────────────────────────────────────────────

    function isValidUrl(url) {
        try {
            const u = new URL(url.trim());
            return u.protocol === 'http:' || u.protocol === 'https:';
        } catch {
            return false;
        }
    }

    function parseUrls(text) {
        return [...new Set(
            text.split(/\r?\n/)
                .map((line) => line.trim())
                .filter((line) => line.length > 0)
        )];
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str ?? '';
        return div.innerHTML;
    }

    function truncate(str, max = 48) {
        if (!str || str.length <= max) return str || '';
        return str.slice(0, max - 1) + '…';
    }

    function formatRelativeTime(ts) {
        const diff = Date.now() - ts;
        const sec = Math.floor(diff / 1000);
        if (sec < 60) return 'Just now';
        const min = Math.floor(sec / 60);
        if (min < 60) return `${min}m ago`;
        const hr = Math.floor(min / 60);
        if (hr < 24) return `${hr}h ago`;
        return new Date(ts).toLocaleDateString();
    }

    function isFileExpired(completedAt) {
        return Date.now() - completedAt > FILE_TTL_MS;
    }

    function markInputError(show) {
        urlInput.classList.toggle('input-error', show);
    }

    // ── History (localStorage) ────────────────────────────────────────

    function loadHistory() {
        try {
            return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
        } catch {
            return [];
        }
    }

    function saveHistory(items) {
        localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_HISTORY)));
    }

    function addToHistory(entry) {
        const items = loadHistory();
        items.unshift({
            id: entry.taskId || crypto.randomUUID(),
            taskId: entry.taskId,
            url: entry.url,
            title: entry.title || truncate(entry.url, 40),
            filename: entry.filename || '',
            thumbnail: entry.thumbnail || '',
            site: entry.site || '',
            duration: entry.duration || '',
            format: entry.format || '',
            status: entry.status,
            message: entry.message || '',
            downloadUrl: entry.downloadUrl || '',
            completedAt: entry.completedAt || Date.now(),
        });
        saveHistory(items);
        renderHistory();
    }

    function renderHistory() {
        const items = loadHistory();
        historyCount.textContent = items.length;
        downloadHistory.innerHTML = '';

        if (items.length === 0) {
            historyEmpty.classList.remove('hidden');
            return;
        }

        historyEmpty.classList.add('hidden');

        for (const item of items) {
            const expired = item.status === 'success' && isFileExpired(item.completedAt);
            const card = document.createElement('article');
            card.className = `download-card download-card--history download-card--${item.status}${expired ? ' download-card--expired' : ''}`;
            card.dataset.historyId = item.id;

            const thumb = item.thumbnail
                ? `<img class="card-thumb" src="${escapeHtml(item.thumbnail)}" alt="" loading="lazy">`
                : `<div class="card-thumb card-thumb--placeholder">${THUMB_PLACEHOLDER}</div>`;

            const badgeClass = item.status === 'success' ? 'complete' : item.status === 'cancelled' ? 'failed' : 'failed';
            const badgeLabel = item.status === 'success' ? 'Complete' : item.status === 'cancelled' ? 'Cancelled' : 'Failed';

            let actions = '';
            if (item.status === 'success' && item.downloadUrl) {
                if (expired) {
                    actions = `<span class="expired-label">File expired (auto-deleted after 2h)</span>`;
                } else {
                    actions = `<a class="save-btn save-btn--sm" href="${escapeHtml(item.downloadUrl)}" download>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        Save
                    </a>`;
                }
            }

            const metaParts = [];
            if (item.site) metaParts.push(`<span class="card-meta-item">${escapeHtml(item.site)}</span>`);
            if (item.duration) metaParts.push(`<span class="card-meta-item">${escapeHtml(item.duration)}</span>`);
            if (item.format) metaParts.push(`<span class="card-meta-item">${escapeHtml(item.format)}</span>`);

            card.innerHTML = `
                ${thumb}
                <div class="card-body">
                    <div class="card-top">
                        <span class="card-title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</span>
                        <span class="card-badge card-badge--${badgeClass}">${badgeLabel}</span>
                    </div>
                    ${metaParts.length ? `<div class="card-meta">${metaParts.join('')}</div>` : ''}
                    ${item.status === 'error' || item.status === 'cancelled' ? `<p class="card-error-msg">${escapeHtml(item.message)}</p>` : ''}
                    <div class="card-actions">
                        ${actions}
                        <span class="card-time">${formatRelativeTime(item.completedAt)}</span>
                    </div>
                </div>`;

            downloadHistory.appendChild(card);
        }
    }

    clearHistoryBtn.addEventListener('click', () => {
        saveHistory([]);
        renderHistory();
        toast('History cleared', 'success');
    });

    // ── Toasts ────────────────────────────────────────────────────────

    function toast(message, type = 'success', durationMs = 4000) {
        const el = document.createElement('div');
        el.className = `toast toast--${type}`;
        const icons = {
            success: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
            error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
            warn: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        };
        el.innerHTML = `${icons[type] || ''}  <span>${message}</span>`;
        toastContainer.appendChild(el);
        setTimeout(() => {
            el.classList.add('leaving');
            el.addEventListener('animationend', () => el.remove());
        }, durationMs);
    }

    // ── Active download cards ─────────────────────────────────────────

    function updateActiveCount() {
        const n = activeJobs.size;
        activeCount.textContent = n;
        activePanel.style.display = n > 0 ? 'block' : 'none';
    }

    function renderThumb(thumbnail, title) {
        if (thumbnail) {
            return `<img class="card-thumb" src="${escapeHtml(thumbnail)}" alt="${escapeHtml(title || '')}" loading="lazy">`;
        }
        return `<div class="card-thumb card-thumb--placeholder">${THUMB_PLACEHOLDER}</div>`;
    }

    function createActiveCard(taskId, url, format) {
        const el = document.createElement('article');
        el.className = 'download-card download-card--processing';
        el.dataset.taskId = taskId;
        el.dataset.format = format;
        el.innerHTML = `
            ${renderThumb('', '')}
            <div class="card-body">
                <div class="card-top">
                    <span class="card-title card-title--muted" title="${escapeHtml(url)}">${escapeHtml(truncate(url, 55))}</span>
                    <span class="card-badge card-badge--queued">Queued</span>
                </div>
                <div class="card-meta">
                    <span class="card-meta-item">${escapeHtml(format)}</span>
                </div>
                <div class="card-progress-row">
                    <div class="progress-track">
                        <div class="progress-bar active" style="width: 2%"></div>
                    </div>
                    <span class="card-pct">0%</span>
                </div>
                <div class="card-stats">
                    <span class="card-stat"><span class="card-stat-label">Speed</span> <span class="card-stat-value card-speed">—</span></span>
                    <span class="card-stat"><span class="card-stat-label">ETA</span> <span class="card-stat-value card-eta">—</span></span>
                    <span class="card-stat"><span class="card-stat-label">Size</span> <span class="card-stat-value card-size">—</span></span>
                </div>
                <div class="card-actions">
                    <button type="button" class="cancel-btn" data-task-id="${escapeHtml(taskId)}">Cancel</button>
                </div>
            </div>`;
        el.querySelector('.cancel-btn').addEventListener('click', () => cancelJob(taskId));
        activeDownloads.prepend(el);
        updateActiveCount();
        return el;
    }

    function updateActiveCard(el, data) {
        const phase = data.phase || (data.status === 'processing' ? 'downloading' : data.status);
        const pct = Math.round(data.progress || 0);
        const title = data.title || data.url || 'Download';
        const badge = el.querySelector('.card-badge');
        const titleEl = el.querySelector('.card-title');
        const bar = el.querySelector('.progress-bar');
        const pctEl = el.querySelector('.card-pct');
        const speedEl = el.querySelector('.card-speed');
        const etaEl = el.querySelector('.card-eta');
        const sizeEl = el.querySelector('.card-size');

        // Thumbnail
        const existingThumb = el.querySelector('.card-thumb');
        if (data.thumbnail && existingThumb?.tagName !== 'IMG') {
            const img = document.createElement('img');
            img.className = 'card-thumb';
            img.src = data.thumbnail;
            img.alt = title;
            img.loading = 'lazy';
            existingThumb.replaceWith(img);
        }

        titleEl.textContent = title;
        titleEl.title = title;
        titleEl.classList.remove('card-title--muted');

        const badgePhase = phase === 'complete' ? 'complete' : (phase === 'failed' || phase === 'cancelled') ? 'failed' : phase;
        badge.className = `card-badge card-badge--${badgePhase}`;
        badge.textContent = PHASE_LABELS[phase] || phase;

        if (data.site || el.dataset.format) {
            let meta = el.querySelector('.card-meta');
            if (!meta) {
                meta = document.createElement('div');
                meta.className = 'card-meta';
                el.querySelector('.card-body').insertBefore(meta, el.querySelector('.card-progress-row'));
            }
            const parts = [];
            if (data.site) parts.push(`<span class="card-meta-item">${escapeHtml(data.site)}</span>`);
            if (el.dataset.format) parts.push(`<span class="card-meta-item">${escapeHtml(el.dataset.format)}</span>`);
            if (data.duration) parts.push(`<span class="card-meta-item">${escapeHtml(data.duration)}</span>`);
            meta.innerHTML = parts.join('');
        }

        if (data.status === 'queued' || data.status === 'processing') {
            el.className = 'download-card download-card--processing';
            bar.classList.add('active');
            if (data.status === 'queued') {
                bar.style.width = '2%';
                pctEl.textContent = data.queue_position ? `#${data.queue_position}` : '…';
                speedEl.textContent = '—';
                etaEl.textContent = '—';
                sizeEl.textContent = '—';
            } else {
                bar.style.width = `${Math.max(pct, 2)}%`;
                pctEl.textContent = phase === 'merging' ? '100%' : `${pct}%`;
                speedEl.textContent = data.speed || '—';
                etaEl.textContent = data.eta || '—';
                if (data.downloaded && data.total_size) {
                    sizeEl.textContent = `${data.downloaded} / ${data.total_size}`;
                } else if (data.downloaded) {
                    sizeEl.textContent = data.downloaded;
                } else {
                    sizeEl.textContent = '—';
                }
            }
            return;
        }

        bar.classList.remove('active');
    }

    function finishActiveCard(el, data, job) {
        el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        el.style.opacity = '0';
        el.style.transform = 'translateX(12px)';
        setTimeout(() => el.remove(), 300);
        updateActiveCount();

        untrackActive(job.taskId);
        addToHistory({
            taskId: job.taskId,
            url: job.url,
            title: data.title || job.url,
            filename: data.filename,
            downloadUrl: data.download_url || '',
            thumbnail: data.thumbnail,
            site: data.site,
            duration: data.duration,
            format: job.format,
            status: data.status,
            message: data.message,
            completedAt: data.completed_at ? data.completed_at * 1000 : Date.now(),
        });
    }

    function loadSessionActive() {
        try {
            return JSON.parse(sessionStorage.getItem(SESSION_KEY) || '[]');
        } catch {
            return [];
        }
    }

    function saveSessionActive(items) {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify(items));
    }

    function trackActive(taskId, url, format) {
        const items = loadSessionActive().filter((i) => i.taskId !== taskId);
        items.push({ taskId, url, format });
        saveSessionActive(items);
    }

    function untrackActive(taskId) {
        saveSessionActive(loadSessionActive().filter((i) => i.taskId !== taskId));
    }

    async function cancelJob(taskId) {
        try {
            const res = await fetch(`/cancel/${taskId}`, { method: 'POST' });
            const data = await res.json();
            toast(data.message || 'Cancel requested', res.ok ? 'warn' : 'error');
        } catch {
            toast('Could not cancel download', 'error');
        }
    }

    async function recoverActiveJobs() {
        try {
            const res = await fetch('/tasks/active');
            if (!res.ok) return;
            const data = await res.json();
            const serverTasks = data.tasks || [];
            const sessionTasks = loadSessionActive();

            const merged = new Map();
            for (const t of [...sessionTasks, ...serverTasks]) {
                merged.set(t.task_id || t.taskId, {
                    taskId: t.task_id || t.taskId,
                    url: t.url,
                    format: t.format || 'best',
                });
            }

            for (const job of merged.values()) {
                if (!job.taskId || activeJobs.has(job.taskId)) continue;
                addJob(job.taskId, job.url, job.format);
            }
        } catch {
            /* recovery is best-effort */
        }
    }

    function addJob(taskId, url, format) {
        if (activeJobs.has(taskId)) return;
        const el = createActiveCard(taskId, url, format);
        activeJobs.set(taskId, { el, url, format, taskId, failures: 0 });
        trackActive(taskId, url, format);
        ensurePolling();
    }

    function removeJob(taskId) {
        activeJobs.delete(taskId);
        updateActiveCount();
        if (activeJobs.size === 0) stopPolling();
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function ensurePolling() {
        if (pollTimer) return;

        pollTimer = setInterval(async () => {
            const entries = [...activeJobs.entries()];
            if (entries.length === 0) {
                stopPolling();
                return;
            }

            await Promise.all(entries.map(async ([taskId, job]) => {
                try {
                    const res = await fetch(`/status/${taskId}`);
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    const data = await res.json();
                    job.failures = 0;
                    updateActiveCard(job.el, data);

                    if (data.status === 'success') {
                        finishActiveCard(job.el, data, job);
                        removeJob(taskId);
                        toast(`Ready: ${data.title || 'download'}`, 'success');
                    } else if (data.status === 'error' || data.status === 'cancelled') {
                        finishActiveCard(job.el, data, job);
                        removeJob(taskId);
                        toast(data.message || 'Download failed', data.status === 'cancelled' ? 'warn' : 'error');
                    }
                } catch (err) {
                    job.failures += 1;
                    console.error('Polling error', err);
                    if (job.failures >= 5) {
                        const errData = {
                            status: 'error',
                            phase: 'failed',
                            message: 'Lost connection to server.',
                        };
                        updateActiveCard(job.el, errData);
                        finishActiveCard(job.el, errData, job);
                        removeJob(taskId);
                        toast('Connection lost', 'error');
                    }
                }
            }));
        }, 1000);
    }

    // ── Form ──────────────────────────────────────────────────────────

    urlInput.addEventListener('input', () => markInputError(false));

    if (pasteBtn) {
        pasteBtn.addEventListener('click', async () => {
            try {
                const text = await navigator.clipboard.readText();
                urlInput.value = text;
                urlInput.focus();
                markInputError(false);
                toast('URL pasted from clipboard', 'success');
            } catch {
                toast('Clipboard access denied by browser', 'warn');
            }
        });
    }

    function setButtonLoading(loading) {
        downloadBtn.disabled = loading;
        btnIcon.style.display = loading ? 'none' : '';
        btnText.style.display = loading ? 'none' : '';
        spinner.style.display = loading ? 'block' : 'none';
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const raw = urlInput.value.trim();
        const format = formatSelect.value;
        const threads = parseInt(threadInput.value, 10) || 4;

        if (!raw) {
            markInputError(true);
            toast('Please enter a video URL', 'warn');
            urlInput.focus();
            return;
        }

        const urls = parseUrls(raw);
        const invalid = urls.filter((u) => !isValidUrl(u));
        if (invalid.length > 0) {
            markInputError(true);
            toast('One or more URLs are invalid. Use full http:// or https:// links.', 'warn');
            return;
        }

        if (threads < 1 || threads > 16) {
            toast('Threads must be between 1 and 16', 'warn');
            return;
        }

        setButtonLoading(true);

        try {
            let started = 0;

            if (urls.length === 1) {
                const res = await fetch('/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: urls[0], format, threads }),
                });
                const data = await res.json();

                if (!res.ok || data.status === 'error') {
                    toast(data.message || 'Failed to start download.', 'error');
                } else if (data.task_id) {
                    addJob(data.task_id, urls[0], format);
                    started = 1;
                }
            } else {
                const res = await fetch('/download/batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ urls, format, threads }),
                });
                const data = await res.json();

                if (data.tasks) {
                    for (const task of data.tasks) {
                        addJob(task.task_id, task.url, format);
                        started += 1;
                    }
                }
                if (data.errors?.length) {
                    for (const err of data.errors) {
                        toast(`${truncate(err.url, 30)}: ${err.message}`, 'error');
                    }
                }
                if (started === 0) {
                    toast(data.message || 'Could not start downloads.', 'error');
                }
            }

            if (started > 0) {
                urlInput.value = '';
                markInputError(false);
                toast(started === 1 ? 'Download started' : `${started} downloads started`, 'success');
            }
        } catch {
            toast('Network error — the server may be unreachable.', 'error');
        } finally {
            setButtonLoading(false);
            urlInput.focus();
        }
    });

    renderHistory();
    recoverActiveJobs();
});
