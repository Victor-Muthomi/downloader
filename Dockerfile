FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/downloads \
    && chown -R appuser:appuser /app

USER appuser

ENV HOST=0.0.0.0
ENV PORT=5000
ENV FLASK_DEBUG=0
ENV DOWNLOADS_DIR=/app/downloads

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health')" || exit 1

CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "app:app", "--timeout", "300"]
