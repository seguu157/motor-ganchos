FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RADAR_DB=/data/radar.sqlite \
    RADAR_HOST=0.0.0.0 \
    RADAR_PORT=8000 \
    HF_HOME=/data/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md requirements.txt requirements-whisper.txt ./
COPY radar ./radar

# El modelo de Whisper pesa varios cientos de MB y se descarga en el primer uso.
# En un VPS justo de memoria conviene construirlo aparte:
#   docker compose build --build-arg INSTALL_WHISPER=1
ARG INSTALL_WHISPER=0
RUN pip install --no-cache-dir . \
    && if [ "$INSTALL_WHISPER" = "1" ]; then pip install --no-cache-dir -r requirements-whisper.txt; fi \
    && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud')"

CMD ["radar", "serve", "--host", "0.0.0.0", "--port", "8000"]
