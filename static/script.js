/**
 * NexDown — Client-side logic
 * Handles form submission, polling, file saving, and toast notifications.
 */
document.addEventListener('DOMContentLoaded', () => {
    // ── DOM refs ──────────────────────────────────────────────────────
    const form = document.getElementById('downloadForm');
    const urlInput = document.getElementById('urlInput');
    const pasteBtn = document.getElementById('pasteBtn');
    const formatSelect = document.getElementById('formatSelect');
    const downloadBtn = document.getElementById('downloadBtn');
    const btnIcon = downloadBtn.querySelector('.btn-icon');
    const btnText = downloadBtn.querySelector('span');
    const spinner = downloadBtn.querySelector('.spinner');
    const progressSection = document.getElementById('progressSection');
    const progressBar = document.getElementById('progressBar');
    const progressMessage = document.getElementById('progressMessage');
    const progressPct = document.getElementById('progressPercentage');
    const resultArea = document.getElementById('resultArea');
    const toastContainer = document.getElementById('toastContainer');

    // ── Validation ────────────────────────────────────────────────────
    const YT_RE = /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch\?.*v=|shorts\/|embed\/|live\/)|youtu\.be\/)[A-Za-z0-9_\-]{11}/;

    function isValidUrl(url) {
        return YT_RE.test(url.trim());
    }

    function markInputError(show) {
        urlInput.classList.toggle('input-error', show);
    }

    urlInput.addEventListener('input', () => markInputError(false));

    // ── Paste button ──────────────────────────────────────────────────
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

    // ── Toast notifications ───────────────────────────────────────────
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

    // ── UI state helpers ──────────────────────────────────────────────
    function resetUI() {
        progressSection.style.display = 'none';
        resultArea.style.display = 'none';
        progressBar.style.width = '0%';
        progressBar.classList.remove('active');
        progressPct.textContent = '0%';
        btnIcon.style.display = '';
        btnText.style.display = '';
        spinner.style.display = 'none';
        downloadBtn.disabled = false;
        markInputError(false);
    }

    function setLoading() {
        resultArea.style.display = 'none';
        downloadBtn.disabled = true;
        btnIcon.style.display = 'none';
        btnText.style.display = 'none';
        spinner.style.display = 'block';
    }

    // ── Show result (success or error) ────────────────────────────────
    function showResult(message, filename, isError = false) {
        resultArea.style.display = 'flex';
        resultArea.className = `result-area ${isError ? 'error' : 'success'}`;

        if (isError) {
            resultArea.innerHTML = `
                <div class="result-header">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                    <span>${escapeHtml(message)}</span>
                </div>`;
        } else {
            resultArea.innerHTML = `
                <div class="result-header">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                    <span>${escapeHtml(message)}</span>
                </div>
                ${filename ? `<a class="save-btn" href="/file/${encodeURIComponent(filename)}" download>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Save to device
                </a>` : ''}`;
        }
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── Polling ───────────────────────────────────────────────────────
    let pollTimer = null;

    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    function startPolling(taskId) {
        let failures = 0;
        const MAX_FAILURES = 5;

        pollTimer = setInterval(async () => {
            try {
                const res = await fetch(`/status/${taskId}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                failures = 0; // reset on success

                if (data.status === 'processing') {
                    progressSection.style.display = 'block';
                    progressBar.classList.add('active');
                    const pct = Math.round(data.progress);
                    progressBar.style.width = `${pct}%`;
                    progressPct.textContent = `${pct}%`;
                    progressMessage.textContent = data.message || 'Processing…';

                } else if (data.status === 'success') {
                    stopPolling();
                    progressBar.style.width = '100%';
                    progressPct.textContent = '100%';
                    progressBar.classList.remove('active');
                    progressMessage.textContent = 'Complete!';

                    setTimeout(() => {
                        resetUI();
                        const title = data.title || data.filename;
                        showResult(`${title}`, data.filename);
                        toast('Video ready — click "Save to device"', 'success');
                    }, 800);

                } else if (data.status === 'error') {
                    stopPolling();
                    resetUI();
                    showResult(data.message || 'Download failed.', null, true);
                    toast(data.message || 'Download failed', 'error');
                }
            } catch (err) {
                failures++;
                console.error('Polling error', err);
                if (failures >= MAX_FAILURES) {
                    stopPolling();
                    resetUI();
                    showResult('Lost connection to server. Please try again.', null, true);
                    toast('Connection lost', 'error');
                }
            }
        }, 1200);
    }

    // ── Form submission ───────────────────────────────────────────────
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = urlInput.value.trim();
        const format = formatSelect.value;
        const threads = parseInt(threadInput.value) || 4;

        // Client-side validation
        if (!url) {
            markInputError(true);
            toast('Please enter a YouTube URL', 'warn');
            urlInput.focus();
            return;
        }
        if (!isValidUrl(url)) {
            markInputError(true);
            showResult('Invalid YouTube URL. Please use a youtube.com or youtu.be link.', null, true);
            return;
        }
        if (threads < 1 || threads > 16) {
            toast('Threads must be between 1 and 16', 'warn');
            return;
        }

        setLoading();
        progressSection.style.display = 'block';
        progressBar.classList.add('active');
        progressBar.style.width = '2%';
        progressMessage.textContent = 'Connecting…';
        progressPct.textContent = '0%';

        try {
            const res = await fetch('/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, format, threads }),
            });

            const data = await res.json();

            if (!res.ok || data.status === 'error') {
                resetUI();
                showResult(data.message || 'Failed to start download.', null, true);
                toast(data.message || 'Request rejected', 'error');
                return;
            }

            if (data.task_id) {
                startPolling(data.task_id);
            }
        } catch (err) {
            resetUI();
            showResult('Network error — the server may be unreachable.', null, true);
            toast('Network error', 'error');
        }
    });
});
